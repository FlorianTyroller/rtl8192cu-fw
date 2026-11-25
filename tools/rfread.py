#!/usr/bin/env python3
"""Read RTL8188CU/8192CU RF (radio) registers over USB, path A.

RF registers are not memory-mapped; the chip exposes them through the
baseband's 3-wire interface. This tool repeats exactly the sequence the
kernel driver uses (rtl8xxxu_read_rfreg in rtl8xxxu_core.c):

  1. read REG_FPGA0_XA_HSSI_PARM2 (0x0824)
  2. write it back with EDGE_READ (bit 31) cleared
  3. write it with the RF address in bits 23..30 and EDGE_READ set
  4. write the step-2 value with EDGE_READ set again
  5. read the result from 0x08B8 (HSPI readback) if 0x0820 bit 8 (PI
     mode) is set, else from 0x08A0 (LSSI readback); low 20 bits

so it writes 0x0824 only, the same values the driver writes thousands
of times a minute while scanning, and restores the original 0x0824
value at the end. It can race the driver's own RF reads; run --passes 2
and compare. Needs root.

Usage: sudo python3 -I rfread.py [--passes N] [--regs 00:40]
"""
import argparse
import ctypes
import fcntl
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regread                                   # noqa: E402

HSSI_PARM1, HSSI_PARM2 = 0x0820, 0x0824
LSSI_READBACK, HSPI_READBACK = 0x08A0, 0x08B8
EDGE_READ = 1 << 31
ADDR_SHIFT, ADDR_MASK = 23, 0x7F800000
PI_MODE = 1 << 8


def read32(fd, reg):
    d = regread.ctrl_read(fd, reg, 4)
    if len(d) != 4:
        raise OSError(f'short read at 0x{reg:04X}')
    return int.from_bytes(d, 'little')


def write32(fd, reg, val):
    data = ctypes.create_string_buffer(val.to_bytes(4, 'little'), 4)
    ct = regread.CtrlTransfer(0x40, 0x05, reg, 0, 4, 2000, ctypes.addressof(data))
    fcntl.ioctl(fd, regread.REQ_NEW, ct)


def rf_read(fd, reg):
    hssia = read32(fd, HSSI_PARM2)
    val = (hssia & ~ADDR_MASK) | (reg << ADDR_SHIFT) | EDGE_READ
    hssia &= ~EDGE_READ
    write32(fd, HSSI_PARM2, hssia)
    time.sleep(0.00001)
    write32(fd, HSSI_PARM2, val)
    time.sleep(0.0001)
    write32(fd, HSSI_PARM2, hssia | EDGE_READ)
    time.sleep(0.00001)
    pi = read32(fd, HSSI_PARM1) & PI_MODE
    return read32(fd, HSPI_READBACK if pi else LSSI_READBACK) & 0xFFFFF, bool(pi)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--passes', type=int, default=2)
    ap.add_argument('--regs', default='00:40', help='HEX start:end (end exclusive)')
    a = ap.parse_args()
    start, end = (int(x, 16) for x in a.regs.split(':'))
    path, v, p = regread.find_device(None, None)
    if not path:
        sys.exit('no stick found')
    fd = os.open(path, os.O_RDWR)
    orig = read32(fd, HSSI_PARM2)
    print(f'device {path} {v}:{p}  HSSI_PARM2 at start = 0x{orig:08X}')
    try:
        results = []
        for n in range(a.passes):
            row = {}
            for reg in range(start, end):
                row[reg], pi = rf_read(fd, reg)
            results.append(row)
            print(f'pass {n + 1} done (readback via {"HSPI" if pi else "LSSI"})')
    finally:
        write32(fd, HSSI_PARM2, orig)
        os.close(fd)
    print('RF path A   ' + '  '.join(f'pass{n + 1}' for n in range(a.passes)) + '   agree')
    for reg in range(start, end):
        vals = [r[reg] for r in results]
        print(f'  RF 0x{reg:02X}  ' + '  '.join(f'{x:05X}' for x in vals)
              + ('   yes' if len(set(vals)) == 1 else '   NO'))


if __name__ == '__main__':
    main()
