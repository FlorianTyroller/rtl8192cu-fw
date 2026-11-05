# Findings — RTL8188CUS/RTL8192CU firmware (rtl8192cufw_TMSC.bin 88.2)

Status: first dig, 2026-11-05. Workbench was ~/fwre; this repo is the durable
home. Symbols: [annotations.tsv](annotations.tsv).

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
      (observed targets 0x29D9, 0x2A7F, 0x2FD9, 0x29F2)
- CODE 0x4000–0x7FFF  16 KB fw RAM, the loaded image
- XDATA 0x0000–0x0FFF MAC registers (same indices as USB register space:
      REG_MCU_FW_DL 0x0080, H2C mailbox REG_HMTFR 0x01CC, payload 0x01D0)
- XDATA 0x8Axx–0x8Bxx fw global variables (0x8ACF cmd id, 0x8AD0 cmd
      payload, 0x8B51 state machine state)

## Code map

- 0x4000  vector slots: LJMP 0x4630 (reset), LJMP 0x60F2 at +0x13 (interrupt)
- 0x4630–0x4670  Keil C51 startup: init-table interpreter; bit-mask table
      01 02 04 08 10 20 40 80 inline at 0x4666 (read PC-relative via MOVC
      A,@A+PC — desyncs naive linear sweeps)
- 0x466E  `MOV DPTR,#0x4B23` — pointer to the C init records
- 0x4B23  init records; terminator hands off to main
- 0x50C6  main()
- 0x42xx–0x45xx  runtime library; 0x435F = multi-byte register copy helper,
      hottest function in the image (38 call sites)
- 0x5560, 0x568A  H2C command pollers: cmd = REG[0x01CC] & 0x0F; per-state
      allowed-command bitmask; payload read from 0x01D0 via 0x435F into fw
      RAM 0x8AD0; handed to the state machine at 0x5EE4
- 0x60F2  interrupt handler (candidate hang location — the chip stops
      answering register access when wedged) — unexplored
- main's large callees 0x6844, 0x7255, 0x7842 — unexplored

## H2C command protocol (host ↔ firmware), fully decoded

Driver side (rtlwifi rtl8192c/fw_common.c + reg values): the host has four
4-byte mailboxes — REG_HMEBOX_0..3 at XDATA 0x01D0/0x01D4/0x01D8/0x01DC
(command id byte + 3 arg bytes, extended args in HMEBOX_EXT at 0x0088+ for
5–7 byte commands) — and announces "box N written" by setting bit N in
REG_HMETFR = 0x01CC. The host considers a box free again when the
corresponding HMETFR bit is cleared and MCUTST_1 (0x01C0)+N reads 0.

Firmware side (confirmed by disassembly):

- 0x5559 `h2c_service`: reads 0x01CC, mask 0x0F = pending boxes; for the
  pending one, reads the box (4-byte register copies via the 0x435F helper),
  stages id at 0x8AD0 and args at 0x8AD1+.
- 0x565D: strips H2C_EXT (bit 7) from the id, calls 0x54A6.
- 0x54A6 `h2c_dispatch`: a Keil C51 switch — table walker at 0x4394 over the
  table at 0x54B0 (entries `{target_hi, target_lo, case_id}`, 00 00
  terminator, default → 0x54DE):

  | id | name (rtlwifi)  | handler |
  |----|-----------------|---------|
  | 2  | JOINBSSRPT      | 0x748B  |
  | 3  | RSVDPAGE        | 0x74B8  |
  | 5  | RSSI_REPORT     | 0x7500  |
  | 6  | RA_MASK         | 0x7539  |
  | 8  | P2P_PS_OFFLOAD  | 0x7552  |
  | 9  | MAC_MODE_SEL    | 0x740F  |
  | 32 | *(unknown)*     | 0x56A6  |
  | 44 | *(unknown)*     | 0x759A  |
  | 45 | *(unknown)*     | 0x541E  |
  | 46 | *(unknown)*     | 0x7881  |
  | 59 | *(unknown)*     | 0x7AC2  |
  | 75 | *(unknown)*     | 0x7C2B  |
  | —  | default         | 0x7485  |

- 0x568A `h2c_ack_tail`: clears the processed mailbox bit, advances the
  state ring at 0x8B51 (`+1 & 3`), sets bit 1 at 0x01C6 (host-visible ack),
  returns.

**The firmware understands six command IDs that Linux never sends** (32,
44, 45, 46, 59, 75 — presumably used by Realtek's Windows driver; candidates:
BT coexistence, AP mode offload, RF calibration). Conversely, Linux's
SETPWRMODE (1), AP_OFFLOAD (0), MACID_PS_MODE (7), PWRM (15), CTW (24) and
KEEP_ALIVE_CTRL (48) are *not* in this table — they must route through a
second dispatch (open: the pre-dispatch call at 0x5EE4, or one of the other
switch walkers at 0x43BA, 0x4DD9, 0x6532, 0x6933).

### Handler semantics, first pass (register names via kernel headers)

- default (0x7485): parks arg0 in IRAM 0x21 and returns — **unknown
  commands are silently ignored**.
- JOINBSSRPT (0x748B): ROM call into 0x2A8B, then reads SYS clock reg;
  substantial state setup.
- RSVDPAGE (0x74B8): gated on SYS_FUNC bit 0; stores page locations at
  0x8B10/0x8B11.
- RSSI_REPORT (0x7500): arg0 bit 0 = enable; arg0>>1 stored at 0x8B15.
- RA_MASK (0x7539): arg0 mirrored at 0x8B12 and written to MAC register
  0x01E7.
- MAC_MODE_SEL (0x740F): arg0 (< 0x20) indexes a 32-entry table at 0x8923.
- cmd 0x20 (0x56A6): branches on chip-version bits of register 0x0004
  (SYS_CFG family) — hardware-variant-dependent behavior.
- ROM helper 0x29D9: "fetch arg byte 0" — handlers call it first; the boot
  ROM is deeply woven into H2C processing.

Correction from the first pass: the dispatch switch value is staged at
**0x8AD1** (not 0x8AD0 — `MOV R7,[0x8AD1]` feeds the dispatch call at
0x565D). Annotations fixed accordingly.

### Second dispatch: status

The only caller of the second table walker (0x43BA, 16-bit cases, 4-byte
entries {target_hi, target_lo, case_hi, case_lo}) is at 0x4FFF with its
table at 0x5002 — but that region reads/writes REG_TXPAUSE and REG_FMTHR
(0x01C8, the fw-to-MAC mailbox), i.e. it is TX/queue management, not H2C.
Where SETPWRMODE etc. actually land remains open. The linear sweep desyncs
across inline data tables in exactly the candidate regions (0x6C30 cluster
reads 0x8AD0/0x8AD1 repeatedly — per-mailbox sequencing, or a desync
artifact); **recursive descent is the prerequisite for further progress**.

## Open leads

1. **Recursive descent in tools/dis8051.py** — the prerequisite: linear
   sweep desyncs on inline tables (bit-mask table 0x4666, 0x5002 table
   region, 0x6C30 cluster). Roots: 0x4000 vectors, 0x50C6 main, all FUNC_
   xref targets; unreachable bytes become data. Also: function census
   (size/xrefs) into analysis/functions.tsv.
2. Second H2C dispatch: where do cmds 0/1/7/15/24/48 land? Re-examine
   0x5EE4 and the 0x6C30 cluster after recursive descent.
3. Name the six Linux-unknown commands by reading their handlers
   (0x56A6, 0x759A, 0x541E, 0x7881, 0x7AC2, 0x7C2B) with register
   annotations.
4. 0x60F2 interrupt handler: RX/USB path, deadlock candidates.
5. Group-key/rekey path: correlate with the field observation that outages
   cluster after the AP's hourly group rekey.
6. 8051 emulator (SIM): run the image against stubbed registers, replay a
   captured pre-hang usbmon trace — the only debugger this chip will ever
   give us. (Capture is running on the affected Pi since 2026-11-05:
   `usbmon-wifi.service`, see doorpi's PI.md — wedges now leave evidence.)

## Tooling notes

- tools/dis8051.py: table-driven linear-sweep disassembler with xref labels.
  Known limits: no recursive descent (inline data tables desync the sweep);
  AJMP/ACALL targets are page-sensitive. 8051 encodes 16-bit operands
  big-endian — v1 of this tool got that backwards and every absolute target
  was byte-swapped. Fixed; kept as a lesson.
- tools/fwl.py: container/variant analyzer (header dump, entropy profile,
  variant diffing).
