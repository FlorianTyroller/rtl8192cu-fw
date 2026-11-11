#!/usr/bin/env python3
"""wifi-calmond — high-frequency calibration-wipe monitor for
RTL8188CU/8192CU sticks (the 2026-11-10 discovery, see
analysis/wedge.md in the rtl8192cu-fw project).

Polls the RX-calibration signature registers over USB every POLL_S
seconds. The firmware never writes these; all-0xFF means the
calibration power domain lost state and RX is dead or dying
(the "zombie" degradation lasts days before total failure).

On first detection it captures evidence (full 0x0000-0x0FFF register
dump + a copy of the current usbmon logs before they rotate), then
recovers: reload the wifi driver and re-activate the connection.

Runs as a systemd service (Restart=always). Stdlib only; reuses
regread.py when installed alongside it.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from regread import ctrl_read, find_device          # installed copy
except ImportError:
    sys.exit('regread.py must be installed alongside wifi-calmond')

POLL_S = 20
GRACE_S = 120                     # post-reload wait before trusting regs
EVIDENCE_DIR = '/var/log/wifi-calmond'
STAMP = '/run/wifi-calmond.lastreload'
# signature registers: (addr, length); wiped = all-FF in the read
SIG_REGS = ((0x0DA6, 4), (0x0FB8, 4))


def log(msg):
    subprocess.run(['logger', '-t', 'wifi-calmond', msg], check=False)


def healthy(fd):
    for reg, n in SIG_REGS:
        try:
            data = ctrl_read(fd, reg, n, timeout_ms=2000)
        except OSError:
            return False                       # usb error: not a verdict
        if not data or any(b != 0xFF for b in data):
            return True                        # holds values -> healthy
    return False


def capture_evidence(fd):
    ts = int(time.time())
    d = f'{EVIDENCE_DIR}/wipe-{ts}'
    os.makedirs(d, exist_ok=True)
    with open(f'{d}/regs-0000-1000.txt', 'w') as f:
        addr = 0
        while addr < 0x1000:
            try:
                chunk = ctrl_read(fd, addr, 8, timeout_ms=2000)
            except OSError:
                chunk = b'\xea' * 8
            f.write(f'{addr:04X}: ' + ' '.join(f'{b:02X}' for b in chunk) + '\n')
            addr += 8
    subprocess.run(['sh', '-c',
                    f'cp /var/log/usbmon-wifi.log* "{d}/" 2>/dev/null'],
                   check=False)
    log(f'evidence captured in {d} (regs + usbmon snapshot)')


def recover():
    log('CALIBRATION WIPE DETECTED - capturing evidence, then reloading')
    drv = subprocess.run(
        ['sh', '-c',
         'basename "$(readlink -f /sys/class/net/wlan0/device/driver/module 2>/dev/null)"'],
        capture_output=True, text=True).stdout.strip()
    if drv and drv != '.':
        subprocess.run(['modprobe', '-r', drv], check=False)
        time.sleep(3)
        subprocess.run(['modprobe', drv], check=False)
        time.sleep(15)
    subprocess.run(['sh', '-c',
                    "nmcli -t -f NAME,TYPE connection show | awk -F: '$2==\"802-11-wireless\"{print $1}' | while read c; do nmcli connection up \"$c\" >/dev/null 2>&1 && break; done"],
                   check=False)
    with open(STAMP, 'w') as f:
        f.write(str(int(time.time())))
    log('driver reloaded after calibration wipe')


def main():
    path, _, _ = find_device(None, None)
    if not path:
        log('no Realtek stick found on USB - monitoring disabled')
        time.sleep(POLL_S)
        return
    fd = os.open(path, os.O_RDWR)
    last_hour = -1
    while True:
        now = int(time.time())
        in_grace = False
        if os.path.exists(STAMP):
            try:
                with open(STAMP) as f:
                    in_grace = now - int(f.read().strip() or 0) < GRACE_S
            except (ValueError, OSError):
                in_grace = False
        if not in_grace:
            try:
                if not healthy(fd):
                    # re-find: another actor (watchdog) may have reloaded
                    # already; reading a stale fd yields garbage evidence
                    p2, _, _ = find_device(None, None)
                    if p2 != path:
                        log('device instance changed - another actor '
                            'reloaded; refreshing, no double recovery')
                        os.close(fd)
                        path = p2
                        if path:
                            fd = os.open(path, os.O_RDWR)
                    else:
                        capture_evidence(fd)
                        os.close(fd)
                        recover()
                        path, _, _ = find_device(None, None)
                        if path:
                            fd = os.open(path, os.O_RDWR)
            except OSError:
                try:
                    path, _, _ = find_device(None, None)
                    if path:
                        fd = os.open(path, os.O_RDWR)
                except OSError:
                    pass                                # device blinked
        if time.localtime().tm_hour != last_hour:
            last_hour = time.localtime().tm_hour
            log('monitor alive, signature ok' )
        time.sleep(POLL_S)


if __name__ == '__main__':
    main()
