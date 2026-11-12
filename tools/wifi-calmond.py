#!/usr/bin/env python3
"""wifi-calmond v2.0 — passive PHY-counter logger for RTL8188CU sticks.

RETRACTION (2026-11-12): v1.x treated all-0xFF at 0x0DA2-0x0DA9 /
0x0FB8 as a "calibration wipe signature" and reloaded the driver on
it. Those registers are 16-bit OFDM PHY error counters
(rtlwifi: ROFDM_PHYCOUNTER1/2/3 at 0xDA0/0xDA4/0xDA8, plus the PHY
status block at 0xF80+). They tick at 10-20/s on an associated stick
and saturate at 0xFFFF after roughly an hour. The v1 "signature" was
a clock, and the v1 reload loop reloaded healthy links every ~85 min.

v2.0 never touches the driver. It passively samples the counters and
logs rate summaries, so a future real RX wedge (see wedge.md: Nov 10
06:53 event — fw alive, H2C answering, RX dead, reload recovers,
root cause OPEN) can be correlated with counter behaviour. Read
errors are logged, never acted on.

Runs as a systemd service. Stdlib only; requires regread.py alongside.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from regread import ctrl_read, find_device

POLL_S = 30
COUNTERS = (0x0DA6, 0x0DA8)      # 16-bit little-endian at these addrs
LOG = '/var/log/wifi-calmond/counters.log'


def log(msg):
    subprocess.run(['logger', '-t', 'wifi-calmond', msg], check=False)


def read16(fd, reg):
    try:
        d = ctrl_read(fd, reg, 2, timeout_ms=2000)
        return (d[1] << 8) | d[0] if len(d) == 2 else None
    except OSError:
        return None


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    while True:
        path, _, _ = find_device(None, None)
        if not path:
            log('no stick on USB; waiting')
            time.sleep(POLL_S)
            continue
        try:
            fd = os.open(path, os.O_RDWR)
        except OSError:
            time.sleep(POLL_S)
            continue
        t0 = time.time()
        v0 = {r: read16(fd, r) for r in COUNTERS}
        os.close(fd)
        time.sleep(POLL_S)
        path, _, _ = find_device(None, None)
        if not path or None in v0.values():
            continue
        try:
            fd = os.open(path, os.O_RDWR)
        except OSError:
            continue
        t1 = time.time()
        v1 = {r: read16(fd, r) for r in COUNTERS}
        os.close(fd)
        if None in v1.values():
            log('counter read failed once (passive, no action)')
            continue
        with open(LOG, 'a') as f:
            for r in COUNTERS:
                dt, dv = t1 - t0, v1[r] - v0[r]
                rate = dv / dt if dt else 0
                f.write(f'{t0:.0f} 0x{r:04X} {v0[r]:04X}->{v1[r]:04X} '
                        f'{rate:.1f}/s\n')
        # hourly summary so silence never looks like health
        if time.localtime().tm_min < 1:
            log('passive logger alive')


if __name__ == '__main__':
    main()
