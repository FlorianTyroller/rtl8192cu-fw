# Findings — RTL8188CUS/RTL8192CU firmware (rtl8192cufw_TMSC.bin 88.2)

Status: second dig, 2026-11-05. Workbench was ~/fwre; this repo is the durable
home. Symbols: [annotations.tsv](annotations.tsv). Functions:
[functions.tsv](functions.tsv).

## Container format

Validated against `struct rtl8xxxu_firmware_header` in the kernel's rtl8xxxu
driver (drivers/net/wireless/realtek/rtl8xxxu/rtl8xxxu.h in older kernels;
split into core.c/regs.h later):

| offset | size | field             | TMSC value                       |
|--------|------|-------------------|----------------------------------|
| 0x00   | u16  | signature         | 0x88C1 (MP A-cut; B-cut = 0x88C2)|
| 0x02   | u8   | category          | 0x02                             |
| 0x03   | u8   | function          | 0x05                             |
| 0x04   | u16  | major version     | 88                               |
| 0x06   | u8   | minor version     | 2                                |
| 0x08   | 4×u8 | month/date/hour/min (BCD) | Dec 5, 17:12              |
| 0x0c   | u16  | ramcodesize       | 16094 = filesize − 32            |
| 0x10   | u32  | svn_idx           | 137364 (Realtek internal SVN)    |
| 0x14–1f|      | reserved          | 0                                |

Payload starts at 0x20. Variants: `rtl8192cufw_A.bin` built 17:11, `_B.bin`
17:10 (same day, one build run), `rtl8192cufw.bin` is a different lineage
(v63.0, built Dec 9). rtl8xxxu picks TMSC (TSMC fab) vs rtl8192cufw.bin
(UMC) from SYS_CFG; rtlwifi picks A/B by chip cut.

## Download protocol (host side, from kernel drivers)

- MCU fw download enable via REG_MCU_FW_DL = 0x0080, page index in bits [2:0]
  of 0x0082, page data written in 4 KB blocks to REG_FW_START_ADDRESS = 0x1000.
- Then checksum poll, MCU_FW_DL_READY, and `reset_8051()` — the kernel
  driver's own words; the architecture is 8051.

## Memory map (empirically validated)

8051, Harvard architecture. Load base 0x4000 gives 97% of absolute xref
targets inside the image on correct instruction boundaries (neighbors
0x2000/0x3000/0x5000: 90–94%).

- CODE 0x0000–0x3FFF  boot ROM — not readable; fw calls into it
      (observed targets 0x29D9, 0x2A7F, 0x2FD9, 0x29F2, 0x2A8B, 0x27DE)
- CODE 0x4000–0x7FFF  16 KB fw RAM, the loaded image
- XDATA 0x0000–0x0FFF MAC registers (same indices as USB register space:
      REG_MCU_FW_DL 0x0080, H2C mailbox REG_HMTFR 0x01CC, payload 0x01D0)
- XDATA 0x8Axx–0x8Bxx fw global variables (0x8ACF cmd id, 0x8AD0 cmd
      payload, 0x8B51 state machine state)

## Code map

- 0x4000  vector slots: LJMP 0x4630 (reset), LJMP 0x60F2 at +0x13 (interrupt)
- 0x4503, 0x4506  `LJMP $` halt slots (library error traps)
- 0x4630–0x4670  Keil C51 startup: init-table interpreter; bit-mask table
      01 02 04 08 10 20 40 80 inline at 0x4666 (read PC-relative via MOVC
      A,@A+PC — desyncs naive linear sweeps)
- 0x466E  `MOV DPTR,#0x4B23` — pointer to the C init records
- 0x4B23  init records (17 B); terminator hands off to main
- 0x50C6  main()
- 0x42xx–0x45xx  runtime library; 0x435F = multi-byte register copy helper,
      hottest function in the image (38 call sites); 0x436B/0x438B =
      3-byte arg load/store R3/R2/R1 ↔ [DPTR]
- 0x4394 / 0x43BA  Keil C51 switch walkers (8-bit / 16-bit case): pop their
      return address into DPTR — **the switch table is always inline right
      after the LCALL**; see H2C section
- 0x5559, 0x568A  H2C service / ack tail; dispatch at 0x54A6 (below)
- 0x60F2  interrupt entry (vector 0x4013)
- 0x4B87, 0x688A/0x6891  interrupt-style handlers with **zero in-image
      callers** — entered from the boot ROM (see "Recursive descent")
- main's large callees 0x6844, 0x7255, 0x7842 — unexplored

## H2C command protocol (host ↔ firmware), fully decoded — corrected 2026-11-05 (2)

> **Correction.** The first pass hand-parsed the dispatch table from a
> desynced linear sweep and paired every id with the wrong handler (shifted
> by one trampoline hop). Recursive descent re-parsed the table bytes
> mechanically; the pairing below is verified against the actual table at
> 0x54B0. Affected first-pass claims re-paired below.

Driver side (rtlwifi rtl8192c/fw_common.c + reg values): the host has four
4-byte mailboxes — REG_HMEBOX_0..3 at XDATA 0x01D0/0x01D4/0x01D8/0x01DC
(command id byte + 3 arg bytes, extended args in HMEBOX_EXT at 0x0088+ for
5–7 byte commands) — and announces "box N written" by setting bit N in
REG_HMTFR = 0x01CC. The host considers a box free again when the
corresponding HMETFR bit is cleared and MCUTST_1 (0x01C0)+N reads 0.

Firmware side (confirmed by recursive descent):

- 0x5559 `h2c_service`: reads 0x01CC & 0x0F → 0x8ACF (pending mask); which
  box is read is gated on the state ring at 0x8B51 (bit-scan of the mask);
  the box regs are copied via 0x435F and staged at 0x8AD0+ (id at 0x8AD1).
- 0x565D: strips H2C_EXT (bit 7) from the id, calls 0x54A6.
- 0x54A6 `h2c_dispatch`: stages 3 arg bytes at 0x8AD7 (via 0x438B),
  R7 = id, then `LCALL 0x4394` — a Keil C51 switch walker that **pops its
  return address into DPTR**, i.e. the switch table sits inline directly
  after the LCALL (0x54B0). Entries `{t_hi, t_lo, case8}`, `00 00`
  terminator followed by the 2-byte default target.
- Each case body is a 9-byte trampoline (block 0x54DE–0x5558):
  `MOV DPTR,#0x8AD7; LCALL 0x436B (args → R3/R2/R1); LJMP <handler>`.
  The handler's RET returns directly past the whole dispatch chain.

  | id | name (rtlwifi)  | trampoline | handler |
  |----|-----------------|------------|---------|
  | 1  | SETPWRMODE      | 0x54E7     | 0x748B  |
  | 2  | JOINBSSRPT      | 0x54DE     | 0x7485  |
  | 3  | RSVDPAGE        | 0x550B     | 0x7552  |
  | 5  | RSSI_REPORT     | 0x5514     | 0x740F  |
  | 6  | RA_MASK         | 0x551D     | 0x56A6  |
  | 7  | MACID_PS_MODE   | 0x5558     | *(bare RET — ignored)* |
  | 8  | P2P_PS_OFFLOAD  | 0x5525     | 0x759A  |
  | 9  | MAC_MODE_SEL    | 0x552E     | 0x541E  |
  | 32 | *(unknown)*     | 0x5536     | 0x7881  |
  | 44 | *(unknown)*     | 0x553F     | 0x7AC2  |
  | 45 | *(unknown)*     | 0x54F0     | 0x74B8  |
  | 46 | *(unknown)*     | 0x54F9     | 0x7500  |
  | 59 | *(unknown)*     | 0x5502     | 0x7539  |
  | 75 | *(unknown)*     | 0x5548     | 0x7C2B  |
  | —  | default         | —          | 0x5551  |

- default (0x5551): `OR 0x01C6,#01; RET` — sets bit 0 of the ack register
  = "ack and ignore". cmd 7's stub is a bare RET = ignore without ack bit.
- 0x568A `h2c_ack_tail`: clears the processed mailbox bit, advances the
  state ring at 0x8B51 (`+1 & 3`), sets bit 1 at 0x01C6, returns.

**Revised Linux-vs-fw picture.** The first pass called SETPWRMODE (1) and
MACID_PS_MODE (7) "missing" — wrong: 1 is handled (0x748B), 7 is
deliberately ignored (bare-RET stub). Genuinely absent from the table (so
ack-and-ignored via default): AP_OFFLOAD (0), PWRM (15), CTW (24),
KEEP_ALIVE_CTRL (48). Linux-unknown ids this fw handles: 32, 44, 45, 46,
59, 75 (Realtek Windows driver commands, presumably). **There is no second
H2C dispatch** — that lead is closed (below).

### Handler semantics, first pass — re-paired to corrected ids

(Addresses verified by the first pass; id attribution corrected.)

- SETPWRMODE (0x748B): ROM call into 0x2A8B, then reads SYS clock reg;
  substantial state setup.
- JOINBSSRPT (0x7485): parks arg0 in IRAM 0x21 and returns.
- cmd 45 (0x74B8): gated on SYS_FUNC bit 0; stores page locations at
  0x8B10/0x8B11.
- cmd 46 (0x7500): arg0 bit 0 = enable; arg0>>1 stored at 0x8B15.
- cmd 59 (0x7539): arg0 mirrored at 0x8B12 and written to MAC reg 0x01E7.
- RSVDPAGE (0x7552): not yet read — the first pass never actually analyzed
  this address; its semantics were mis-filed under cmd 3.
- RSSI_REPORT (0x740F): arg0 (< 0x20) indexes a 32-entry table at 0x8923.
- RA_MASK (0x56A6): branches on chip-version bits of register 0x0004
  (SYS_CFG family) — hardware-variant-dependent behavior.
- ROM helper 0x29D9: "fetch arg byte 0" — handlers call it first; the boot
  ROM is deeply woven into H2C processing.

## Recursive descent (2026-11-05 (2)): architecture and closure

tools/dis8051.py now defaults to recursive descent (old sweep: `--linear`).
Result on TMSC: 8650 instructions, 15365/16094 bytes code, **0 byte
conflicts**, 104 functions ([functions.tsv](functions.tsv)), 5 switch
tables auto-parsed from the walker idiom (45 + 6 cases).

- Every remaining unreached byte range is legitimate data: zero padding
  0x4003–0x4013 / 0x4016–0x4220, the PC-relative bit-mask table 0x4666,
  the 17-byte init-records blob at 0x4B23, 3 tail bytes. The first pass's
  "seq_lut" tables at 0x40DA/0x40F6/0x4112 were artifacts of decoding zero
  padding — annotations deleted.
- **The image's 0x4000/0x4013 LJMP slots are not the whole interrupt
  story.** A 601 B handler with full register-save prologue (0x4B87), a
  174 B RETI-terminated handler (0x688A, alt entry 0x6891) and the 0x4FFC
  switch function have **zero callers inside the image** — the boot ROM
  owns the real 8051 vector table and calls into the image at fixed
  addresses. Architecture: ROM is the kernel, the image is policy.
- 0x4FFC (entry corrected from "0x4FFF" — that was the LCALL site):
  ROM-entered 16-bit bitmask switch (cases 0x40/0x80/0x100/0x200) via the
  0x43BA walker, table at 0x5002; touches TX-pause-family regs → TX/queue
  management, **not** an H2C dispatch. This closes the "second dispatch"
  question: cmds 0/15/24/48 fall to default = ack-and-ignore.
- The 0x6C30 "cluster" decodes cleanly now: it indexes an XDATA table at
  0x88xx, fetches a 2-byte pointer, and restages command bytes into
  0x8AD6/0x8AD7 — the dispatch-time staging area. Not a second dispatch;
  it is what feeds 0x8AD7.
- Error handling: `LJMP $` halt slots at 0x4503/0x4506 (library error
  traps). If the fw ever lands there, the chip is dead until re-download —
  a wedge *shape* to remember, though a library trap is not the observed
  field hang.
- Canonical run (seeds = stranded-code entries found by gap audit; all
  verified as function entries by prologue/termination):

      python3 -I tools/dis8051.py blobs/tmsc.img --base 0x4000 \
        --census analysis/functions.tsv \
        --seed 0x43BA --seed 0x4503 --seed 0x4506 --seed 0x4860 \
        --seed 0x4AE4 --seed 0x4B87 --seed 0x4FE0 --seed 0x4FEF \
        --seed 0x4FFC --seed 0x51C9 --seed 0x67EA --seed 0x6891 \
        --seed 0x7D2C --seed 0x7D39 --seed 0x7E2B

## Open leads

1. ~~Recursive descent in tools/dis8051.py~~ **done 2026-11-05 (2)**, incl.
   function census → analysis/functions.tsv and the dispatch correction
   above.
2. ~~Second H2C dispatch~~ **closed**: no second dispatch. cmds 0/15/24/48
   → default (ack-and-ignore); cmd 7 ignored silently. Residual: read the
   real RSVDPAGE handler (0x7552) — the first pass never actually did.
3. Name the six Linux-unknown commands by reading their handlers with
   register annotations — pairing now corrected: 0x7881 (32), 0x7AC2 (44),
   0x74B8 (45), 0x7500 (46), 0x7539 (59), 0x7C2B (75).
4. Interrupt architecture: 0x60F2 dispatch + ROM-entered 0x4B87 / 0x688A /
   the RETI stubs (0x4DD9, 0x6532, 0x6933 — those are POP/RETI epilogues,
   not switch walkers as first guessed). Which interrupt sources exist,
   what shares state with the H2C path — deadlock candidates for the
   field wedge.
5. Group-key/rekey path: correlate with the field observation that outages
   cluster after the AP's hourly group rekey.
6. 8051 emulator (SIM): run the image against stubbed registers, replay a
   captured pre-hang usbmon trace — the only debugger this chip will ever
   give us. (Capture is running on the affected Pi since 2026-11-05:
   `usbmon-wifi.service`, see doorpi's PI.md — wedges now leave evidence.)
   Descent + census give the emulator a complete code map — unblocked.

## Tooling notes

- tools/dis8051.py: recursive descent by default — roots are the vector
  LJMPs plus `--seed`s; Keil switch tables (inline after `LCALL 0x4394`/
  `0x43BA`) parsed as data with their case targets enqueued as code;
  unreached bytes render as data/gaps; `--census` writes the function
  table; `--walker ADDR:SIZE` extends walker detection; `--linear` keeps
  the old sweep for comparison. Known limits: AJMP/ACALL targets are
  page-sensitive; `JMP @A+DPTR` outside the walker idiom stops flow (none
  occur in reached code). 8051 encodes 16-bit operands big-endian — v1 of
  this tool got that backwards and every absolute target was byte-swapped.
  Fixed; kept as a lesson.
- tools/fwl.py: container/variant analyzer (header dump, entropy profile,
  variant diffing).
