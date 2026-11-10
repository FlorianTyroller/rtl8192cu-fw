---
title: "The wedge investigation"
redirect_from:
  - /analysis/wedge.md
---

# The wedge investigation

The RTL8188CU/8192CU family has a long-standing field problem: the
stick silently stops passing traffic and only a driver reload (firmware
re-download) recovers it. This document records the investigation:
the capture setup, the captured events, the emulator evidence, and the
standing discriminators. Factual firmware documentation lives in
[firmware.md](firmware.md).

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
