#!/usr/bin/env python3
"""Table-driven MCS-51 (8051) disassembler, written for Realtek rtl8192cu
MAC firmware analysis.

Default mode is recursive descent from entry-point seeds: every byte reached
by control flow is code, Keil C51 switch tables (inline after LCALL to a
walker) are decoded as data, and everything else is reported as a gap.
Linear sweep (desyncs on inline tables) remains available as --linear.

Keil C51 switch idiom, as emitted by this compiler version:
    MOV DPTR,#imm16        ; or via registers
    LCALL walker           ; walker POPs its return address into DPTR:
    <table>                ;   the table sits INLINE right after the LCALL
The walker walks {target_hi, target_lo, case...} entries until target
0000, then jumps to the 2-byte default target that follows. Walker entry
sizes observed: 0x4394 -> 3 bytes (8-bit case), 0x43BA -> 4 bytes (16-bit
case in B:A). Extend/override with --walker ADDR:ENTRYSIZE.

Usage:
  python3 -I dis8051.py IMG [--base 0x4000] [--out FILE] [--stats-only]
                          [--linear] [--seed 0xADDR ...]
                          [--walker 0x4394:3,...] [--census FILE]
                          [--annotations FILE]
"""
import argparse
import collections
import re
import sys

FSIZE = {'i8': 1, 'i16': 2, 'bit': 1, 'dir': 1, 'rel': 1, 'a11': 1, 'a16': 2}
T = {}
REGS = {}  # value -> [names], loaded from kernel headers via --regs

DEFAULT_WALKERS = {0x4394: 3, 0x43BA: 4}
VECTOR_SCAN = 0x40  # scan this many header bytes for LJMP seeds
MAX_TABLE_ENTRIES = 128


def load_regs(paths):
    rx = re.compile(r'^#define\s+(REG_\w+)\s+0x([0-9A-Fa-f]{3,4})\b')
    for p in paths.split(','):
        try:
            with open(p) as f:
                for line in f:
                    m = rx.match(line.strip())
                    if m:
                        REGS.setdefault(int(m.group(2), 16), []).append(m.group(1))
        except OSError as e:
            print(f'warning: cannot read {p}: {e}', file=sys.stderr)


def load_annotations(path):
    """annotations.tsv -> {addr: symbol} for code rows."""
    names = {}
    try:
        with open(path) as f:
            for line in f:
                if line.startswith('#') or not line.strip():
                    continue
                parts = line.rstrip('\n').split('\t')
                if len(parts) >= 3 and parts[0] == 'code':
                    try:
                        names[int(parts[1], 16)] = parts[2]
                    except ValueError:
                        pass
    except OSError as e:
        print(f'warning: cannot read {path}: {e}', file=sys.stderr)
    return names


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
    if op == 0x90 and REGS:                     # MOV DPTR,#imm16 -> annotate
        val = vals[0]
        names = REGS.get(val)
        if names:
            text += '   ; ' + '|'.join(names[:3])
    return Insn(addr, raw, text, length, target, is_call)


# --- flow classification -------------------------------------------------

def flow_kind(ins):
    """'stop' | 'jump' (unconditional) | 'branch' (conditional) | 'call' | 'flow'"""
    op = ins.raw[0]
    if ins.is_call:
        return 'call'
    if op in (0x22, 0x32, 0x73):                # RET / RETI / JMP @A+DPTR
        return 'stop'
    if op == 0x02 or (op & 0x1F) == 0x01:       # LJMP / AJMP
        return 'jump'
    if op == 0x80:                              # SJMP
        return 'jump'
    if ins.target is not None:                  # conditional relative branch
        return 'branch'
    return 'flow'


def parse_switch_table(img, base, tbl, entsz):
    """Parse a Keil switch table at `tbl`. Returns (targets, end_addr) with
    the default target last, or None if it doesn't parse as a table."""
    off = tbl - base
    targets = []
    for _ in range(MAX_TABLE_ENTRIES):
        if off < 0 or off + 4 > len(img):
            return None
        thi, tlo = img[off], img[off + 1]
        if thi == 0 and tlo == 0:               # terminator + 2-byte default
            targets.append((img[off + 2] << 8) | img[off + 3])
            return targets, base + off + 4      # off is an image index
        targets.append((thi << 8) | tlo)
        off += entsz
    return None


def vector_seeds(img, base):
    """LJMP opcodes in the vector page are entry points. Returns
    (slot_addresses, targets) — slots must be decoded too (they are code)."""
    slots, targets = [], []
    for off in range(0, min(VECTOR_SCAN, len(img) - 2)):
        if img[off] == 0x02:
            t = (img[off + 1] << 8) | img[off + 2]
            if base <= t < base + len(img):
                slots.append(base + off)
                targets.append(t)
    return slots, targets


def descend(img, base, seeds, walkers):
    """Recursive descent. Returns (insns, tables, funcs, conflicts).
    tables: {tbl_start: (targets, end)}"""
    insns, tables, funcs = {}, {}, set()
    owner = {}                                  # byte addr -> insn start
    conflicts = 0
    stack = list(seeds)
    trail = set()                               # (from, to) visited edges
    while stack:
        a = stack.pop()
        while True:
            if not base <= a < base + len(img):  # external (boot ROM) target
                break
            if a in insns:
                break
            ins = decode(img, a, base)
            if not ins:
                break
            insns[a] = ins
            for b in range(a, a + ins.length):
                if owner.get(b, a) != a:
                    conflicts += 1
                owner.setdefault(b, a)
            nxt = a + ins.length
            kind = flow_kind(ins)
            if kind == 'call' and ins.target in walkers:
                # Walker: table is inline at the return address; execution
                # continues at a case target, never at the table end.
                funcs.add(ins.target)
                stack.append(ins.target)        # walker body is real code
                parsed = parse_switch_table(img, base, nxt, walkers[ins.target])
                if parsed:
                    targets, end = parsed
                    tables[nxt] = (targets, end)
                    for t in targets:
                        if base <= t < base + len(img):
                            stack.append(t)
                    break
                # fall through: treat as an ordinary call
            if kind == 'call':
                funcs.add(ins.target)
                stack.append(ins.target)
                a = nxt
                continue
            if kind in ('jump', 'branch'):
                edge = (a, ins.target)
                if edge not in trail:
                    trail.add(edge)
                    stack.append(ins.target)
                if kind == 'jump':
                    break
                a = nxt
                continue
            if kind == 'stop':
                break
            a = nxt
    return insns, tables, funcs, conflicts


def census(insns, funcs, calls, base, n, ann_names):
    """Function census rows: addr, end, size, callers, term, name.
    Out-of-image callees (boot ROM) get end/size '-' and term 'rom'."""
    rows = []
    for f in sorted(funcs):
        if not base <= f < base + n:
            rows.append((f, 0, 0, calls.get(f, 0), 'rom', ann_names.get(f, '')))
    entries = sorted(f for f in funcs if base <= f < base + n)
    for i, f in enumerate(entries):
        end = entries[i + 1] if i + 1 < len(entries) else base + n
        body = [a for a in insns if f <= a < end]
        term = 'none'
        if body:
            last = insns[max(body)]
            op = last.raw[0]
            if op == 0x22:
                term = 'ret'
            elif op == 0x32:
                term = 'reti'
            elif op == 0x73:
                term = 'indirect'
            elif last.target is not None:
                term = 'jmp' if f <= last.target < end else 'jmpout'
            else:
                term = 'fall'
        rows.append((f, end, end - f, calls.get(f, 0), term,
                     ann_names.get(f, '')))
    return sorted(rows)


TERM_NAMES = {'ret': 'RET/RETI', 'reti': 'RETI', 'jmp': 'jumps within',
              'jmpout': 'jumps out', 'fall': 'falls into next',
              'indirect': 'JMP @A+DPTR', 'none': 'no body'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('img')
    ap.add_argument('--base', type=lambda x: int(x, 0), default=0x4000)
    ap.add_argument('--out')
    ap.add_argument('--regs', help='comma-separated kernel headers with #define REG_* 0xVAL')
    ap.add_argument('--stats-only', action='store_true')
    ap.add_argument('--linear', action='store_true',
                    help='old linear sweep (desyncs on inline tables)')
    ap.add_argument('--seed', action='append', default=[],
                    type=lambda x: int(x, 0), help='extra descent root (hex)')
    ap.add_argument('--walker', action='append', default=[],
                    help='ADDR:ENTRYSIZE switch walker, e.g. 0x4394:3')
    ap.add_argument('--census', help='write function census TSV to FILE')
    ap.add_argument('--annotations', default='analysis/annotations.tsv',
                    help='symbol names for census (functions unlabeled if missing)')
    args = ap.parse_args()
    if args.regs:
        load_regs(args.regs)
    walkers = dict(DEFAULT_WALKERS)
    for w in args.walker:
        a, _, s = w.partition(':')
        walkers[int(a, 0)] = int(s, 0)
    with open(args.img, 'rb') as f:
        img = f.read()
    n = len(img)
    ann_names = load_annotations(args.annotations) if args.census else {}

    if args.linear:
        insns = {}
        a = args.base
        while a - args.base < n:
            ins = decode(img, a, args.base)
            if not ins:
                break
            insns[a] = ins
            a += ins.length
        tables, funcs, conflicts = {}, set(), 0
        for ins in insns.values():
            if ins.is_call:
                funcs.add(ins.target)
    else:
        slots, vtargets = vector_seeds(img, args.base)
        roots = slots + vtargets + args.seed
        if not roots:
            sys.exit('no roots: pass --seed')
        insns, tables, funcs, conflicts = descend(img, args.base, roots, walkers)

    calls, all_t = collections.Counter(), set()
    for ins in insns.values():
        if ins.target is not None:
            all_t.add(ins.target)
            if ins.is_call:
                calls[ins.target] += 1
    code_bytes = sum(i.length for i in insns.values())
    tbl_bytes = sum(e - s for s, (_, e) in tables.items())
    print(f'base={args.base:#06x}  mode={"linear" if args.linear else "descent"}  '
          f'instructions={len(insns)}  code bytes={code_bytes}/{n}')
    if not args.linear:
        print(f'switch tables: {len(tables)} ({tbl_bytes} B), cases: '
              f'{sum(len(t) for t, _ in tables.values())}, '
                  f'functions: {len(funcs)}, byte conflicts: {conflicts}')
        gaps = n - code_bytes - tbl_bytes
        print(f'data gaps (unreached bytes): {gaps}')
    in_img = [t for t in all_t if args.base <= t < args.base + n]
    on_b = [t for t in in_img if t in insns]
    print(f'xref targets: {len(all_t)} total, {len(in_img)} in-image, '
          f'{len(on_b)}/{len(in_img)} on boundary')
    if args.stats_only:
        for t, c in calls.most_common(15):
            print(f'  {t:04X}: {c} calls' + ('' if t in insns else '  OFF-BOUNDARY'))
        return

    # --- render ---
    occupied = {}                               # byte -> kind ('i'+start / 'tbl' / None)
    for ins in insns.values():
        for b in range(ins.addr, ins.addr + ins.length):
            occupied[b] = ins.addr
    for s, (_, e) in tables.items():
        for b in range(s, e):
            occupied.setdefault(b, 'tbl')

    lines = []
    a = args.base
    while a < args.base + n:
        off = a - args.base
        if a in insns:
            ins = insns[a]
            lbl = ''
            if a in funcs and a in calls:
                lbl = f'FUNC_{a:04X}:'
            elif a in funcs:
                lbl = f'FUNC_{a:04X}:'        # seed / vector target, no callers
            elif a in all_t:
                lbl = f'LOC_{a:04X}:'
            hexb = ' '.join(f'{b:02X}' for b in ins.raw).ljust(9)
            lines.append(f'{a:04X}  {lbl:<14}{hexb} {ins.text}')
            a += ins.length
        elif occupied.get(a) == 'tbl':
            s = a
            while occupied.get(a) == 'tbl':
                a += 1
            tgt, end = tables[s]
            lines.append(f'{s:04X}  {"":<14}            ; switch table '
                         f'({len(tgt)} cases + default), cases -> '
                         + ' '.join(f'{t:04X}' for t in tgt))
            for chunk in range(s, a, 8):
                b = img[chunk - args.base:min(chunk + 8, a) - args.base]
                lines.append(f'{chunk:04X}  {"":<14}'
                             + ' '.join(f'{x:02X}' for x in b))
        else:
            s = a
            while a < args.base + n and occupied.get(a) is None:
                a += 1
            lines.append(f'{s:04X}  {"":<14}            ; gap '
                         f'({a - s} B unreached)')
            for chunk in range(s, a, 8):
                b = img[chunk - args.base:min(chunk + 8, a) - args.base]
                lines.append(f'{chunk:04X}  {"":<14}'
                             + ' '.join(f'{x:02X}' for x in b))
    out = '\n'.join(lines) + '\n'
    if args.out:
        with open(args.out, 'w') as f:
            f.write(out)
        print(f'wrote {len(lines)} lines to {args.out}')
    else:
        sys.stdout.write(out)

    if args.census:
        rows = census(insns, funcs, calls, args.base, n, ann_names)
        with open(args.census, 'w') as f:
            f.write('# Function census (recursive descent, TMSC 88.2)\n')
            f.write('# addr\tend\tsize\tcallers\tterm\tname\n')
            for r in rows:
                end = f'{r[1]:04X}' if r[1] else '----'
                f.write(f'{r[0]:04X}\t{end}\t{r[2]}\t{r[3]}\t{r[4]}\t{r[5]}\n')
        print(f'wrote {len(rows)} functions to {args.census}')


if __name__ == '__main__':
    main()
