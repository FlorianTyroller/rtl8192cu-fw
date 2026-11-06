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
  80:1f:02:00:00:99), driven by rtl8xxxu with firmware
  `rtl8192cufw_TMSC.bin` 88.2 — the exact image this repo documents.
- **The capture Pi**: a Raspberry Pi 1 Model B serving as the field
  box. It runs the stick as its only network link, a watchdog service
  (gateway ping every ~90 s; 3 consecutive failures → driver reload,
  10 → reboot), and a usbmon capture service writing all USB traffic
  to `/var/log/usbmon-wifi.log` (rotated; ~15 min granularity after
  the rotation fix, roughly 2 h of history).
- **Radio environment**: one primary AP (an ISP router,
  the gateway, with the Pi 10 cm beside it) and two same-SSID mesh
  repeaters. BSSIDs used below:
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
