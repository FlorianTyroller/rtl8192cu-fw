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

## H2C parameter staging — resolved (2026-11-06)

The full path of a command, now traced end-to-end (0x5559 service body):

1. `[0x8ACF] = REG_HMETFR & 0x0F` (pending boxes); the box to read is
   selected by the state ring: box regs are addressed as `0x01D0 + 4*state`
   — 0x435F is **not** a copy helper but `DPTR += A*B` (MUL AB then add),
   a scaled-index helper used everywhere for `base + state*stride`.
2. Box byte 0 (id, H2C_EXT bit included) → `0x8AD0`; then 0x5EE4 — a Keil
   generic-pointer memcpy (src/dest ptrs parked at 0x8B3F/0x8B42, byte
   loop via 0x4374 ptr-increment + ROM 0x29D9 read + 0x424D write) —
   copies it to `0x8AD1`. (0x29D9's use *inside* this loop proves it is a
   pointer deref, not a literal fetch.)
3. Dispatch id = `[0x8AD1] & 0x7F`; bit 7 (H2C_EXT) is extracted at
   0x55BB by SWAP + 3×RRC + AND 1 and selects the staging mode:
   - basic: `param[0..2] = box bytes 1..3` → 0x8AD2..0x8AD4
   - extended: `param[0] = EXT[0x0088+2*state]`, `param[1] =
     EXT[0x0089+2*state]`, `param[2..4] = box bytes 1..3` → 0x8AD2..0x8AD6
4. 0x54A6 stores the constant pointer {1, 0x8A, 0xD2} (XDATA:0x8AD2) at
   0x8AD7 via 0x438B — that stored pointer is what every trampoline reads
   back with 0x436B. **The handler ABI is just `&staging[0]`.**

The EXT layout — two bytes per box at 0x0088+2*box — matches rtlwifi's
reg.h `REG_HMEBOX_EXT_0..3 = 0x0088/0x008A/0x008C/0x008E` exactly
(rtl8xxxu's regs.h naming, one byte per box, is the imprecise one).
rtlwifi's 6-byte RA_MASK (`6 | H2C_EXT`, 4 mask bytes split
box1..box3 + EXT) therefore arrives as param {mask[3], ext1, mask[0],
mask[1], mask[2]} — which is why the RA_MASK handler reloads multiple
pointer slots (0x8ADC/0x8ADE) to reassemble the 32-bit mask.

Loose end worth a look: 0x6CE5 (inside the 0x6BBD function) indexes a
**CODE table at 0x41BA by the command id** (MOVC) and compares it against
the 16-bit value at [0x8AD6:0x8AD7]. 0x41BA sits inside the zero padding,
so every threshold is 0 in this build — a per-command limit table compiled
to nothing here (or reserved for other SKUs).

## H2C handler ABI and the six Linux-unknown commands (2026-11-05 (3))

Lead #3 executed. The headline is the **argument ABI**, which re-frames all
handler analysis: every trampoline handler receives a Keil C51 *generic
pointer* in R3:R2:R1 (R3 = space: 0=IDATA, 1=XDATA, 0xFE=PDATA, 0xFF=CODE;
R2:R1 = address) — the trampoline's `LCALL 0x436B` loads exactly that from
staging 0x8AD7. Field access:

- ROM 0x29D9 → param byte 0
- lib 0x4220 with `DPTR=#N` → param byte N (verified: 0x4220 is the Keil
  generic-ptr deref with DPTR as offset — the earlier "single MAC register
  read" reading was the special case ptr={1,0,0}; several sites first
  reload R3:R2:R1 from other pointer slots 0x8ADC/0x8ADE via 0x436B,
  proving multiple staged pointers, not one)

So H2C box bytes 1–3 = {space, addr_hi, addr_lo} of a parameter block
inside the chip. ~~Whether the block comes from the raw mailbox args or
from the fw's own per-command defaults … is open~~ — **RESOLVED
2026-11-06**, see next section: the pointer always targets the fw's own
staging at 0x8AD2, which the service fills with the raw mailbox bytes.
rtlwifi's literal arguments work exactly as the driver intends.

### The six Linux-unknown commands, read

| id | handler | semantics | conf. |
|----|---------|-----------|-------|
| 32 | 0x7881 | `[0x8B38] = param[0]`, RET. 0x8B38 has exactly one consumer: 0x6547 computes `param0 + REG_DRIVER_EARLY_INT − 2` → 0x8B3E (beacon early-rx timing). **Beacon early/pass-time offset.** | high |
| 44 | 0x7AC2 | TX-buffer offload op: computes `[0x8AE0] = TDECTRL[0x0209] + param[0]`, bit 0 selects a mode word at 0x8ADB; 8-iteration 16-bit sum loop over a struct in **XDATA 0xFCxx** (packet-buffer space); stages work block 0x8ADA–0x8AEE, calls the offload state machine **0x7889** (switch on staged type byte: cases 0–5, 0x80–0x82, table at 0x78D9); then polls status reg **0x01C3** against 0xE8/0xEB thresholds with ROM 0x3754 retries; completion acks via **bit 4 of 0x01C6**. Shape says checksum/TCP-offload (Windows NDIS style); functional name unproven. | behavior high, name med |
| 45 | 0x74B8 | System-config snapshot: if `param[2] & 1` → latch `{param[0]→0x8B10, param[1]→0x8B11, (param[2]>>1)&0x7F→0x8B0F, param[3]→0x8B16}` else defaults {3, 5, 0x14, 5}. Block 0x8B0F–0x8B16 is read all over the power/restore paths (0x44DA, 0x47D7, 0x4B5D inside the ROM-entry handler, …). **Power-state restore-parameter snapshot.** | high |
| 46 | 0x7500 | If `param[0] & 1`: `[0x8B15] = param[0]>>1`, `REG_DRIVER_EARLY_INT (0x0558) = param[1]`; else `[0x8B15] = 5`, `0x0558 = 3`. **Beacon early-interrupt config** (enable + value, defaults 5/3). | high |
| 59 | 0x7539 | `[0x8B12] = param[0]`; if nonzero calls mode-switch 0x45A2(0, 4) (compares 4-bit mode vs IDATA 0x22); writes `REG[0x01E7] = param[0]` (fw-private reg). Rate-mask mirror + queue-mode kick; BT-coex rate control is the leading guess. | med |
| 75 | 0x7C2B | `[0x8B05] = param[0]; [0x8B06] = param[1]`, RET. Consumers: 0x8B05 read at 0x4CDF (startup family), 0x8B06 at 0x6336. Parameter handoff, purpose unproved. | behavior high |

And the residual from lead #2:

- **RSVDPAGE (3, 0x7552)**: `R5 = TDECTRL[0x0209]` (direct read — current
  TX-DMA page base); stores `R5 + param[N]` for N=0..4 → 0x8AF7–0x8AFB.
  Five reserved-page locations computed off the live page base. Matches
  rtlwifi's intent (fw TXes null/ps-poll from those pages during LPS);
  the 5-vs-3 arg question folds into the open pointer-ABI question above.

New small facts: 0x01C6 ack register actually carries (at least) bit 0 =
unknown-cmd ack (default handler), bit 1 = processed (h2c_ack_tail),
bit 4 = cmd-44 offload completion. XDATA 0xFC00–0xFD10 = packet/descriptor
buffer space the fw reads and writes directly. Reg 0x01C3 = offload status.
ROM 0x3754 = poll/retry service (called with code 0x0A).

First-pass casualties of the ABI discovery: every earlier "MOV DPTR,#NN;
LCALL 4220 = read reg NN" interpretation inside H2C handlers is re-labeled
param[N] (e.g. cmd 45's "SYS_FUNC gate" is really `param[2] & 1`; RA_MASK's
"SYS_CFG version bits" is param[4]). Reg reads that survive are the direct
MOVX ones (TDECTRL 0x0209, DRIVER_EARLY_INT 0x0558, 0x01E7).

## Interrupt architecture, first pass (2026-11-06)

- 0x60F2 (vector 0x4013): full-context save (ACC/B/DPTR/PSW/AR0–7, bank 0),
  then **writes its own address little-endian to regs 0x01C4/0x01C5**
  ({0xF2,0x60}), then reads the 4-byte **REG_FWISR 0x0134–0x0137**, ANDs
  each byte with the fw-configurable enable masks in IDATA 0x28–0x2B,
  parks the masked copies in IDATA 0x2C–0x2F, and dispatches by bit
  tests (source 0 first at 0x613A, W1C: writes 1 back to 0x0134). A long
  chain of source handlers fills 0x613A–0x65xx. Per-source naming
  (RX/USB/TX-beacon/…) is open — next session's map.
- The other ROM-entered handlers do the same 0x01C4 announce: 0x4B87
  writes {0x87,0x4B}, 0x6891 writes {0x91,0x68}. **0x01C4/0x01C5 is a
  host-readable "last fw entry point" indicator** in the MCUTST area —
  when a stick wedges, reading that register pair over USB tells us
  which handler the fw entered last. Field-diagnostic gold; the usbmon
  service on doorpi should capture it on the next wedge.
- The `SETB C; JBC AF,1; CLR C; PUSH PSW` prologue seen on h2c_service,
  cmd 44, 0x7889, 0x45A2 … is the Keil critical-section idiom: bit AF =
  IE.7 = EA; it saves and clears the global interrupt enable. PSW is
  restored (`POP D0; MOV AF,C`) only at the very end of h2c_service —
  **the entire H2C chain (dispatch → trampoline → handler → ack) runs
  with interrupts disabled.** Consequence for the wedge hunt: any handler
  (or boot-ROM call it makes, e.g. SETPWRMODE's 0x2A8B) that polls a
  register advanced only by an interrupt or the USB side while EA is off
  hangs the 8051 permanently. That is the exact deadlock shape to audit
  handler-by-handler.
- 0x4DD9 / 0x6532 / 0x6933 (first-guessed "switch walkers") are shared
  interrupt epilogues: POP chains + RETI, entered by AJMP from the
  source handlers.

## FWISR source map and EA-off deadlock audit (2026-11-06 (2))

Dispatcher (0x60F2) sources, in dispatch priority order. W1C = writes the
bit back to the FWISR byte. FWISR1 (0x0135) has no handler in this build.

| source | handler | behavior |
|--------|---------|----------|
| FWISR0.b0 | 0x613A | W1C; reads hw context latches (custom SFRs 0xD1–0xD7, 0xD9 → IDATA 0x4D–0x54), branches on their bits |
| FWISR0.b1 | 0x628E | W1C; same SFR-latch copy → IDATA 0x56–0x5D; calls 0x5FA4 |
| FWISR0.b3 | 0x62B4 | W1C only (pure ack) |
| FWISR0.b4 | 0x62BF | W1C; sets IDATA 0x55 bit 4 |
| FWISR0.b5 | 0x62CD | **clock-off/IPS entry** — see below |
| FWISR0.b6 | 0x62F7 | W1C; updates mode-flag block 0x8B2C/0x8B2E/0x8B32/0x8B34 |
| FWISR0.b7 | 0x620F | calls 0x4281, 0x5C66, 0x5CC3 (protocol event) |
| FWISR2.b0 | 0x632C | biggest: W1C, TXPAUSE 0x0522, DUAL_TSF_RST 0x0553, 0x05FC; calls 0x4DE0/0x4E89/0x6539/0x655F/0x6582; reads cmd-75's 0x8B06 and cmd-45's 0x8B11 — beacon/TSF/TX scheduling |
| FWISR2.b1 | 0x63EA | W1C, TXPAUSE; calls 0x4DE0, 0x6084 |
| FWISR2.b2 | 0x641E | W1C; calls 0x46B3 |
| FWISR2.b3 | 0x6439 | W1C, timer regs 0x013C/0x0157; calls 0x4B6C; writes beacon-early globals 0x8B3D/0x8B3E |
| FWISR2.b4 | 0x6476 | W1C, 0x013C/0x0157; calls 0x4AFC; 0x8B1B |
| FWISR2.b5 | 0x64A6 | W1C; calls 0x4949, 0x4A97; 0x8B1A |
| FWISR2.b6 | 0x64CA | W1C; calls 0x4AFC |
| FWISR3.b0 | 0x64F7 | calls 0x48FE, 0x4DE0, 0x7DC1; 0x8B31 |
| FWISR3.b1 | 0x64EA | W1C; 0x8B2C |

**FWISR0.b5 = the clock-off/IPS path** (0x62CD): quiesces everything —
`REG_FWIMR[0x0130–0x0133] = 0` (via 0x519D, also FTIMR), clears 0x01CF
bit 5, W1C, `IE = 0`, custom SFR 0xE8 = 0, clears reg 0x0003 bit 1 (the
8051 clock gate), 16-cycle settle delay (0x520E), then **`SJMP $` at
0x62F1**. Wake must come from outside (USB resume → hardware 8051
restart); if that handshake fails, the chip sits at 0x62F1 forever with
all interrupts masked. Third known spin point after the 0x4503/0x4506
library error traps.

**EA-off deadlock audit verdict**: a scanner over all H2C-reachable code
(87 functions, call-graph closed over the census) found exactly three
backward-jump hardware-adjacent loops — 0x725A, 0x7A79, 0x7AA3 — and all
three are bounded self-incrementing counter loops (ROM call per step,
fixed trip count). cmd 44's 0x01C3 status poll is bounded at 1000 retries
(`[0x8AE2:0x8AE3]` vs 0x03E8) before giving up with ack bit 4; the
REG_SCH_TX_CMD (0x05F8) drain at 0x7EA1 likewise caps at 1000. **No
unbounded hardware poll exists in H2C-reachable image code.** The
remaining deadlock surface is (a) the boot ROM (SETPWRMODE's 0x2A8B etc.
— statically unassessable, needs the emulator) and (b) the three
intentional spin points. Corollary: a field wedge that persists across
the 1000-retry bounds is either stuck in ROM or at a spin point.

Other finds from the same sweep: 0x7E2B is the **timer-event/beacon-loss
processor** — W1C-style drains of regs 0x0157/0x015B/0x015F, writes timer
int reg 0x013C bits 2/3/4, counts events in 0x8B0C and misses in 0x8B0D
(incremented when opmode == infra and the count crosses 0x8B10 — the
value cmd 45 stores: the beacon-loss threshold feeding power-save).
0x7DC1 maps opmode → TXPAUSE (0x00/0xFF/hold) and acks via **0x01C6 bit
3** — so the ack register now has four known bits: 0 unknown-cmd, 1
processed, 3 opmode-change, 4 offload-done. The 0x7C4A/0x7C50/0x7D42/0x7D23
family are TX-drain/power-transition sequences built on ROM helpers
(0x27DE/0x2A7F/0x2FD9/0x2A8B/0x3481/0x3754) and REG_SPS0_CTRL (0x0011).

## Emulator v1 (2026-11-06 (3)): tools/sim8051.py

The chip has no debugger; now we have one. Full 8051 core, image at
0x4000, boot-ROM calls trapped and stubbed (0x29D9 emulated as the
generic-ptr read per the reverse-engineered ABI), MAC-register writes
logged, halt-slot detection, H2C mailbox injection (`--h2c id:b1:b2:b3
--ext e0:e1`, entry defaults to the service routine with a sentinel
return frame).

Validated against the static model — all four tests agree:

| test | result |
|------|--------|
| cmd 5 (RSSI) basic | id staged 0x8AD0/0x8AD1, param = {b1,b2,b3}, state ring advanced, HMETFR W1C, clean return |
| id 0x55 (unknown) | default handler → **0x01C6 bit 0 ack** |
| cmd 1 (SETPWRMODE) | ROM 0x2A8B + writes 0x0136/0x0137 (FWISR+2/+3), 0x0604, 0x060A, **REG_DRIVER_EARLY_INT 0x0558 = 0** — power-mode setup programs the beacon early-int (new fact) |
| cmd 6\|EXT (RA_MASK) | param = {EXT0,EXT1,b1,b2,b3} = 44 55 11 22 33 — extended staging confirmed; handler writes reg 0x052C |
| cmd 7 | zero side effects — "silently ignored" confirmed dynamically |

Also refined dynamically: the 0x01C6 bit-1 "processed" ack is written on
the service's no-more-work exit path, not per box.

Known limits (v1): no interrupt model (FWISR never fires — main-loop and
ROM-context paths diverge; enter at the service instead of reset), ROM
stubs return immediately with plausible values (0x2A7F/0x2FD9 return
A=0), reset-path execution depends on ROM-initialized state we cannot
reproduce. Next: scripted FWISR injection, stateful ROM stubs, then
replay of the doorpi usbmon capture.

## Variant validation (2026-11-06 (4))

The full toolchain (descent + walker parsing + seed audit) runs clean on
all three firmware builds:

| variant | size | instructions | functions | tables | data-gap bytes |
|---------|------|--------------|-----------|--------|----------------|
| TMSC (analyzed) | 16094 | 8650 | 104 | 5 | 566 |
| A | 16094 | 8650 | 104 | 5 | 566 |
| B | 16064 | 8640 | 102 | 5 | 565 |

A differs from TMSC in 211 bytes (A-cut config; identical layout). B is
30 B smaller with a slightly shifted layout and needs its own seed list
(14 seeds, in the canonical command note below); every remaining gap in
all three variants is legitimate data (vector padding, bit-mask table,
init records, tail).

Cross-variant architecture fact: the ROM-ABI entry points 0x4860,
0x4AE4 and 0x4B87 sit at **identical addresses in TMSC/A and B** — the
boot ROM calls into the image at fixed, build-stable addresses. The
Keil walker bodies (0x4394/0x43BA) and the library cluster are also
position-stable; only the 0x4Fxx-0x7E application layer shifted.

B canonical seeds: 0x43BA 0x4503 0x4506 0x4860 0x4AE4 0x4B87 0x4FDE
0x5062 0x506F 0x523C 0x604E 0x675B 0x7DA7 0x7DB4.

## "First wedge" capture — RECLASSIFIED as AP-side (corrected 2026-11-06 (9))

The usbmon capture on doorpi caught a complete wedge tonight
(2026-11-06 01:09–01:26). Trace saved at ~/wedge-captures/
20261106-0126-wedge.gz (lines 700000–990000 of /var/log/usbmon-wifi.log;
kept out of this repo — it embeds firmware-download bytes). Register
accesses decode as USB control rq05 (wValue = MAC register address),
which makes the whole driver↔fw conversation readable with our reg map.

Anatomy (anchor 0 s = 01:25:54.7, the USB reset):

| time | event |
|------|-------|
| 01:09:33 (−981 s) | signal to primary AP collapses; **roam to second AP AA:00:00:00:00:02**. H2C burst: JOINBSSRPT(0) → RA_MASK\|H2C_EXT (32-bit mask 0xFFFFA00F via EXT regs 0x0088=ffff) → JOINBSSRPT(1) → RA_MASK\|EXT. Assoc OK (aid 18). |
| 01:09:35 (−980 s) | **unicast RX collapses** (906 → ~150 frames/10 s; residual = beacons/broadcast). Driver RSSI reports halve: 0x49/0x4a → 0x1e–0x22. |
| −980 s … −316 s | **broadcast-only limbo, ~11 min**: driver keeps the RSSI H2C cycle (every 2.08 s, boxes rotating), and **0x01CC reads return 0x00 every single time** — the fw acks every mailbox promptly. The 8051 command path is fully alive. |
| 01:20:38 (−316 s) | RSSI cycle stops (driver-side: nothing left to report). |
| 01:23:14 (−160 s) | auth-retry storms; **RX is flaky, not dead**: 3 auth attempts to primary AP get no reply, but 01:23:27 auth to second AP succeeds (assoc times out), 01:23:46 auth **and** AssocResp both succeed (aid 22). Low-rate frames sometimes get through. |
| 01:25:53 (0−1.7 s) | watchdog reloads driver; `rtl8xxxu_active_to_emu: Disabling MAC timed out` — the fw never completes the register-level power-down handshake. USB reset, fw reload, recovery on the primary AP. |

**Correction 2026-11-06 (9).** The timeline above was mis-narrated. The
watchdog's own journal shows the gateway REACHABLE every 90 s from
01:10:04 through 01:22:34 (12 consecutive checks), and the bulk-in
stream during the "limbo" carries 7,031 frames >160 B — real data
traffic. The 01:09:35 traffic drop is the quieter repeater BSS, not
unicast death. Corrected reading: 01:09:33 legitimate roam (primary
signal collapse) → working link via the repeater AA:00:00:00:00:02 →
~01:22:34 the repeater/backhaul dies → auth dance (primary out of
range; repeater assoc flaky at RSSI 0x1f, assoc OK 01:23:46 but
backhaul dead) → watchdog reload. **Tonight's outage was AP-side, not a
firmware wedge.** The H2C path was alive because the fw was healthy.
The only genuine fw anomaly tonight: the MAC-disable handshake timeout
during teardown. The emulator TXPAUSE-stuck state (section 8) remains a
real, discovered latent failure mode — but with **zero field
confirmation**; the "unicast dead / broadcast flows / 11-min limbo"
field claims built on this event are withdrawn.

Sharpened discriminator for future captures — a REAL fw wedge means:
link dead + no AP-side explanation in the journal (no roam, no beacon
loss to a visibly dying AP) + H2C cycle continuing (0x01CC reads 0x00)
+ preferably 0x01C4/0x01C5 read out. doorpi's environment has TWO APs
(primary AA:00:00:00:00:01, repeater AA:00:00:00:00:02) — every capture
must be checked against both before blaming the stick. One open oddity
from tonight: the driver's RSSI H2C cycle stopped at 01:20:38, ~2 min
before the repeater died, while the link still worked.

Conclusions that change the model (superseded by the correction above):

- **The wedge is not an 8051 hang.** The H2C/mailbox state machine stayed
  responsive for the entire 16-minute death spiral (0x01CC = 0x00 even at
  −1 s). Neither the EA-off deadlock shape nor a spin point matches.
- The wedge lives in the **MAC RX datapath / RX-processing**, is
  progressive, and rate-dependent (basic-rate frames sometimes pass,
  data doesn't).
- **Trigger correlates with AP roam**, not group rekey: the burst is
  exactly the BSS-transition H2C sequence (JOINBSSRPT×2 + RA_MASK|EXT×2),
  landing on handlers 0x7485 (opmode→IDATA 0x21), 0x56A6 (RA_MASK,
  chip-version branch, EXT mask reassembly) and the 0x7E06/0x7DC1
  opmode→TXPAUSE machinery — with the beacon-loss counters 0x8B0C/0x8B0D
  (threshold 0x8B10) firing mid-transition because a roam IS a
  beacon-loss storm.
- The telemetry PI.md called "~20 s of broadcast-only" was **~11–13 min**
  tonight: the broadcast-only phase is long and variable; only its end
  (auth failures) trips the watchdog.
- Previous wedges this boot (Nov 4 21:06, Nov 5 07:10) predate the
  surviving logs — from now on, every wedge's journal should be checked
  for a "authenticate with <BSSID ≠ primary>" roam burst just before
  onset (cheap discriminator for the roam hypothesis).
- Repro candidate: force a roam (power-cycle the primary AP while the
  stick is associated) — if that reproduces the wedge on demand, we have
  a live trigger instead of a 1/day wait.

Fw-side suspects, in order: (1) RA_MASK handler 0x56A6 racing the
beacon-loss/power-save path during BSS transition (all EA-off, ROM calls
in the loop); (2) the opmode→TXPAUSE/queue reconfigure (0x7DC1/0x4DE0
flag block); (3) an RX-aggregation/filter state left inconsistent by the
transition (FWISR0.b0/b1 latch path). The emulator (sim8051.py) can now
replay this exact H2C sequence against the image — next step.

## Roam burst replayed in the emulator (2026-11-06 (6))

sim8051.py now has `--script` replay: `box N bytes` (hardware sets the
HMETFR bit on box writes — verified against the trace: zero host writes
to 0x01CC exist), `ext ADDR bytes`, `fwisr b3:b2:b1:b0`, `state`. W1C
semantics modeled for 0x01CC and FWISR 0x0134–0x0137 (fw writes clear
bits). Two protocol facts closed on the way: the driver picks the box
the state ring will service next (trace box order 3,0,1,2,3 = ring
order), and the kernel's 8051-reset handshake writes bit 5 to 0x01CF —
exactly the bit the FWISR0.b5 clock-off handler double-checks (the
reset path and the clock-off handler are two ends of one protocol).
Also: MCUTST_1+N (0x01C0+N) is a per-box status byte; cmd 44's poll of
0x01C3 is a wait on box 3's status.

Replay results — corrected 2026-11-06 (7); the first comparison was
invalid (see below):

- **RA_MASK|EXT always programs REG_INIDATA_RATE_SEL+macid** — a
  protocol-legal clean control (JOINBSSRPT(1) then RA_MASK, consecutive
  ring boxes) takes the same ~1700-step path and writes 0x0484 = 0x13
  exactly like the roam burst. The write comes from helper **0x5C4E**:
  `[0x0484 + R7] = R5` — a macid-indexed rate-register writer (0x0480 =
  INIRTS_RATE_SEL+macid, 0x0484 = INIDATA_RATE_SEL+macid); a sibling
  helper writes macid classifications to 0x8722. The "roam-only rate
  divergence" first reported here was an artifact and is withdrawn.
- **The service is ring-strict** (verified dynamically and in code):
  it services only the box the 0x8B51 ring points at and exits without
  scanning when that box is not pending. An out-of-ring box write
  starves until the ring reaches it — the real driver always writes
  ring-consecutive boxes (trace: 3,0,1,2,3), so this constraint is
  invisible in the field but is a real protocol property (and the reason
  my first "clean control" never ran its RA_MASK at all).
- **0x01C4 indicator gains a value: 0x60F3 = dispatcher-exit marker**,
  written at 0x6519 after every interrupt pass. Readout table for a
  wedged stick: 0x60F2 = stuck inside the interrupt dispatcher,
  0x60F3 = interrupts completing normally (wedge elsewhere),
  0x4B87/0x6891 = inside those ROM-entry handlers.
- **Interleaved FWISR events (2.b3 timer, 2.b0 beacon/TXPAUSE, 0.b0 RX
  context) between the roam commands: all clean** — no hangs, no ack
  anomalies, no register-level corruption. The beacon/TXPAUSE handler's
  actions are gated on mode globals (0x8B2C/0x8B2D family) that are zero
  in a cold sim, so it writes nothing; exercising its real behavior
  needs seeded state, and the MAC RX datapath itself is beyond pure-8051
  emulation. Honest scope statement: the emulator proves the 8051-side
  protocol and handlers stay consistent through the roam burst — the
  wedge, per the field evidence, lives below that, in MAC state.

Next: seed realistic globals (mode flags, 0x8B10 beacon-loss threshold,
counters) via a legal H2C warm-up sequence, then re-inject FWISR2.b0/b3
and watch TXPAUSE and the 0x7E2B beacon-loss path for opmode-dependent
misprogramming; in parallel, the field discriminator (roam bursts in
the journal before wedge onset) keeps running on doorpi.

## Wedge mechanism candidate reproduced (2026-11-06 (8))

With seeded-but-plausible state (assoc flag [0x8B32].0 = 1, [0x8B33] ≠ 3,
beacon-loss threshold 0x8B10 = 5 via a real cmd-45, opmode = 1 via real
JOINBSSRPTs, mode nibble IDATA 0x22 = 4 via real cmd 59), a storm of
timer events (hw latch 0x013E bit 1 + event reg 0x0157, handler entered
at the ROM vector 0x4B87) drives the firmware into a **TX-paused state
it cannot leave**:

- The 0x4B87 timer case (dispatched via 0x013C-block bits, masked by
  IDATA 0x30–0x33; case 0x013E.b1 at 0x4D70) runs the deep path:
  W1C 0x013E @0x4D78 → second W1C @0x4F93 → **TXPAUSE (0x0522) = 0xFF
  @0x4F77** → **REG_SPS0_CTRL (0x0011) = 0 @0x7D1F**.
- Afterwards the "recovery" H2C set — JOINBSSRPT(1) + RA_MASK|EXT +
  cmd 59 — **cannot lift the pause**: cmd 59's unpause sequence (the
  0x7D42 drain: SPS0_CTRL |= 9, TXPAUSE = 0) only runs inside the
  mode-switch helper 0x45A2 when the mode nibble CHANGES; post-roam it
  is unchanged, so the whole drain is skipped (observed: 477 steps,
  only the rate mirror 0x01E7 written). TXPAUSE stays 0xFF forever.
- The H2C path remains fully responsive throughout — every command
  acked, exactly the field evidence (0x01CC = 0x00 for 16 min).

This matches the field wedge signature: TX paused (unicast dead),
beacons/broadcast still flowing, mailbox alive, only fw re-download
recovers. Attribution notes, honestly: the TXPAUSE=0xFF write comes
from the 0x4D7x deep path (0x4F77), NOT via the 0x7E2B beacon-loss
counters as first hypothesized (0x8B0C/0x8B0D stayed 0 in this
variant); the flags were hand-seeded (plausible, not legally derived
end-to-end); and one field nuance stays open — the stick TX'd auth/
assoc successfully at 01:23:46 during the real wedge, which a full
0xFF pause should prevent (mgmt-queue bypass? partial pause windows?
needs the TXPAUSE queue-bit map and finer trace reading).

fw-side fix candidates this suggests (for a host-side workaround):
after any roam/BSS transition, force the mode-switch to re-run (e.g.
cmd 59 twice with different param[0]s, or a JOINBSSRPT(0)→(1) cycle
AFTER the flags settle) so the drain/unpause path executes; or
periodically rewrite TXPAUSE = 0 from the driver when associated.

## Flag-block provenance, and where static work ends (2026-11-06 (10))

Chasing the legal derivation of the TXPAUSE-stuck repro's seeded flags:
the 0x8B2C–0x8B37 block is *read* and decoded all over the 0x4Bxx–0x4Fxx
timer/ROM-entry region (e.g. 0x4CFF, 0x4D79, 0x4E00 decode bits 1/2 and
5–7 into mode decisions), but its **writers are ROM-mediated** — the
write-helper idiom (ROM 0x27DE/0x2A7F with computed DPTRs, as in the
0x7C50 tx-drain family) or fw init in the unreadable boot ROM. A fully
static, fully legal derivation of [0x8B32].0 is therefore **not
possible**; the emulator repro with two hand-seeded flag bytes is the
honest endpoint of static + emulation analysis. Upgrading the TXPAUSE
failure mode from "lab repro" to "confirmed field mechanism" now
requires hardware evidence: either the forced-roam experiment (designed
to separate fw-wedge from AP-side noise — tonight's reclassification
shows how easily AP noise impersonates a wedge) or a future capture that
passes the discriminator (link dead + no AP-side journal explanation +
H2C alive + 0x01C4 readout).

## Plan for the next session (written 2026-11-06, night)

Static + emulation work is exhausted (see (10)); everything below needs
hardware, the field box, or user actions. In priority order:

1. **Forced-roam experiment** (needs the user present — it bounces the
   home network). Two variants, both with the usbmon capture running:
   a. *Roam variant*: associate doorpi to the primary AP
      (AA:00:00:00:00:01), repeater reachable → power-cycle the PRIMARY
      → the stick must roam to the repeater. This recreates the 01:09:33
      scenario WITHOUT the backhaul-death noise. Watch: does the link
      survive the roam? H2C behavior? TXPAUSE-recoverable?
   b. *Beacon-loss variant*: make BOTH APs unreachable (kill primary,
      repeater already out of range or blocked) → pure beacon loss, no
      roam target → the timer/flag machinery under load (the lab
      TXPAUSE-stuck path). Watch for: link death with NO AP-side journal
      story = the discriminator firing for the first time on demand.
   Log everything; judge against the wedge discriminator (below).
2. **Wedge discriminator checklist** for every future watchdog trigger
   (currently manual; automating on doorpi needs sudo):
   - journalctl -k around onset: roam? "authenticate with <BSSID>"?
     beacon loss? Check BOTH APs (primary AA:00:00:00:00:01, repeater
     AA:00:00:00:00:02 — the repeater impersonates wedges).
   - usbmon: H2C cycle continuing (0x01CC reads = 0x00) while the link
     is dead → fw-side suspicion.
   - 0x01C4/0x01C5 readout (0x60F2 stuck-in-dispatcher / 0x60F3 clean /
     0x4B87 stuck-in-ROM-handler) — needs a MAC-reg read path; rtl8xxxu
     has none, so this means a tiny helper (ethtool? custom) — stretch
     goal, needs sudo + user buy-in.
3. **Driver-side workaround prototype** (needs sudo on doorpi + user
   OK): after every roam, force the fw's mode-switch to re-run so the
   TX-drain/unpause executes (cmd 59 twice with different param[0], or
   a JOINBSSRPT 0→1 cycle) — rebuild via pi/rtl8xxxu-build.sh. This
   immunizes against the lab-reproduced TXPAUSE-stuck class even if it
   is never confirmed as the field cause.
4. **Optional lab fuzzing** (bounded): extend sim80511 with a scripted
   expected-beacon model and fuzz the timer/flag state space for more
   unpausable states; the two seeded bytes are the only unverified part
   of the repro.
5. Oddity worth a look when convenient: the driver's RSSI H2C cycle
   stopped at 01:20:38, ~2 min before the repeater died, while the link
   still worked (rtl8xxxu source read).

Support: replay scripts are in tools/sim-scripts/ (roam-burst,
txpause-stuck, clean-assoc); wedge traces land in ~/wedge-captures/ on
this machine (NOT the repo — they embed firmware-download bytes).

## Forced-roam experiment, variant (a): CLEAN (2026-11-06 (11))

User power-cycled the primary AP at 15:07:50 (router UI). The stick
handled it without incident — **no wedge, no watchdog intervention**,
~70 s total disruption:

- 15:08:00 deauth from primary → hunt: repeater 84:90:0a (3 auth tries,
  no reply — flaky again), a third AP AA:00:00:00:00:03 (no reply),
  two aborted rounds to the still-rebooting primary
- 15:08:49 associated to the repeater (after "regulatory prevented
  using AP config, downgraded"), left it 3 s later when the primary
  reappeared, one more flaky repeater auth timeout
- 15:09:02+ back on the primary. Watchdog: zero unreachable counts.

The usbmon capture (~/wedge-captures/20261106-1508-forced-roam.gz)
shows the healthy roam uses **the identical H2C command vocabulary** to
the 2026-11-06 01:09 event: JOINBSSRPT(0) → RA_MASK|EXT →
JOINBSSRPT(1) → RA_MASK|EXT — executed twice (repeater, then primary)
within 62 s — followed by a fully working link. **The BSS-transition
burst is benign by itself**; the 01:09 outage remains attributed to the
repeater environment (its auth flakiness recurred today). Consistent
with the TXPAUSE-stuck lab mode requiring flag-block state a healthy
roam does not produce.

Environment (user-confirmed): router on the ground floor — the Pi sits
**10 cm from it** — plus exactly **two repeaters**: one in the basement,
one on the 1st floor. All three observed BSSIDs are the user's own:
AA:00:00:00:00:01 = router, AA:00:00:00:00:02 and AA:00:00:00:00:03 =
the two repeaters (which is which floor is unconfirmed). **Follow-up, answered from the capture (2026-11-06 (12)):** the primary
DID stop beaconing. The capture's bulk-in stream around the roam holds
two periodic beacon classes with distinct sizes and TSF phases: a
158-B stream that **stops dead at 01:09:27.7** — six seconds before the
roam — and a 150-B stream (the repeater's beacons, received all along
on the shared mesh channel; its RX-descriptor profile changes shape
after the roam when it becomes the serving BSS). So: primary beacons
vanished at 01:09:27.7 → mac80211 beacon-loss disconnect (~6 s) → roam
to the repeater → repeater backhaul dead → outage. With the stick 10 cm
from the router, the primary's radio must have blipped (the Speedport
Smart 4 exposes no event log to check what it did). The 2026-11-06
01:09 event is now explained end-to-end with **zero firmware-side
evidence**. The fw remains clean across every captured event so far;
the hunt continues passively.

Remaining open from the plan: variant (b) (both APs unreachable → pure
beacon loss into the timer/flag machinery) is not triggerable from the
router UI alone (needs the repeater down too); the log-rotation fix on
doorpi still pending (1 GB log, ~6 days headroom; needs sudo).

## Event taxonomy from the Speedport log (2026-11-06 (13))

The router's log (~/speedportlog.txt, user-exported) maps doorpi's
associations across the whole wedge history and regrades it:

| event | router-side story | verdict |
|-------|-------------------|---------|
| Nov 4 21:06 (dmesg "wedge" 1) | **nothing at all** — no doorpi roam/logout that evening; this was the day of the manual rtl8xxxu switch | manual driver work; the "Disabling MAC timed out" is likely **routine unload noise**, pending confirmation on a clean unload |
| Nov 5 07:06–07:10 (wedge 2) | doorpi logged off 07:06:19 (no router trigger logged), re-logged-in **to the router** 07:09:33, DHCP renewed 07:09:35 — then watchdog reload 07:10:21 | **the one unresolved event**: associated directly to the gateway with a fresh lease, pings failing ~45 s later. Either the real stick-side data-path wedge, or a second unlogged router beacon blip |
| Nov 6 01:09–01:26 (wedge 3) | beacon stop (capture-proven) → roam to repeater → repeater backhaul dead | fully explained, router/repeater-side |

Two systematic observations: (1) the Speedport logs nothing for the
beacon blips that start these chains — unlogged PHY-level hiccups (or
unlogged background channel scans — the leading environmental theory:
periodic auto-channel scans pause beaconing; 3 same-SSID mesh APs +
crowded 2.4 GHz) ; (2) both roam-away events (Nov 5 07:06, Nov 6 01:09)
had no logged router trigger, so either the router blips ~2×/day or the
stick's beacon-loss machinery false-fires at 10 cm — still open.

Consequence: the stress series (repeated router restarts) now has a
sharper target — reproduce the **Nov-5 shape** (re-associate to the
router, then data path dead) with full instrumentation. And every
future event gets one more check: if the stick is associated to the
ROUTER and the gateway is unreachable, that is stick-side until proven
otherwise (no repeater in the path).

## Open leads

1. ~~Recursive descent in tools/dis8051.py~~ **done 2026-11-05 (2)**, incl.
   function census → analysis/functions.tsv and the dispatch correction
   above.
2. ~~Second H2C dispatch~~ **closed**: no second dispatch. cmds 0/15/24/48
   → default (ack-and-ignore); cmd 7 ignored silently. Residual: read the
   real RSVDPAGE handler (0x7552) — the first pass never actually did.
3. ~~Name the six Linux-unknown commands~~ **done 2026-11-05 (3)**, and the
   param-block source residual **resolved 2026-11-06** (see "H2C parameter
   staging"): the handler pointer is always `&0x8AD2`, filled with raw
   mailbox bytes — rtlwifi's literal arguments are fully compatible, and
   the EXT layout matches the driver's exactly. Remaining soft spots: cmd
   44's functional name (checksum offload?), cmd 75's purpose, and the
   all-zero per-id limit table at 0x41BA.
4. ~~Interrupt architecture~~ **done 2026-11-06 (2)**: per-source FWISR
   map (above), the FWISR0.b5 clock-off spin at 0x62F1, and the EA-off
   audit verdict (image code clean; risk = boot ROM + spin points).
   Remaining small: name the individual FWISR sources properly (RX vs
   USB vs timer vs beacon — the latch-copy and callee evidence gives
   strong hints but no definitive names), and confirm the host can read
   0x01C4/0x01C5 on a wedged stick (add to doorpi's capture).
5. Group-key/rekey path: correlate with the field observation that outages
   cluster after the AP's hourly group rekey.
6. 8051 emulator: **v1 works (2026-11-06 (3))** — validated the whole H2C
   model dynamically (above). Next: interrupt injection, stateful ROM
   stubs, then replay of the pre-hang usbmon trace from doorpi (capture
   live since 2026-11-05, see doorpi's PI.md).

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
