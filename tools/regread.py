#!/usr/bin/env python3
"""Read MAC registers of a (possibly wedged) RTL8188CU-family stick
directly over USB, with no dependencies beyond Python stdlib.

Uses the same protocol the rtl8xxxu driver uses (decoded in this
project, see analysis/firmware.md): USB control transfer IN,
bmRequestType 0xC0, bRequest 0x05, wValue = register address.

Works on a live system (driver present, via usbfs) and is intended
especially for reading the diagnostics register 0x01C4/0x01C5 from a
WEDGED stick — the readout table is in analysis/firmware.md
"Diagnostics". Needs root. If the transfer times out, the firmware is
dead at the USB level (itself a diagnostic result).

Usage:
  sudo python3 -I regread.py 01C4            # read one register
  sudo python3 -I regread.py 01C4:2 01CC 0134:4
  sudo python3 -I regread.py --vendor 0bda --product 8176 01C4
"""
import argparse
import ctypes
import fcntl
import os
import struct
import sys

# USBDEVFS_CONTROL = _IOWR('U', 0, struct usbdevfs_ctrltransfer).
# Linux 6.18 added a trailing `void __user *data` pointer to the struct,
# changing the encoded size (12 -> 16 on 32-bit, 24 on 64-bit) and the
# calling convention (data buffer is now a pointer inside the struct).
# Compute both request numbers at runtime and try new-style first.
_PTR = ctypes.sizeof(ctypes.c_void_p)
_SZ_NEW = 8 + 4 + _PTR + (_PTR - 8 - 4) % _PTR  # fields + pad before pointer
REQ_NEW = (3 << 30) | (_SZ_NEW << 16) | (0x55 << 8) | 0
REQ_OLD = (3 << 30) | (12 << 16) | (0x55 << 8) | 0
REQ = None
REALTEK_IDS = {('0bda', '8176'), ('0bda', '8179'), ('0bda', '8188'),
               ('0bda', '8192'), ('0bda', '8812'),
               ('7392', '7811'),   # Edimax EW-7811Un
               ('7392', '7811'), ('0df6', '005c'), ('2001', '3308')}


def find_device(vendor, product):
    for dev in sorted(os.listdir('/sys/bus/usb/devices')):
        p = f'/sys/bus/usb/devices/{dev}'
        try:
            v = open(f'{p}/idVendor').read().strip()
            pr = open(f'{p}/idProduct').read().strip()
        except OSError:
            continue
        if (v, pr) == (vendor, product) or (not vendor and (v, pr) in REALTEK_IDS):
            bus = open(f'{p}/busnum').read().strip()
            devn = open(f'{p}/devnum').read().strip()
            return f'/dev/bus/usb/{int(bus):03d}/{int(devn):03d}', v, pr
    return None, None, None


class CtrlTransfer(ctypes.Structure):
    _fields_ = [('bRequestType', ctypes.c_uint8), ('bRequest', ctypes.c_uint8),
                ('wValue', ctypes.c_uint16), ('wIndex', ctypes.c_uint16),
                ('wLength', ctypes.c_uint16),
                ('timeout', ctypes.c_uint32), ('data', ctypes.c_void_p)]


def ctrl_read(fd, reg, length, timeout_ms=3000):
    """Control IN via usbfs; supports pre- and post-6.18 struct layouts."""
    global REQ
    data = ctypes.create_string_buffer(length)
    if REQ in (None, REQ_NEW):
        ct = CtrlTransfer(0xC0, 0x05, reg, 0, length, timeout_ms,
                          ctypes.addressof(data))
        try:
            n = fcntl.ioctl(fd, REQ_NEW, ct)
            REQ = REQ_NEW
            return data.raw[:n if 0 < n <= length else length]
        except OSError as e:
            if e.errno != 25:
                raise
            REQ = REQ_OLD
    # pre-6.18: inline data after the 12-byte header
    buf = bytearray(struct.pack('<BBHHHI', 0xC0, 0x05, reg, 0, length,
                                timeout_ms) + b'\x00' * length)
    n = fcntl.ioctl(fd, REQ_OLD, buf, True)
    return bytes(buf[12:12 + (n if n > 0 else length)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('regs', nargs='*', help='register(s), HEX, optional :len')
    ap.add_argument('--vendor', help='override idVendor (hex, no 0x)')
    ap.add_argument('--product', help='override idProduct')
    ap.add_argument('--timeout', type=int, default=3000)
    ap.add_argument('--dump', metavar='START:END',
                    help='read register range to hex lines on stdout')
    a = ap.parse_args()

    path, v, pr = find_device(a.vendor, a.product)
    if not path:
        sys.exit('no Realtek USB wifi device found in /sys/bus/usb — '
                 'the stick is not enumerated at all (USB-level wedge)')
    print(f'device: {path}  idVendor:idProduct = {v}:{pr}')

    fd = os.open(path, os.O_RDWR)
    try:
        if a.dump:
            st, _, e = a.dump.partition(':')
            start, end = int(st, 16), int(e, 16)
            out = bytearray()
            addr = start
            while addr < end:
                n = min(8, end - addr)
                out += ctrl_read(fd, addr, n, a.timeout)
                addr += n
            for off in range(0, len(out), 16):
                chunk = out[off:off + 16]
                print(f'{start + off:04X}: ' + ' '.join(f'{b:02X}' for b in chunk))
            sys.exit(0)
        for spec in a.regs:
            reg, _, ln = spec.partition(':')
            length = int(ln) if ln else 1
            r = int(reg, 16)
            try:
                data = ctrl_read(fd, r, length, a.timeout)
                print(f'  reg 0x{r:04X} = ' + ' '.join(f'{b:02X}' for b in data)
                      + ('' if len(data) == length else f'  (short: {len(data)}/{length})'))
            except OSError as e:
                print(f'  reg 0x{r:04X}: TRANSFER FAILED: {e}  '
                      '=> firmware not answering USB control transfers (USB-level wedge)')
    finally:
        os.close(fd)

    print('\n0x01C4/0x01C5 readout (little-endian): '
          '0x50C6/0x50C7 = main loop alive, 0x60F2 = stuck in interrupt '
          'dispatch, 0x60F3 = interrupts completing, 0x4B87/0x4B88 = '
          'timer-int handler enter/exit, 0x6891 = hw-event handler.')


if __name__ == '__main__':
    main()
