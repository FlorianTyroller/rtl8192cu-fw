# rtl8192cu-fw — reverse engineering the Realtek RTL8188CUS WiFi firmware

Documentation of the 8051 firmware that runs inside the Realtek RTL8188CUS /
RTL8192CU WiFi chips — the brain of the ubiquitous Edimax EW-7811Un USB stick
(the *the* Raspberry Pi WiFi adapter of 2012–2015) and countless others.

The firmware blob (`rtl8192cufw*.bin` from linux-firmware) is a closed,
undocumented 16 KB 8051 binary. This project disassembles, maps and documents
it: container format, memory map, entry points, the H2C (host-to-chip) command
dispatcher, and the runtime structure of a Keil C51 build from December 2012.

**Nothing here redistributes Realtek's firmware.** The blobs are fetched onto
your machine from your own linux-firmware installation (`tools/fetch-firmware.sh`);
our tools then disassemble them locally. Published are only our own analysis,
annotations and code (see [Legal](#legal)).

## What we know so far (short version)

- Container: 32-byte header — signature (A-cut/B-cut), version, BCD build
  timestamp, payload size, and Realtek's internal SVN revision (137364).
- Three variants (A, B, TMSC) were built in three consecutive minutes on
  5 Dec 2012, 17:10–17:12.
- 8051, Keil C51 runtime; code RAM at 0x4000–0x7FFF, boot ROM at
  0x0000–0x3FFF (owns the real interrupt vectors and calls into the
  image), MAC registers in XDATA 0x0000–0x0FFF, fw RAM at 0x8xxx.
- Full recursive-descent code map: 104 functions, 5 switch tables, every
  non-code byte accounted for (the 0x4050–0x41D5 "gap" holds the RA data
  tables — read via register-computed MOVC, invisible to static descent).
- Every host↔fw channel documented: H2C (mailbox + EXT + staging +
  dispatch + all 15 handlers, both Linux-known and Windows-only), C2H
  event channel (0x01A0/0x01AF/0x01B8/0x01CB), the ack register 0x01C6
  (5 known bits), the reset handshake (FWIMR→0x01CF→clock-off), and the
  diagnostic indicator 0x01C4 (main-loop heartbeat, per-handler stamps).
- Interrupt architecture complete: 7 active FWISR sources all named,
  timer-int block per-case, TX power/queue state engine (7 states, 12
  transitions — incl. the power-vetoed unpause that can legitimately
  leave TX paused), power-save engine, and the full rate-adaptation
  engine (32 macid slots, fallback chains, retry thresholds, mode
  classes).
- The wedge story: every captured field event explained as
  environment-side; one lab-reproduced fw failure mode (unpausable TX
  state) with its real-code veto mechanism identified; emulator
  (sim8051.py) can replay captured H2C sequences against the image.

Full detail: [analysis/findings.md](analysis/findings.md), the symbol
database [analysis/annotations.tsv](analysis/annotations.tsv) and the
function census [analysis/functions.tsv](analysis/functions.tsv). Tool
sources: [dis8051.py](tools/dis8051.py) · [fwl.py](tools/fwl.py) ·
[fetch-firmware.sh](tools/fetch-firmware.sh) (directory browsing is on
[GitHub](https://github.com/FlorianTyroller/rtl8192cu-fw), not Pages).

## Quick start

    tools/fetch-firmware.sh          # copies rtl8192cufw*.bin into blobs/
    tail -c +33 blobs/rtl8192cufw_TMSC.bin > blobs/tmsc.img
    python3 -I tools/dis8051.py blobs/tmsc.img --base 0x4000 --out /tmp/tmsc.dis

## Why

These chips still wedge in the field (see any rtl8xxxu thread): the stick
silently stops receiving, recovers only on driver reload. Understanding the
firmware is step one toward understanding the wedge. This repo is the map;
the territory belongs to Realtek.

## Legal

The firmware is Copyright Realtek, shipped under the linux-firmware
`binary-redist-Realtek-restrictive` license (binary redistribution allowed,
disassembly formally not). This repository therefore contains **no firmware
bytes and no full disassembly dumps** — only factual documentation, short
excerpts, and original tooling. That keeps the analysis useful and the
redistribution surface clean. Analysis of your own lawfully obtained copy
is your affair; nothing here circumvents any protection.

Licensing of this repository's own material:
- code in `tools/` — MIT (see [LICENSE](LICENSE))
- documentation and analysis — CC-BY-4.0
