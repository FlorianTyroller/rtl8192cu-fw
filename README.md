# rtl8192cu-fw — reverse engineering the Realtek RTL8188CUS WiFi firmware

Documentation of the 8051 firmware inside the Realtek RTL8188CUS /
RTL8192CU WiFi chips — the MAC of the ubiquitous Edimax EW-7811Un USB
stick (the Raspberry Pi WiFi adapter of 2012–2015) and countless
others.

The firmware blob (`rtl8192cufw*.bin` from linux-firmware) is a closed
16 KB 8051 binary. This project disassembles, maps, and documents it:
container format, memory map, the full host↔firmware command protocol,
interrupt architecture, power-save and rate-adaptation engines, and the
runtime structure of a Keil C51 build from December 2012. An emulator
replays captured command sequences against the image.

**Nothing here redistributes Realtek's firmware.** The blobs are
fetched from your own linux-firmware installation
(`tools/fetch-firmware.sh`); the tools then disassemble them locally.
Published are only analysis, annotations, and code (see
[Legal](#legal)).

## Documentation

| document | content |
|----------|---------|
| [analysis/firmware.md](analysis/firmware.md) | Factual reference: container, memory map, H2C/C2H protocols, interrupts, state engines, rate adaptation, ROM interface, diagnostics |
| [analysis/wedge.md](analysis/wedge.md) | The field investigation: capture setup, event analysis, discriminators, emulator evidence |
| [analysis/ERRATA.md](analysis/ERRATA.md) | Corrected claims, kept for transparency |
| [analysis/annotations.tsv](analysis/annotations.tsv) | Symbol database, confidence-tagged |
| [analysis/annotations-b.tsv](analysis/annotations-b.tsv) | B-cut symbol map |
| [analysis/functions.tsv](analysis/functions.tsv) | Function census (104 functions, every byte accounted) |

## What we know, in brief

- Three firmware variants (A, B, TMSC) built in three consecutive
  minutes on 5 Dec 2012; the library block is address-identical across
  builds — the boot ROM calls into the image at fixed addresses.
- Every host↔firmware channel is documented: H2C (mailbox + EXT +
  staging + dispatch + all 15 handlers, including six commands Linux
  never sends), C2H events, the ack register, the 8051 reset
  handshake, and a host-readable diagnostics register.
- All seven active interrupt sources are named. The TX power/queue
  state engine, power-save engine, and both halves of rate adaptation
  are decoded.
- The field-wedge investigation: every captured outage is explained by
  the radio environment; one lab-reproduced firmware failure mode (an
  unpausable TX pause with a real code path behind it) awaits field
  confirmation.

## Quick start

    tools/fetch-firmware.sh
    tail -c +33 blobs/rtl8192cufw_TMSC.bin > /tmp/tmsc.img
    python3 -I tools/dis8051.py /tmp/tmsc.img --base 0x4000 --out /tmp/tmsc.dis
    python3 -I tools/sim8051.py /tmp/tmsc.img --script tools/sim-scripts/roam-burst.script

Tool sources: [dis8051.py](tools/dis8051.py) ·
[sim8051.py](tools/sim8051.py) · [fwl.py](tools/fwl.py) ·
[fetch-firmware.sh](tools/fetch-firmware.sh) (directory browsing is on
[GitHub](https://github.com/FlorianTyroller/rtl8192cu-fw), not Pages).

## Why

These chips still fail in the field (any rtl8xxxu thread): the stick
silently stops passing traffic and recovers only on driver reload.
Understanding the firmware is step one toward understanding the
failure. This repo is the map; the territory belongs to Realtek.

## Legal

The firmware is Copyright Realtek, shipped under the linux-firmware
`binary-redist-Realtek-restrictive` license (binary redistribution
allowed, disassembly formally not). This repository therefore contains
**no firmware bytes and no full disassembly dumps** — only factual
documentation, short excerpts, and original tooling. Analysis of your
own lawfully obtained copy is your affair; nothing here circumvents
any protection.

Licensing of this repository's own material:
- code in `tools/` — MIT (see [LICENSE](LICENSE))
- documentation and analysis — CC-BY-4.0
