---
title: "Errata"
redirect_from:
  - /analysis/ERRATA.md
---

# Errata

Claims made during the investigation that were later corrected. Kept
for transparency; each entry cites the final correct state.

1. **Dispatch table pairing (first pass).** Hand-read from a desynced
   linear sweep, every id was paired with the wrong handler (shifted by
   one trampoline hop). Fixed by recursive descent + mechanical table
   parsing; SETPWRMODE(1) and MACID_PS_MODE(7) were "missing" only in
   the misread table.

2. **"Second H2C dispatch".** Did not exist. The 0x5002 switch is a
   bitmask TX/queue scheduler (ROM-entered), and the 0x6C30 cluster is
   the dispatch staging feed.

3. **seq_lut tables "decoded from zero padding".** Wrong. The
   0x4050–0x41D5 descent gap holds the rate-adaptation data tables
   (318 non-zero bytes, byte-verified). Static descent cannot reach
   register-computed MOVC reads. Lesson: **a descent gap is only
   padding after a byte check.**

4. **0x41BA limit table "all-zero".** Wrong; it holds live RA limits
   1–12. (Same root cause as #3.)

5. **"Roam-only rate divergence" (emulator).** An invalid control
   (out-of-ring box write — see firmware.md, ring-strict service)
   produced a spurious 1700-vs-346 step comparison and an apparent
   roam-only INIDATA_RATE_SEL write. Withdrawn: RA_MASK always
   programs it.

6. **Nov 6 01:09 event initially narrated as a firmware wedge.**
   Reclassified as environment-side: the watchdog's own log shows the
   gateway reachable through the "outage" window and the capture shows
   7,031 data frames flowing; the traffic drop was the quieter
   repeater BSS. The AP-P beacon stop at 01:09:27.7 (capture-proven)
   closed the case.

7. **"8051 hang" wedge model.** The H2C path stayed responsive through
   the entire 16-minute Nov 6 event (all 771 mailbox reads returned
   0x00). The hang model applies only to the three intentional spin
   points (0x4503/0x4506 error traps, 0x62F1 clock-off park).

8. **Vector table "two slots".** The vector page has four entries
   (0x4000, 0x4013, 0x4043, 0x404B); the scan window was too small.
   Also corrected an off-by-one in a reader report (0x4042 → 0x4043).

9. **RA worker trigger "FWISR1.b1".** Byte-verified as FWISR2.b1
   (handler reads the masked byte-2 copy, IDATA 0x2E, and W1Cs
   0x0136). All seven enabled interrupt sources now have handlers; no
   orphans.

10. **lib_reg_read / lib_reg_cpy mislabels.** 0x4220 is a Keil
    generic-pointer read (register read only in the null-pointer
    special case); 0x435F is `DPTR += A*B`. Several first-pass
    "register accesses" inside H2C handlers are param-block reads.

11. **"Calibration wipe" root cause (Nov 10–12).** The all-0xFF
    cluster at 0x0DA2-0x0DA9/0x0F80+ was interpreted as power-domain
    state loss because no software writes those registers. External
    review (2026-11-12) showed they are 16-bit OFDM PHY error
    counters (ROFDM_PHYCOUNTER1/2/3) saturating at 0xFFFF after ~1 h
    — the "no software writer" proof was vacuous because the PHY
    hardware writes them. The signature-based auto-reload fired 31
    times on a healthy link (watchdog journal, Nov 10 16:39 – Nov 12
    08:51; first reported as 35, a miscount). Root cause of the real Nov 10 wedge is
    open again. Lesson recorded: the "name the cluster via the
    datasheet" step flagged as optional was the cheap falsification
    that would have prevented this.
