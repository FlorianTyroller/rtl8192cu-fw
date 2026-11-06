---
title: "Firmware reference"
redirect_from:
  - /analysis/firmware.md
---

# RTL8188CUS / RTL8192CU firmware — reference

Factual documentation of the 8051 MAC firmware `rtl8192cufw_TMSC.bin`
(revision 88.2, built 2012-12-05). The companion document
[wedge.md](wedge.md) covers the field investigation; [ERRATA.md](ERRATA.md)
records corrected claims. Symbol database: [annotations.tsv](annotations.tsv).
Function census: [functions.tsv](functions.tsv).

Confidence tags used throughout: **[confirmed]** = verified against bytes
and at least one independent source (emulator, kernel driver, or trace);
**[likely]** = strong evidence, one unverified link; **[guess]** = shape only.

## Container format

32-byte header, payload at 0x20. Validated against
`struct rtl8xxxu_firmware_header` in the Linux rtl8xxxu driver.

| offset | size | field | TMSC value |
|--------|------|-------|------------|
| 0x00 | u16 | signature | 0x88C1 (A-cut; B-cut = 0x88C2) |
| 0x02 | u8 | category | 0x02 |
| 0x03 | u8 | function | 0x05 |
| 0x04 | u16 | major version | 88 |
| 0x06 | u8 | minor version | 2 |
| 0x08 | 4×u8 | build time (BCD) | Dec 5, 17:12 |
| 0x0c | u16 | ramcodesize | 16094 = filesize − 32 |
| 0x10 | u32 | svn_idx | 137364 |
| 0x14–0x1f | | reserved | 0 |

Variants `rtl8192cufw_A.bin` (17:11) and `_B.bin` (17:10) come from the
same build run. `rtl8192cufw.bin` is a different lineage (v63.0,
Dec 9). rtl8xxxu selects TMSC vs UMC build from SYS_CFG; rtlwifi
selects A/B by chip cut.

Host download: REG_MCU_FW_DL (0x0080) enable, page index in bits [2:0]
of 0x0082, 4 KB pages to 0x1000, then checksum poll, DL_READY, and the
driver's 8051 reset (see "Reset handshake").

## Memory map

- CODE 0x0000–0x3FFF — boot ROM. Not readable. The firmware calls into
  it; it calls into the firmware (see "Interrupt entry").
- CODE 0x4000–0x7FFF — the 16 KB image.
- XDATA 0x0000–0x0FFF — MAC registers (same indices as the USB register
  space).
- XDATA 0x4000–0x7FFF area — not used as code; packet/descriptor buffer
  space at 0xFC00–0xFD10.
- XDATA 0x8000+ — firmware RAM (globals, per-station tables).

Load base 0x4000 is validated: 97% of absolute xref targets fall inside
the image on correct instruction boundaries (neighbors: 90–94%).

## Code layout

- 0x4000–0x004D — vector slots: 0x4000→0x4630 (reset), 0x4013→0x60F2
  (FWISR dispatcher), 0x4043→0x6891 (hw-event ack), 0x404B→0x4B87
  (timer-interrupt handler).
- 0x4050–0x41D5 — rate-adaptation data tables (23 fallback chains,
  selector ladders, thresholds, limits, weights). Read via
  register-computed MOVC, so static descent never reaches them.
- 0x4220–0x4E89 — Keil C51 runtime library and walkers
  (address-identical across builds).
- 0x466E–0x4B22 — startup, init-table interpreter.
- 0x50C6 — `main`.
- 0x740F–0x7C2B — H2C handlers.

`main` [confirmed]: strap polls → RA table init (0x7255) → interrupt
mask init (0x680A: FWIMR = {0x33,0x00,0x07,0x00}; FTIMR = {0x1F,0x11})
→ FWISR clear → 0x0080 |= 0x40, IE = 0x85 → loop. The loop writes a
heartbeat (0x50C6/0x50C7 alternating) to 0x01C4 and polls work flags
(IDATA 0x55): bit 4 → H2C service, bit 6 → rate-adaptation worker.

## Host-to-chip (H2C) protocol

Driver side (rtlwifi rtl8192c): four 4-byte mailboxes REG_HMEBOX_0..3
at 0x01D0/0x01D4/0x01D8/0x01DC, one per box; pending bits in REG_HMETFR
(0x01CC). Hardware sets bit N when the host writes box N; the firmware
clears it (write-one-to-clear) after consuming the box. The host never
writes 0x01CC [confirmed against captures].

Firmware side, per command [confirmed, emulator-replayed]:

1. Service routine 0x5559 (interrupts disabled for its full run):
   reads 0x01CC & 0x0F; selects the box the state ring 0x8B51 points
   at; stages box byte 0 to 0x8AD0, copies it to 0x8AD1 via 0x5EE4.
2. Extended mode (id bit 7, H2C_EXT): stages
   `param[] = {EXT[0x0088+2s], EXT[0x0089+2s], box1, box2, box3}` to
   0x8AD2–0x8AD6. Basic mode: `param[] = {box1, box2, box3}`.
   The EXT layout (two bytes per box) matches rtlwifi's
   REG_HMEBOX_EXT_0..3 = 0x0088/0x008A/0x008C/0x008E.
3. Dispatch 0x54A6 stores the constant pointer {1, 0x8A, 0xD2}
   (&param[0]) at 0x8AD7 and calls Keil switch walker 0x4394 with the
   table at 0x54B0 (inline after the LCALL — the walker pops its return
   address as the table pointer).
4. Each case is a trampoline `MOV DPTR,#0x8AD7; LCALL 0x436B
   (args→R3/R2/R1); LJMP <handler>` — a Keil generic pointer to the
   staging block. ROM 0x29D9 reads param[0]; lib 0x4220 reads param[N].

Handlers receive a generic pointer; "register accesses" inside handlers
are param-block reads unless the MOVX is direct.

| id | name (rtlwifi) | handler | semantics |
|----|---------------|---------|-----------|
| 0 | AP_OFFLOAD | — | default path (ack-and-ignore) |
| 1 | SETPWRMODE | 0x748B | ROM 0x2A8B + reg writes; programs REG_DRIVER_EARLY_INT |
| 2 | JOINBSSRPT | 0x7485 | param[0] → opmode (IDATA 0x21) |
| 3 | RSVDPAGE | 0x7552 | page locations = TDECTRL(0x0209) base + param[0..4] → 0x8AF7–0x8AFB |
| 5 | RSSI_REPORT | 0x740F | per-macid value → RA mode table 0x8923+n |
| 6 | RA_MASK | 0x56A6 | rate mask; see "Rate adaptation" |
| 7 | MACID_PS_MODE | 0x5558 | bare RET — ignored |
| 8 | P2P_PS_OFFLOAD | 0x759A | NoA/absence gating of the TX state engine |
| 9 | MAC_MODE_SEL | 0x541E | GPIO mode-switch callback registration |
| 15 | PWRM | — | absent → default |
| 24 | CTW | — | absent → default |
| 32 | (Windows-only) | 0x7881 | beacon early-RX offset → 0x8B38; consumed at 0x6547 as `value + REG_DRIVER_EARLY_INT − 2` |
| 44 | (Windows-only) | 0x7AC2 | TX-buffer offload op; bounded poll; ack bit 4 |
| 45 | (Windows-only) | 0x74B8 | PS profile config (1–3 enter PS, 4 resume) via 0x47FA |
| 46 | (Windows-only) | 0x7500 | beacon early-int config (REG_DRIVER_EARLY_INT) |
| 59 | (Windows-only) | 0x7539 | rate mirror + mode-switch kick |
| 75 | (Windows-only) | 0x7C2B | arms a TSF reset (consumed by timer-int case 0x013D.4) |
| — | default | 0x5551 | ack-and-ignore (bit 0 of 0x01C6) |

RA_MASK layout (extended mode) [confirmed]: param[0..3] = 32-bit rate
mask with the mode class in bits 28–31; param[4] = macid (bits 0–4) +
flags. Mode classes initialize the allowed-mask: B/G = 0x0FF5, A =
0x0FF0, B = 0x000D (numerically confirmed against the code tables);
classes 0–3 are HT variants; 0x0C–0x0F copy ARFR0..3.

Protocol properties [confirmed]: the service is ring-strict — it
services only the ring's box and never scans; an out-of-ring box write
starves until the ring reaches it. Drivers therefore write boxes in
ring order.

## Chip-to-host (C2H) channel

- Event registers 0x01A0–0x01A2; demux 0x5F33 copies header + payload
  to staged pointers, then REG_C2HEVT_CLEAR (0x01AF) = 0xFF and
  0x01CB bit 7 toggles (consumed) [confirmed].
- Ready events to REG_C2HEVT_MSG_TEST (0x01B8): 4 = leave-LPS ready,
  8 = leave-awake-mode ready [confirmed].
- Ack register 0x01C6 bits: 0 unknown-command, 1 processed, 3
  opmode-change done, 4 offload completion / poll timeout, 6
  TX-queue-drain failed [confirmed].

## Reset handshake (8051 clock-off)

Both drivers use the identical sequence [confirmed kernel + firmware]:

1. Driver: FWIMR = 0x20 (unmask only FWISR0.b5), then 0x01CF = 0x20.
2. Firmware: FWISR0.b5 handler 0x62CD double-checks 0x01CF bit 5,
   quiesces (FWIMR/FTIMR/timer regs cleared via 0x519D), clears the
   CPU-enable bit in the SYS_FUNC registers, parks at 0x62F1 (`SJMP $`).
3. Driver: polls SYS_FUNC CPU_ENABLE cleared (5 s), forces if needed.

## Interrupt architecture

Dispatcher 0x60F2 (vector 0x4013): full-context save, announces its
address to 0x01C4, reads FWISR 0x0134–0x0137 masked by IDATA 0x28–0x2B,
dispatches by bit, W1C. Seven sources active at init [confirmed]:

| source | name | handler behavior |
|--------|------|------------------|
| FWISR0.b0 | TX report ready | pops 8 status bytes from SFR latches 0xD1–0xD7/0xD9, accumulates per-macid statistics |
| FWISR0.b1 | C2H/event ready | copies latches to IDATA 0x56–0x5D, calls 0x5FA4 |
| FWISR0.b4 | H2C mailbox arrival | sets main-loop work flag |
| FWISR0.b5 | clock-off request | fatal path → 0x62F1 park |
| FWISR2.b0 | beacon DMA | early-int re-arm, TSF handling, TX state engine drive |
| FWISR2.b1 | beacon loss / watchdog | beacon-DMA watchdog (0x6084) + deferred RA tick |
| FWISR2.b2 | power-save/timer | PS-RX sequencing |

Timer-interrupt block 0x013C–0x013F (handler 0x4B87, vector 0x404B,
masks IDATA 0x30–0x33): beacon-miss accounting (0x4860), PS-RX window
stages, GPIO/LED worker, PS-mode request + CPWM ack (0x012F), TSF reset
(cmd-75-armed), wake/unpause, and the beacon-loss processor 0x7E2B.

Hw-event handler 0x6891 (vector 0x4043): acknowledges/masks eight
sources via registers 0x0051/0x0052 (status) and 0x0055/0x0056.

## State engines

**Mode nibble** (IDATA 0x22, low 4 bits) — controller 0x45A2 [confirmed]:
4 = LPS armed, 2 = quiesced (TXPAUSE 0xFF), 0x0C = awake/resumed,
1 = plain awake. Transition helpers 0x4AB2/0x4ACC/0x4A7C/0x4A32.

**TX power/queue state** ([0x8B2D]) — engine 0x6582 [confirmed]:
0 idle, 1 active, 2/5 quiesced (TXPAUSE 0xFF), 3 resumed-after-miss,
4 deep-quiesce, 6 partial pause (TXPAUSE 0x6F). Twelve transition
primitives (0x6753–0x67E9). Triggers: beacon-loss policy (0x4E56),
timer-int, beacon-DMA, and P2P/NoA gating. The unpause from states 4/5
runs through 0x6781 and is **vetoed** if the SPS0 power-settle poll
(0x7D42) fails — TXPAUSE then stays 0xFF. This is a legitimate
firmware path with the exact shape of the unpausable state reproduced
in the emulator (see wedge.md).

**Power-save engine** [confirmed]: PS profiles (cmd 45), beacon
countdown pair (0x8B0B/0x8B19), miss counting (0x8B0C vs threshold
0x8B10, latch 0x8B0D) with early-int timeout growing 3 TU per miss,
PS timer family (XDATA 0x50–0x5F, arm regs 0x0153/0x0157/0x015B/0x015F),
PS RX windows (REG_PS_RX_INFO 0x0692), reserved-page frames.

## Rate adaptation

32 per-macid slots. Main-loop worker 0x6BBD [likely]: activity
counters, retry thresholds 25/17/10/3 by rate class, fallback chains
from CODE 0x4050, weighted-average path. TX-report-driven writers:
retry-throttle hysteresis (0x5C66), target-rate set (0x5CC3), step-down
(0x5D41). Rate metric = 1.5 × CODE[0x4166] (0.5-Mbps units for OFDM).
Only REG_INIDATA_RATE_SEL (0x0484+macid) is programmed; 0x0480 is
never written in this build [confirmed].

## Boot-ROM interface

17 call targets, 96 call sites. Queue-op ABI [likely]:
0x27DE query, 0x2A7F store, 0x4353 load, 0x2FD9 execute, 0x2A8B
execute-immediate, 0x3481 commit. Named with confidence tags in
annotations.tsv. ROM internals are statically unreadable; the calling
conventions are documented at the call sites.

## Diagnostics (0x01C4/0x01C5)

Host-readable "last firmware entry" indicator, little-endian:
0x50C6/0x50C7 = main loop alive (heartbeat), 0x60F2 = inside FWISR
dispatch, 0x60F3 = dispatch completed, 0x4B87/0x4B88 = timer-int
handler enter/exit, 0x6891 = hw-event handler, 0x5FC5 = TX drain check.

## Tooling

- `tools/dis8051.py` — recursive-descent disassembler (linear sweep via
  `--linear`); parses Keil switch tables inline after LCALL to the
  walkers (0x4394/0x43BA); function census; per-variant seed lists in
  the canonical commands below.
- `tools/sim8051.py` — 8051 emulator: full core, W1C register
  semantics, H2C/FWISR script replay (`tools/sim-scripts/`).
- `tools/fwl.py` — container/variant analyzer.

Canonical descent run (seeds = stranded entries found by gap audit):

    python3 -I tools/dis8051.py blobs/tmsc.img --base 0x4000 \
      --census analysis/functions.tsv \
      --seed 0x43BA --seed 0x4503 --seed 0x4506 --seed 0x4860 \
      --seed 0x4AE4 --seed 0x4B87 --seed 0x4FE0 --seed 0x4FEF \
      --seed 0x4FFC --seed 0x51C9 --seed 0x67EA --seed 0x6891 \
      --seed 0x7D2C --seed 0x7D39 --seed 0x7E2B

Known tool limits: AJMP/ACALL targets are page-sensitive; `JMP @A+DPTR`
outside the walker idiom stops flow (none occur in reached code);
descent gaps are data only after a byte check (see ERRATA).
