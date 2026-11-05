#!/usr/bin/env python3
"""Parse Realtek rtl8192cu firmware containers: header, layout, variant diff.

Usage: python3 -I fwl.py FILE [FILE ...]
"""
import collections
import math
import sys

HDR = 0x20  # payload start hypothesis (imem_size = filesize - 32 supports it)


def u16(b, o):
    return int.from_bytes(b[o:o + 2], 'little')


def u32(b, o):
    return int.from_bytes(b[o:o + 4], 'little')


def entropy(data):
    if not data:
        return 0.0
    c = collections.Counter(data)
    n = len(data)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def hdr_dump(b, name):
    print(f"== {name} ({len(b)} bytes) ==")
    print(f"  signature  {u16(b,0):#06x}   category {b[2]:#04x}  function {b[3]:#04x}")
    print(f"  version    {u16(b,4)}.{b[6]}   (dmesg said 88.2, sig 0x88c1)")
    print(f"  bytes 8-11 {b[8]:02x} {b[9]:02x} {b[10]:02x} {b[11]:02x}   (build date 20{b[8]:02x}-{b[9]:02x}-{b[10]:02x}?)")
    for off in (0x0c, 0x10, 0x14, 0x18):
        print(f"  u32@{off:#04x}    {u32(b,off):#010x} ({u32(b,off)})")
    payload = b[HDR:]
    print(f"  payload    {len(payload)} bytes @ {HDR:#x}, entropy {entropy(payload):.2f} bits/byte")
    # code-ness checks: 8051 LJMP (0x02) density, top bytes
    lj = sum(1 for x in payload if x == 0x02)
    print(f"  0x02 bytes in payload: {lj} ({100*lj/len(payload):.1f}%)")
    top = collections.Counter(payload).most_common(8)
    print(f"  top bytes: {' '.join(f'{k:#04x}:{v}' for k, v in top)}")
    # first 0x40 bytes of payload as LJMP candidates (vector table)
    print(f"  payload[0:0x40]:")
    for i in range(0, 0x40, 16):
        row = payload[i:i + 16]
        print(f"    {i:#06x}: " + ' '.join(f'{x:02x}' for x in row))
    return payload


def diff_same_len(a, b, na, nb):
    """Same length: list differing regions."""
    diffs = [i for i in range(len(a)) if a[i] != b[i]]
    if not diffs:
        print(f"  {na} == {nb}")
        return
    # group into runs
    runs, start, prev = [], diffs[0], diffs[0]
    for i in diffs[1:]:
        if i > prev + 16:
            runs.append((start, prev))
            start = i
        prev = i
    runs.append((start, prev))
    print(f"  {na} vs {nb}: {len(diffs)} bytes differ in {len(runs)} regions:")
    for s, e in runs[:20]:
        print(f"    {s:#06x}-{e:#06x} ({e-s+1} B)")


def diff_indel(a, b, na, nb):
    """Different length: try to explain as one insertion/deletion."""
    d = len(a) - len(b)
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    print(f"  {na}({len(a)}) vs {nb}({len(b)}): len delta {d}, first diff at {i:#x}")
    found = False
    for p in range(max(0, i - 8), min(len(b), i + len(a) - len(b) + 8)):
        # hypothesis: b == a with d bytes removed at p
        if b[:p] == a[:p] and b[p:] == a[p + d:]:
            print(f"    clean single deletion of {d} bytes at {na}[{p:#x}]")
            found = True
            break
    if not found:
        print("    not a single indel; tail comparison:")
        # longest common suffix
        s = 0
        while s < n and a[len(a) - 1 - s] == b[len(b) - 1 - s]:
            s += 1
        print(f"    common prefix {i:#x}, common suffix {s:#x}")


def main():
    files = {}
    for path in sys.argv[1:]:
        with open(path, 'rb') as f:
            files[path.split('/')[-1]] = f.read()
    payloads = {}
    for name, b in files.items():
        payloads[name] = hdr_dump(b, name)
        print()
    names = sorted(files)
    ref = 'rtl8192cufw_TMSC.bin'
    for name in names:
        if name == ref:
            continue
        if len(files[name]) == len(files[ref]):
            diff_same_len(files[ref], files[name], ref, name)
        else:
            diff_indel(files[ref], files[name], ref, name)
        print()
    # block entropy profile of the reference payload: tables vs code vs strings
    p = payloads[ref]
    print(f"== {ref} payload entropy per 1 KiB block ==")
    for i in range(0, len(p), 1024):
        blk = p[i:i + 1024]
        bar = '#' * int(entropy(blk) * 8)
        print(f"  {i:#06x} {entropy(blk):5.2f} {bar}")


if __name__ == '__main__':
    main()
