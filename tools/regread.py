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
import fcntl
import os
import struct
import sys

USBDEVFS_CONTROL = 0
REALTEK_IDS = {('0bda', '8176'), ('0bda', '8179'), ('0bda', '8188'),
               ('0bda', '8192'), ('0bda', '8812')}


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


def ctrl_read(fd, reg, length, timeout_ms=3000):
    """usbdevfs_ctrltransfer: u8 u8 u16 u16 u16 u32, packed."""
    hdr = struct.pack('<BBHHHI', 0xC0, 0x05, reg, 0x0000, length, timeout_ms)
    buf = bytearray(hdr + b'\x00' * length)
    n = fcntl.ioctl(fd, USBDEVFS_CONTROL, buf, True)
    return bytes(buf[12:12 + (n if n > 0 else length)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('regs', nargs='+', help='register(s), HEX, optional :len')
    ap.add_argument('--vendor', help='override idVendor (hex, no 0x)')
    ap.add_argument('--product', help='override idProduct')
    ap.add_argument('--timeout', type=int, default=3000)
    a = ap.parse_args()

    path, v, pr = find_device(a.vendor, a.product)
    if not path:
        sys.exit('no Realtek USB wifi device found in /sys/bus/usb — '
                 'the stick is not enumerated at all (USB-level wedge)')
    print(f'device: {path}  idVendor:idProduct = {v}:{pr}')

    fd = os.open(path, os.O_RDWR)
    try:
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
