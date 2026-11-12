#!/usr/bin/env python3
"""wifi-calmond v2.1 — passive RX-health logger for RTL8188CU sticks.

RETRACTION (2026-11-12): v1.x treated all-0xFF at 0x0DA2-0x0DA9 /
0x0FB8 as a "calibration wipe signature" and reloaded the driver on
it. Those registers are 16-bit OFDM PHY error counters
(rtlwifi: ROFDM_PHYCOUNTER1/2/3 at 0xDA0/0xDA4/0xDA8, plus the PHY
status block at 0xF80+). They tick at 10-20/s on an associated stick
and saturate at 0xFFFF after roughly an hour. The v1 "signature" was
a clock, and the v1 reload loop reloaded healthy links every ~85 min.

This daemon NEVER touches the driver and never writes a register.
Every POLL_S seconds it appends one line to LOG with:

  - mac80211 station statistics (`iw station dump`): beacon-rx rate,
    rx-packet rate, beacon loss, signal. These do not saturate and
    measure the wedge symptom directly (the Nov 7-9 zombie phase lost
    broadcast RX first; the Nov 10 wedge lost all RX).
  - the two OFDM PHY error counters with their rate. They saturate
    after about an hour of uptime and nothing resets them, so a
    saturated counter is logged as "SAT" and carries NO information —
    0/s on a saturated counter is not "RX dead".
  - the 8051 heartbeat register 0x01C4 (see firmware.md, Diagnostics).

It writes a syslog marker when the stick is associated but beacons
stop arriving (and when they resume), so a real event gets an onset
timestamp to correlate against the usbmon capture. Marker only: no
recovery action is taken here; that stays with the ping watchdog.

Runs as a systemd service. Stdlib only; requires regread.py alongside.
"""
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from regread import ctrl_read, find_device

POLL_S = 30
IFACE = 'wlan0'
IW = '/usr/sbin/iw'
COUNTERS = (0x0DA6, 0x0DA8)      # 16-bit little-endian, saturating
HEARTBEAT = 0x01C4
LOG = '/var/log/wifi-calmond/health.log'
LOG_MAX = 5 * 1024 * 1024        # rotate to .1 above this size
STALL_BEACON_RATE = 2.0          # beacons/s; an AP sends ~10/s
STALL_SAMPLES = 2                # consecutive low samples before the marker

STA_FIELDS = {'beacon rx': 'beacon_rx', 'rx packets': 'rx_packets',
              'beacon loss': 'beacon_loss', 'rx drop misc': 'rx_drop',
              'tx failed': 'tx_failed', 'signal': 'signal'}


def log(msg):
    subprocess.run(['logger', '-t', 'wifi-calmond', msg], check=False)


def station():
    """mac80211 statistics for the current AP, or None when not associated."""
    try:
        out = subprocess.run([IW, 'dev', IFACE, 'station', 'dump'],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r'Station (\S+)', out)
    if not m:
        return None
    sta = {'bssid': m.group(1)}
    for line in out.splitlines():
        key, _, val = line.strip().partition(':')
        if key in STA_FIELDS:
            n = re.match(r'\s*(-?\d+)', val)
            if n:
                sta[STA_FIELDS[key]] = int(n.group(1))
    return sta


def registers():
    """PHY counters and heartbeat via usbfs; any value may be None."""
    regs = {r: None for r in COUNTERS + (HEARTBEAT,)}
    path, _, _ = find_device(None, None)
    if not path:
        return regs
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return regs
    try:
        for r in regs:
            try:
                d = ctrl_read(fd, r, 2, timeout_ms=2000)
                if len(d) == 2:
                    regs[r] = d[0] | (d[1] << 8)
            except OSError:
                pass
    finally:
        os.close(fd)
    return regs


def rate(new, old, dt):
    """Per-second rate of a monotonic counter, '-' if unknown or reset."""
    if new is None or old is None or dt <= 0 or new < old:
        return '-'
    return f'{(new - old) / dt:.1f}'


def write_line(line):
    try:
        if os.path.getsize(LOG) > LOG_MAX:
            os.replace(LOG, LOG + '.1')
    except OSError:
        pass
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    log('passive RX-health logger started (no driver actions)')
    prev_t = prev_sta = prev_regs = None
    low = 0
    stalled = False
    last_hour = -1
    while True:
        t = time.time()
        sta = station()
        regs = registers()
        dt = t - prev_t if prev_t else 0
        same_assoc = bool(sta and prev_sta and sta['bssid'] == prev_sta['bssid'])

        if sta:
            old = prev_sta if same_assoc else {}
            beacon_rate = rate(sta.get('beacon_rx'), old.get('beacon_rx'), dt)
            link = (f"assoc={sta['bssid']} beacon/s={beacon_rate} "
                    f"rxpkt/s={rate(sta.get('rx_packets'), old.get('rx_packets'), dt)} "
                    f"bloss={sta.get('beacon_loss', '-')} "
                    f"sig={sta.get('signal', '-')} "
                    f"rxdrop={sta.get('rx_drop', '-')} "
                    f"txfail={sta.get('tx_failed', '-')}")
        else:
            beacon_rate = '-'
            link = 'assoc=none'

        phy = []
        for r in COUNTERS:
            v = regs[r]
            if v is None:
                phy.append(f'{r:04X}=ERR')
            elif v == 0xFFFF:
                phy.append(f'{r:04X}=SAT')
            else:
                old = prev_regs[r] if prev_regs else None
                phy.append(f'{r:04X}={v:04X}({rate(v, old, dt)}/s)')
        hb = regs[HEARTBEAT]
        write_line(f"{t:.0f} {link} {' '.join(phy)} "
                   f"hb={'ERR' if hb is None else f'{hb:04X}'}")

        # onset/offset markers for "associated but no beacons arriving"
        if beacon_rate != '-' and float(beacon_rate) < STALL_BEACON_RATE:
            low += 1
            if low == STALL_SAMPLES and not stalled:
                stalled = True
                log(f"RX STALL SUSPECT: associated to {sta['bssid']}, "
                    f"beacons {beacon_rate}/s (passive marker, no action)")
        elif beacon_rate != '-':
            if stalled:
                log(f'beacons resumed ({beacon_rate}/s)')
            low = 0
            stalled = False
        elif not sta:
            low = 0
            stalled = False

        hour = time.localtime().tm_hour
        if hour != last_hour:
            last_hour = hour
            log('passive logger alive')      # so silence never looks like health

        prev_t, prev_sta, prev_regs = t, sta, regs
        time.sleep(max(1.0, POLL_S - (time.time() - t)))


if __name__ == '__main__':
    main()
