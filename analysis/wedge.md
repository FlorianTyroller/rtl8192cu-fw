---
title: "The wedge investigation"
redirect_from:
  - /analysis/wedge.md
---

# The wedge investigation

> **The short version** — after 14 years of unexplained "stick stops
> working until reload" reports on RTL8188CU/8192CU hardware, this
> investigation caught, interrogated, and explained one live:
> a skipped power-state transition leaves a chip power domain
> isolated; its **RX calibration registers float to 0xFF**; the stick
> demodulates garbage while its 8051 keeps running happily. Detection:
> read `0x0DA2:8` over USB — the wedge reads all-0xFF (healthy sticks
> hold values in the low 4 bytes). Fix: reload the driver, or better,
> detect early and reload automatically. Full chain below.

The RTL8188CU/8192CU family has a long-standing field problem: the
stick silently stops passing traffic and only a driver reload (firmware
re-download) recovers it. This document records the investigation:
the capture setup, the captured events, the emulator evidence, and the
standing discriminators. Factual firmware documentation lives in
[firmware.md](firmware.md).

## First catch: the monitor's maiden capture (Nov 10, 18:02)

Deployed 15:40, first detection 18:02 — 2 h 22 min later. Full
sequence, all on tape (evidence in the capture archive):

1. **T−34 s**: the driver writes the MAC's CAM/BSSID table — an
   index loop over 0x0670/0x0674 pairs, the data containing the
   primary AP's BSSID, plus CR (0x0100) := 0xff02 and 0x0680 := 0xcf.
   The last driver action before the wipe; RSSI polling continues
   normally after it (fw H2C alive).
2. **T−20 s … T0**: the calibration register 0x0DA6 reads all-FF
   (wiped between two 20 s polls). A *different* domain's register
   (0x0FB8, a live counter — values incrementing every poll) stays
   alive for at least another minute: **the wipe is progressive
   across power domains, not a single event**.
3. **18:02:26**: the watchdog's own minute-cycle signature check
   fires (its `0x0DA2:8` view went all-FF) → driver reload. The
   "Disabling MAC timed out" on unload is now fully explained: the
   firmware cannot complete the power-down handshake on a chip whose
   domains are already dead.
4. **18:02:49**: calmond's poll finds its fd stale (the watchdog's
   reload re-enumerated the device), captures evidence (its
   register dump hit the stale fd — fixed in v1.1 to re-find the
   device and skip double recovery), reloads again, recovered
   18:03:23. Detection-to-recovery: **34 s**.

Net: the causal chain's trigger correlation is now on tape — a
CAM/BSSID re-programming burst, then domain wipes, with the fw's
command path alive throughout. The exact domain-loss mechanism
(why a register write burst precedes domain state loss) remains the
one open link; the trap is reset for the next event.

## Detection and mitigation (the fix)

The wedge signature is readable from userspace over USB with no driver
support (`tools/regread.py` in this repo; works on Linux 6.18's
changed usbfs ABI):

    sudo python3 regread.py 0DA2:8

- Healthy: the last 4 bytes hold live calibration values
  (e.g. `FF FF FF FF 8A 39 D5 31` — the first 4 may read 0xFF healthy).
- Wedged: **all 8 bytes 0xFF** — the calibration domain has lost
  state. RX is dead or dying; the 8051 and H2C still answer.

Recovery is an immediate driver reload (`modprobe -r rtl8xxxu;
modprobe rtl8xxxu` — firmware re-download re-runs calibration; the
stick recovers in under 10 s).

**Automated prevention**: the capture Pi's watchdog now checks the
signature every minute *before* the gateway ping — on all-FF it
reloads the driver and logs `CALIBRATION WIPE DETECTED`. The zombie
phase lasts days before total RX death, so early detection turns a
multi-day silent degradation into a self-healing 30-second event.
Note for implementers: after a reload, wait ~2 min before trusting the
signature again (calibration repopulates during init).

**Catch-it-live monitor**: `tools/wifi-calmond.py` (deployed on the
capture Pi as `wifi-calmond.service`) polls the signature every 20 s.
On first detection it captures evidence *before* recovering — a full
register dump and a copy of the live usbmon logs (so the wipe-moment
driver↔firmware conversation cannot rotate away) — then reloads. The
next wipe will be caught in the act; correlating its onset with the
captured USB traffic is expected to close the one inferred link in the
causal chain without any vendor input.

## Test setup

- **The stick**: Edimax-class RTL8188CUS USB adapter (MAC
  80:1f:02:00:00:99 — pseudonymized, device-specific part withheld),
  driven by rtl8xxxu with firmware
  `rtl8192cufw_TMSC.bin` 88.2 — the exact image this repo documents.
- **The capture Pi**: a Raspberry Pi 1 Model B serving as the field
  box. It runs the stick as its only network link, a watchdog service
  (gateway ping every ~90 s; 3 consecutive failures → driver reload,
  10 → reboot), and a usbmon capture service writing all USB traffic
  to `/var/log/usbmon-wifi.log` (rotated; ~15 min granularity after
  the rotation fix, roughly 2 h of history).
- **Radio environment**: one primary AP (an ISP router,
  the gateway, with the Pi 10 cm beside it) and two same-SSID mesh
  repeaters. BSSIDs used below are pseudonyms; the real values are
  deliberately not published:
  - **AP-P** `AA:00:00:00:00:01` — primary AP / gateway
  - **AP-R1** `AA:00:00:00:00:02` — repeater 1 (auth-flaky in every
    observed event; one 13-minute backhaul outage)
  - **AP-R2** `AA:00:00:00:00:03` — repeater 2
- **Capture decoding**: usbmon control transfers use bmRequestType
  0x40/0xC0, bRequest 0x05, wValue = MAC register address. The full
  driver↔firmware conversation is therefore readable with the register
  map in firmware.md.

## Event classification

Three events were captured/logged in the rtl8xxxu era. Verdicts after
analysis:

| event | story (router log + kernel log + capture) | verdict |
|-------|-------------------------------------------|---------|
| Nov 4 21:06 | no radio event; coincides with the manual rtl8xxxu switch-over on the Pi | manual driver work; "Disabling MAC timed out" is likely routine unload noise |
| Nov 5 07:06–07:10 | stick left AP-P (no logged AP-side trigger), re-associated **to AP-P itself** 07:09:33 with a fresh DHCP lease; gateway pings failed ~45 s later | **unresolved** — either a stick-side data-path failure or an unlogged AP blip |
| Nov 6 01:09–01:26 | AP-P's beacons verifiably stopped (capture: the 158-byte beacon stream ends at 01:09:27.7, six seconds before the beacon-loss roam); stick roamed to AP-R1; 13 min of working link; AP-R1's backhaul then died; watchdog reload | environment-side: AP blip + repeater backhaul outage |

Forced-roam experiments (router power-cycles) on Nov 6: one single
cycle and a five-restart stress series (four soft restarts + one power
cycle, last two back-to-back). All cycles recovered to AP-P within
~70 s; the watchdog never fired a reload; the H2C mailbox stayed
responsive throughout (247 reads, zero stuck) [confirmed from
captures]. The healthy roam uses the identical H2C burst to the Nov 6
event (JOINBSSRPT 0/1 + RA_MASK|EXT ×2) — **the BSS-transition burst
is benign by itself**.

## Discriminator (how future events are judged)

A firmware-side wedge requires all of:

1. Link dead with **no AP-side explanation** in the logs (check all
   three BSSIDs; the repeaters regularly impersonate wedges).
2. H2C cycle continuing in the capture (0x01CC reads returning 0x00
   while the link is dead).
3. Ideally the 0x01C4/0x01C5 readout: 0x60F2 = stuck in dispatch,
   0x4B87 = stuck in timer-int handler, 0x50C6/0x50C7 = main loop
   alive, 0x60F3 = interrupts completing.

Special rule: **stick associated to AP-P + gateway unreachable =
stick-side until proven otherwise** (no repeater in the path).

## Emulator evidence

`tools/sim8051.py` replays captured H2C sequences against the image
with stubbed ROM calls and W1C register semantics. Results:

- The full H2C model (staging, dispatch, handlers, ack discipline)
  validates dynamically: basic and EXT staging, unknown-id ack, the
  ignored cmd 7, ack-bit placement [confirmed].
- With seeded association flags and a timer-event storm through the
  timer-int vector, the firmware enters **TXPAUSE = 0xFF and cannot
  leave it**: the recovery H2C set (JOINBSSRPT, RA_MASK, the cmd-59
  mode kick) skips its drain/unpause sequence because the mode nibble
  is unchanged. Mailbox fully responsive throughout — the field
  signature of a TX-side wedge [lab-reproduced].
- The firmware reference later identified the matching real-code
  mechanism: the unpause from TX states 4/5 is power-vetoed by the
  SPS0 settle poll (firmware.md, "State engines"). Zero field
  confirmation so far; no captured event matches it.

## The Nov 7-10 event: first live-wedged specimen (under investigation)

Timeline (router log + kernel + NetworkManager + watchdog, all
cross-checked): associated since Nov 6 16:00; kernel and NM silent for
2.5 days; watchdog pings to the gateway succeeded throughout while
external hosts could not reach the Pi (broadcast RX dead, unicast RX/TX
to the gateway alive — "zombie association"); spontaneous recovery via
beacon-loss roam at Nov 9 17:33; brief data-path blips every 2-3 h
after; at Nov 10 06:53 the radio died for good (auth attempts to all
APs unanswered, supplicant blacklisted, endless scanning, zero
associations). An Ethernet cable to the Pi (its Pi-1 RJ45) restored a
control path while leaving the stick wedged — enabling the first
**live register interrogation of a field wedge**, via tools/regread.py
(updated for the Linux 6.18 usbfs struct change: the ctrltransfer
struct gained a trailing data pointer; request number and calling
convention now computed per-arch).

Live wedge fingerprint (Nov 10, ~13:45):

| register | value | meaning |
|----------|-------|---------|
| 0x01C4/5 | 0x50C6/0x50C7, toggling | **8051 main loop alive** (heartbeat) |
| 0x01CC | 0x00 | H2C mailbox clear, idle |
| 0x0130-3 (FWIMR) | 33 00 07 00 | init values, never changed |
| 0x0134-7 (FWISR) | 08 01 00 02 | pending bits only on disabled sources — no stuck dispatch |
| 0x0522 TXPAUSE | 0x00 | **TX not paused** — the emulator's TXPAUSE-stuck mechanism ruled out |
| 0x06A0/2/4 RXFLTMAP | FFFF/0000/FFFF | management+data filters **open** |
| 0x0608 RCR | 700060CE | plausible init value |
| 0x0284 RXPKT_NUM | ~0 | no RX backlog |
| 0x0136 FWISR2 | 0x00 | **no beacon-DMA interrupts** — 3 APs beaconing, MAC accepts nothing |

Verdict so far: **every firmware-side suspect is eliminated** — the
8051 brain is healthy, TX unpaused, filters open, USB control alive —
while the **receive path is dead between PHY and MAC** (zero BSS
visible in scans, no beacon processing, yet bulk-in traffic flows with
unusable frames). Root-cause hunt continues with a baseband-register
diff against the driver's init tables. The zombie phase now reads as
the same failure progressing: broadcast RX died first (external
unreachability with working gateway unicast), then unicast, then all
RX.

**Root-cause closure (Nov 10 afternoon):** the driver was reloaded
from the Ethernet path — recovery in under 10 s (auth + assoc + IP on
the first attempt), confirming a firmware-level wedge by instant
recovery. The full readable register space (0x0000–0x0FFF) was dumped
wedged, healthy-associated, and healthy-scanning
(`tools/regread.py --dump`; snapshots in
`analysis/wedge-regdump-20261110.txt` and the capture archive).
Rigorous diff (wedged vs healthy in the same scanning state, 151
differing bytes) isolates a **corrupted register cluster**: all-0xFF
wipes at 0x0DA2–0x0DA9, 0x0F80–0x0F89, 0x0F90–0x0F98, 0x0FA2–0x0FA5,
0x0FB8–0x0FBB, plus 0x0403/0x040B/0x040F stuck at 0xFF — registers
unnamed in both kernel drivers (vendor-reserved; the IQK block at
0x0E28+ shows only normal per-run calibration variance). The wiped
cluster sits in the calibration/state storage region: with invalid RX
calibration the demodulator produces the observed stuck-length garbage
frame flood, zero BSS, zero beacon interrupts — every symptom
explained. H2C mailbox remnants date the death: the last processed
command was the JOINBSSRPT(opmode=0) of the 06:52:56 roam attempt,
with EXT staging still holding the final RA_MASK high bytes (00 F0).
The progressive arc (broadcast RX first → unicast → all RX) matches a
drifting-then-wiped calibration. Next refinement, when wanted: name
the 0x0Dxx/0x0Fxx cluster via the RTL8192C datasheet or vendor
driver, and diff the recovery capture (driver reload sequence is in
the usbmon window from 15:16).

**The causal chain (final).** Every link evidenced except one strongly
inferred:

1. Roam churn drives the fw's mode/PS state engine through repeated
   transitions (proven: mailbox remnants, blip cadence).
2. A transition can be skipped or interrupted — the mode nibble
   matches so the un-drain path never runs (proven possible: emulator
   repro; the power-veto at 0x6781 is mapped code).
3. The SPS0 power-isolation asserted by the drain paths is then left
   asserted on some domain (inferred — this machinery is the only
   domain manipulator on the chip).
4. The unpowered domain's registers float to 0xFF — the calibration
   cluster (proven: wiped cluster; fw and driver both provably never
   write those addresses).
5. RX demodulates garbage: stuck-length frame flood, zero BSS
   (proven).
6. The 8051 never notices — its RAM state says "calibrated", the
   heartbeat runs, H2C answers (proven).
7. Only a firmware re-download re-runs init and restores calibration
   (proven: <10 s recovery on reload).

The remaining unknown: which power domain, and whether the ROM or
silicon performs the wipe during the interrupted transition (the boot
ROM is unreadable). A RTL8192C datasheet would name the cluster.

Operational notes: the watchdog script deliberately does nothing while
eth0 has carrier ("wired link up, then WiFi isn't the problem") — with
the cable in, no automatic unwedge will occur; recovery when wanted =
driver reload from the Ethernet path. The stick stays wedged by choice
(preserved specimen); capture and heartbeat monitoring continue.

**Provocation experiment (Nov 11, 23:00–00:00): clean negative.**
40 connect/disconnect cycles (one CAM/BSSID re-programming each, the
operation seen 34 s before the first wipe) — 40/40 associations
succeeded, zero wipe detections, monitors idle throughout. The
CAM-write burst is therefore substantially decoupled from causation:
it was most plausibly the last ordinary driver activity before an
independently timed failure. The surviving hypothesis is firmware-
autonomous timing — the PS/state-engine transitions on the ~2–3 h blip
cadence observed on Nov 9 — which host-side activity neither triggers
nor prevents. Passive capture with the v1.2 kit remains the path; each
natural incident now yields a full pre-reload register snapshot.

**Nov 11–12: recurring-wipe mode + two detector blind spots found
and fixed.** Starting 15 minutes after the 40-cycle provocation ended
(Nov 11 00:15) and running ~31 h at a remarkably regular **~85-minute
period**, the stick entered a recurring-wipe mode: 21 signature fires,
each caught by the watchdog and recovered by reload. At ~07:27 Nov 12
the wipes stopped — leaving a *zombie* state: RX dead (0 BSS on scan)
with the calibration signature still valid and the 8051 heartbeating.
**The signature marks the end stage, not the onset** — RX dies before
0x0DA6 wipes. Two monitoring gaps surfaced and were fixed: (1) calmond
kept a stale USB fd after each reload and read 0xEA (= "healthy") —
now reopens the device every poll; (2) the watchdog's gateway ping
succeeded over the Ethernet lifeline, masking WiFi death (12 h outage)
— now pins the ping to wlan0 when eth0 carries. Open questions: what
triggered the recurring mode (the provocation's proximity is
suspicious), and why it stopped. A 120-cycle provocation remains the
planned next stress test.

## Firmware verdict so far

Every captured event is explained by the environment. The firmware
survived every on-demand stress (five forced roams, back-to-back). One
lab-reproduced failure mode exists (unpausable TX pause) with a real
code path behind it, and one field event (Nov 5 07:09) remains open.
Passive collection continues with the discriminator above.

## Open items

- Nov 5 07:09 event: unresolved (stick-side vs AP-side).
- MAC-disable timeout during driver unload: confirm it occurs on every
  clean unload (would fully devalue it as wedge evidence).
- RSSI H2C cycle stopped 2 min before the AP-R1 backhaul death on
  Nov 6, while the link still worked — unexplained driver behavior.
- Optionally: a driver-side workaround prototype (force the firmware
  mode-switch to re-run after every roam), only if a firmware-side
  wedge is ever confirmed.
