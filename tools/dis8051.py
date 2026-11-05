#!/usr/bin/env python3
"""Table-driven MCS-51 (8051) disassembler, written for Realtek rtl8192cu
MAC firmware analysis. Linear sweep, two passes (xref labels).

Usage:
  python3 -I dis8051.py IMG [--base 0x4000] [--out FILE] [--stats-only]
"""
import argparse
import collections
import sys

FSIZE = {'i8': 1, 'i16': 2, 'bit': 1, 'dir': 1, 'rel': 1, 'a11': 1, 'a16': 2}
T = {}


def _t(op, mne, fields=()):
    T[op] = (mne, tuple(fields), 1 + sum(FSIZE[f] for f in fields))


# --- base page ---------------------------------------------------------
_t(0x00, 'NOP'); _t(0x01, 'AJMP {a11}', ['a11']); _t(0x02, 'LJMP {a16}', ['a16'])
_t(0x03, 'RR A'); _t(0x04, 'INC A'); _t(0x05, 'INC {dir}', ['dir'])
_t(0x06, 'INC @R0'); _t(0x07, 'INC @R1')
for i in range(8): _t(0x08 + i, f'INC R{i}')
_t(0x10, 'JBC {bit},{rel}', ['bit', 'rel']); _t(0x11, 'ACALL {a11}', ['a11'])
_t(0x12, 'LCALL {a16}', ['a16']); _t(0x13, 'RRC A'); _t(0x14, 'DEC A')
_t(0x15, 'DEC {dir}', ['dir']); _t(0x16, 'DEC @R0'); _t(0x17, 'DEC @R1')
for i in range(8): _t(0x18 + i, f'DEC R{i}')
_t(0x20, 'JB {bit},{rel}', ['bit', 'rel']); _t(0x21, 'AJMP {a11}', ['a11'])
_t(0x22, 'RET'); _t(0x23, 'RL A'); _t(0x24, 'ADD A,#{i8}', ['i8'])
_t(0x25, 'ADD A,{dir}', ['dir']); _t(0x26, 'ADD A,@R0'); _t(0x27, 'ADD A,@R1')
for i in range(8): _t(0x28 + i, f'ADD A,R{i}')
_t(0x30, 'JNB {bit},{rel}', ['bit', 'rel']); _t(0x31, 'ACALL {a11}', ['a11'])
_t(0x32, 'RETI'); _t(0x33, 'RLC A'); _t(0x34, 'ADDC A,#{i8}', ['i8'])
_t(0x35, 'ADDC A,{dir}', ['dir']); _t(0x36, 'ADDC A,@R0'); _t(0x37, 'ADDC A,@R1')
for i in range(8): _t(0x38 + i, f'ADDC A,R{i}')
_t(0x40, 'JC {rel}', ['rel']); _t(0x41, 'AJMP {a11}', ['a11'])
_t(0x42, 'ORL {dir},A', ['dir']); _t(0x43, 'ORL {dir},#{i8}', ['dir', 'i8'])
_t(0x44, 'ORL A,#{i8}', ['i8']); _t(0x45, 'ORL A,{dir}', ['dir'])
_t(0x46, 'ORL A,@R0'); _t(0x47, 'ORL A,@R1')
for i in range(8): _t(0x48 + i, f'ORL A,R{i}')
_t(0x50, 'JNC {rel}', ['rel']); _t(0x51, 'ACALL {a11}', ['a11'])
_t(0x52, 'ANL {dir},A', ['dir']); _t(0x53, 'ANL {dir},#{i8}', ['dir', 'i8'])
_t(0x54, 'ANL A,#{i8}', ['i8']); _t(0x55, 'ANL A,{dir}', ['dir'])
_t(0x56, 'ANL A,@R0'); _t(0x57, 'ANL A,@R1')
for i in range(8): _t(0x58 + i, f'ANL A,R{i}')
_t(0x60, 'JZ {rel}', ['rel']); _t(0x61, 'AJMP {a11}', ['a11'])
_t(0x62, 'XRL {dir},A', ['dir']); _t(0x63, 'XRL {dir},#{i8}', ['dir', 'i8'])
_t(0x64, 'XRL A,#{i8}', ['i8']); _t(0x65, 'XRL A,{dir}', ['dir'])
_t(0x66, 'XRL A,@R0'); _t(0x67, 'XRL A,@R1')
for i in range(8): _t(0x68 + i, f'XRL A,R{i}')
_t(0x70, 'JNZ {rel}', ['rel']); _t(0x71, 'ACALL {a11}', ['a11'])
_t(0x72, 'ORL C,{bit}', ['bit']); _t(0x73, 'JMP @A+DPTR')
_t(0x74, 'MOV A,#{i8}', ['i8']); _t(0x75, 'MOV {dir},#{i8}', ['dir', 'i8'])
_t(0x76, 'MOV @R0,#{i8}', ['i8']); _t(0x77, 'MOV @R1,#{i8}', ['i8'])
for i in range(8): _t(0x78 + i, f'MOV R{i},#{{i8}}', ['i8'])
_t(0x80, 'SJMP {rel}', ['rel']); _t(0x81, 'AJMP {a11}', ['a11'])
_t(0x82, 'ANL C,{bit}', ['bit']); _t(0x83, 'MOVC A,@A+PC'); _t(0x84, 'DIV AB')
_t(0x85, 'MOV dst,src', ['dir', 'dir'])                # bytes are src,dst
_t(0x86, 'MOV {dir},@R0', ['dir']); _t(0x87, 'MOV {dir},@R1', ['dir'])
for i in range(8): _t(0x88 + i, f'MOV {{dir}},R{i}', ['dir'])
_t(0x90, 'MOV DPTR,#{i16}', ['i16']); _t(0x91, 'ACALL {a11}', ['a11'])
_t(0x92, 'MOV {bit},C', ['bit']); _t(0x93, 'MOVC A,@A+DPTR')
_t(0x94, 'SUBB A,#{i8}', ['i8']); _t(0x95, 'SUBB A,{dir}', ['dir'])
_t(0x96, 'SUBB A,@R0'); _t(0x97, 'SUBB A,@R1')
for i in range(8): _t(0x98 + i, f'SUBB A,R{i}')
_t(0xA0, 'ORL C,/{bit}', ['bit']); _t(0xA1, 'AJMP {a11}', ['a11'])
_t(0xA2, 'MOV C,{bit}', ['bit']); _t(0xA3, 'INC DPTR'); _t(0xA4, 'MUL AB')
_t(0xA5, 'DB A5')                                      # undefined on stock 8051
_t(0xA6, 'MOV @R0,{dir}', ['dir']); _t(0xA7, 'MOV @R1,{dir}', ['dir'])
for i in range(8): _t(0xA8 + i, f'MOV R{i},{{dir}}', ['dir'])
_t(0xB0, 'ANL C,/{bit}', ['bit']); _t(0xB1, 'ACALL {a11}', ['a11'])
_t(0xB2, 'CPL {bit}', ['bit']); _t(0xB3, 'CPL C')
_t(0xB4, 'CJNE A,#{i8},{rel}', ['i8', 'rel'])
_t(0xB5, 'CJNE A,{dir},{rel}', ['dir', 'rel'])
_t(0xB6, 'CJNE @R0,#{i8},{rel}', ['i8', 'rel'])
_t(0xB7, 'CJNE @R1,#{i8},{rel}', ['i8', 'rel'])
for i in range(8): _t(0xB8 + i, f'CJNE R{i},#{{i8}},{{rel}}', ['i8', 'rel'])
_t(0xC0, 'PUSH {dir}', ['dir']); _t(0xC1, 'AJMP {a11}', ['a11'])
_t(0xC2, 'CLR {bit}', ['bit']); _t(0xC3, 'CLR C'); _t(0xC4, 'SWAP A')
_t(0xC5, 'XCH A,{dir}', ['dir']); _t(0xC6, 'XCH A,@R0'); _t(0xC7, 'XCH A,@R1')
for i in range(8): _t(0xC8 + i, f'XCH A,R{i}')
_t(0xD0, 'POP {dir}', ['dir']); _t(0xD1, 'ACALL {a11}', ['a11'])
_t(0xD2, 'SETB {bit}', ['bit']); _t(0xD3, 'SETB C'); _t(0xD4, 'DA A')
_t(0xD5, 'DJNZ {dir},{rel}', ['dir', 'rel'])
_t(0xD6, 'XCHD A,@R0'); _t(0xD7, 'XCHD A,@R1')
for i in range(8): _t(0xD8 + i, f'DJNZ R{i},{{rel}}', ['rel'])
_t(0xE0, 'MOVX A,@DPTR'); _t(0xE1, 'AJMP {a11}', ['a11'])
_t(0xE2, 'MOVX A,@R0'); _t(0xE3, 'MOVX A,@R1'); _t(0xE4, 'CLR A')
_t(0xE5, 'MOV A,{dir}', ['dir']); _t(0xE6, 'MOV A,@R0'); _t(0xE7, 'MOV A,@R1')
for i in range(8): _t(0xE8 + i, f'MOV A,R{i}')
_t(0xF0, 'MOVX @DPTR,A'); _t(0xF1, 'ACALL {a11}', ['a11'])
_t(0xF2, 'MOVX @R0,A'); _t(0xF3, 'MOVX @R1,A'); _t(0xF4, 'CPL A')
_t(0xF5, 'MOV {dir},A', ['dir']); _t(0xF6, 'MOV @R0,A'); _t(0xF7, 'MOV @R1,A')
for i in range(8): _t(0xF8 + i, f'MOV R{i},A')

SFR = {0x80: 'P0', 0x81: 'SP', 0x82: 'DPL', 0x83: 'DPH', 0x87: 'PCON',
       0x88: 'TCON', 0x89: 'TMOD', 0x8A: 'TL0', 0x8B: 'TL1', 0x8C: 'TH0',
       0x8D: 'TH1', 0x90: 'P1', 0x98: 'SCON', 0x99: 'SBUF', 0xA0: 'P2',
       0xA8: 'IE', 0xB0: 'P3', 0xB8: 'IP', 0xD0: 'PSW', 0xE0: 'ACC', 0xF0: 'B'}


class Insn:
    __slots__ = ('addr', 'raw', 'text', 'length', 'target', 'is_call')

    def __init__(self, addr, raw, text, length, target=None, is_call=False):
        self.addr, self.raw, self.text = addr, raw, text
        self.length, self.target, self.is_call = length, target, is_call


def sfr(v):
    return f'{v:02X}' + (f'({SFR[v]})' if v in SFR else '')


def decode(img, addr, base):
    off = addr - base
    if not 0 <= off < len(img):
        return None
    op = img[off]
    mne, fields, length = T[op]
    if off + length > len(img):
        return None
    raw = img[off:off + length]
    vals, target, is_call = [], None, False
    pos = 1
    for f in fields:
        if f in ('i8', 'bit', 'dir', 'rel', 'a11'):
            vals.append(raw[pos]); pos += 1
        else:                                   # i16 / a16 (big-endian: high, low)
            vals.append((raw[pos] << 8) | raw[pos + 1]); pos += 2
    text = mne
    for f, v in zip(fields, vals):
        if f == 'rel':
            continue
        text = text.replace('{%s}' % f, f'{v:04X}' if f in ('i16', 'a16') else f'{v:02X}')
    # computed targets
    if fields and fields[-1] == 'rel':
        target = addr + length + (raw[-1] - 256 if raw[-1] > 127 else raw[-1])
        text = text.replace('{rel}', f'{target:04X}')
    elif op in (0x02, 0x12):                    # LJMP / LCALL (high byte first)
        target = (raw[1] << 8) | raw[2]
        is_call = op == 0x12
    elif (op & 0x0F) in (0x01, 0x11):           # AJMP / ACALL
        target = ((addr + 2) & 0xF800) | ((op & 0xE0) << 3) | raw[1]
        is_call = (op & 0x0F) == 0x11
        text = mne.replace('{a11}', f'{target:04X}')
    if op == 0x85:                              # MOV direct,direct: bytes src,dst
        text = f'MOV {sfr(raw[2])}, {sfr(raw[1])}'
    return Insn(addr, raw, text, length, target, is_call)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('img')
    ap.add_argument('--base', type=lambda x: int(x, 0), default=0x4000)
    ap.add_argument('--out')
    ap.add_argument('--stats-only', action='store_true')
    args = ap.parse_args()
    with open(args.img, 'rb') as f:
        img = f.read()
    n = len(img)

    insns = {}
    a = args.base
    while a - args.base < n:
        ins = decode(img, a, args.base)
        if not ins:
            break
        insns[a] = ins
        a += ins.length

    calls, all_t = collections.Counter(), set()
    for ins in insns.values():
        if ins.target is not None:
            all_t.add(ins.target)
            if ins.is_call:
                calls[ins.target] += 1
    in_img = [t for t in all_t if args.base <= t < args.base + n]
    on_b = [t for t in in_img if t in insns]
    # AJMP/ACALL targets depend on the instruction's own address (2 KB page),
    # so they are the sensitive test for a wrong base.
    a11 = [i.target for i in insns.values() if i.target is not None
           and i.raw[0] not in (0x02, 0x12)
           and (i.raw[0] & 0x0F) in (0x01, 0x11)]
    a11_in = [t for t in a11 if args.base <= t < args.base + n]
    a11_b = [t for t in a11_in if t in insns]
    print(f'base={args.base:#06x}  instructions={len(insns)}  '
          f'bytes={sum(i.length for i in insns.values())}/{n}')
    print(f'xref targets: {len(all_t)} total, {len(in_img)} in-image '
          f'({100*len(in_img)/max(1,len(all_t)):.0f}%), '
          f'{len(on_b)}/{len(in_img)} on boundary')
    print(f'AJMP/ACALL: {len(a11)} targets, {len(a11_in)} in-image, '
          f'{len(a11_b)} on boundary ({100*len(a11_b)/max(1,len(a11)):.0f}%)')
    if args.stats_only:
        for t, c in calls.most_common(15):
            print(f'  {t:04X}: {c} calls' + ('' if t in insns else '  OFF-BOUNDARY'))
        return

    lines = []
    for addr in sorted(insns):
        ins = insns[addr]
        lbl = ''
        if addr in calls:
            lbl = f'FUNC_{addr:04X}:'
        elif addr in all_t:
            lbl = f'LOC_{addr:04X}:'
        hexb = ' '.join(f'{b:02X}' for b in ins.raw).ljust(9)
        lines.append(f'{addr:04X}  {lbl:<14}{hexb} {ins.text}')
    out = '\n'.join(lines) + '\n'
    if args.out:
        with open(args.out, 'w') as f:
            f.write(out)
        print(f'wrote {len(lines)} lines to {args.out}')
    else:
        sys.stdout.write(out)


if __name__ == '__main__':
    main()
