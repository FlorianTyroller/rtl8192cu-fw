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

## Open leads

1. Enumerate all 16 H2C command IDs; cross-reference handler addresses with
   the command names in rtlwifi's rtl8192c/fw_common.c.
2. 0x60F2 interrupt handler: RX/USB path, deadlock candidates.
3. Group-key/rekey path: correlate with the field observation that outages
   cluster after the AP's hourly group rekey.
4. 8051 emulator (SIM): run the image against stubbed registers, replay a
   captured pre-hang usbmon trace — the only debugger this chip will ever
   give us.

## Tooling notes

- tools/dis8051.py: table-driven linear-sweep disassembler with xref labels.
  Known limits: no recursive descent (inline data tables desync the sweep);
  AJMP/ACALL targets are page-sensitive. 8051 encodes 16-bit operands
  big-endian — v1 of this tool got that backwards and every absolute target
  was byte-swapped. Fixed; kept as a lesson.
- tools/fwl.py: container/variant analyzer (header dump, entropy profile,
  variant diffing).
