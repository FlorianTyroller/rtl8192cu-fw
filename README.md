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
  0x0000–0x3FFF (called into, not readable), MAC registers in XDATA
  0x0000–0x0FFF, fw globals at XDATA 0x8Axx.
- `main()` at 0x50C6; H2C command poller at 0x5560 polling register 0x01CC,
  dispatching through a per-state allowed-command bitmask.

Full detail: [analysis/findings.md](analysis/findings.md) and the symbol
database [analysis/annotations.tsv](analysis/annotations.tsv). Tool sources:
[dis8051.py](tools/dis8051.py) · [fwl.py](tools/fwl.py) ·
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
