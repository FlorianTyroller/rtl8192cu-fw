# Session review brief — November 2026 dig (Nov 5–12)

Written for an independent reviewer. Read alongside the repo; every
claim below is checkable against a file named in brackets. The intent
is audit: find our blind spots, wrong inferences, and unexplored
branches.

## Project context

Reverse-engineering the RTL8188CUS/8192CU 8051 WiFi firmware
(`rtl8192cufw_TMSC.bin` 88.2, Dec 2012) and chasing the field "wedge"
(the stick silently dies; only driver reload fixes it). Authoritative
docs: `analysis/firmware.md` (reference), `analysis/wedge.md`
(investigation), `analysis/ERRATA.md` (ten corrected claims),
`analysis/annotations.tsv` (243 confidence-tagged symbols). Field box:
a Raspberry Pi 1 B ("capture Pi", wlan .117, eth0 lifeline .200) with
the stick as its only WiFi, running usbmon capture (~4 h archive),
a 1-minute watchdog, and a 10-second calibration monitor. Radio
environment: primary AP/router (AP-P) + two same-SSID mesh repeaters
(AP-R1 auth-flaky, AP-R2).

## Session timeline (Nov 5–12, condensed)

1. Firmware documentation completed: all 5 open points closed via
   three parallel reading agents + integration; full protocol,
   interrupts, state engines, rate adaptation, ROM ABI documented
   [firmware.md, annotations.tsv].
2. Site restructured professional (firmware/wedge/ERRATA split,
   neutral names, sitemap, robots.txt, Search Console verified).
3. Routine Pi checks → Nov 9: Pi unreachable despite power/LEDs OK.
   Ethernet lifeline added → discovered "zombie association": stick
   associated 2.5 days, kernel silent, gateway pings from the Pi OK,
   but externally unreachable (broadcast RX dead, unicast alive).
4. Spontaneous recovery Nov 9 17:33; then 60-second data-path blips
   every 2–3 h with H2C fully alive (247 mailbox reads, 0 stuck).
5. Full wedge Nov 10 06:53: RX dead, scans never complete, supplicant
   stuck. Live register interrogation via new `tools/regread.py`
   (fixed for Linux 6.18's changed usbfs struct: trailing `void *data`
   → request 0xC0105500 on 32-bit).
6. Live wedge fingerprint: 8051 main loop ALIVE (heartbeat 0x50C6/67
   toggling), mailbox 0x00, FWIMR=init, no stuck dispatch, TXPAUSE=0,
   RX filters open, RCR plausible, 86/86 MAC init regs byte-perfect —
   but RX dead between PHY and MAC (0 BSS, 0 beacon-DMA interrupts).
7. Driver reload → recovery <10 s → fw-level wedge confirmed.
8. Root cause by byte-diff (wedged vs healthy dumps, same chip):
   all-0xFF wipe cluster at 0x0DA2-0x0DA9, 0x0F80-0x0F98,
   0x0FA2-0x0FA5, 0x0FB8-0x0FBB (+0x0403/0B/0F). Disassembly proves
   the fw NEVER writes these (no direct or computed refs) and the
   driver never writes them → power/clock domain state loss
   [wedge.md "causal chain", 7 links, 6 proven 1 inferred].
9. Detection+prevention deployed: watchdog signature check + reload;
   `tools/wifi-calmond.py` daemon (10 s poll; on wipe: priority regs
   first, full dump, usbmon copy, reload).
10. Maiden catch Nov 10 18:02 (2 h 22 m after deploy): CAM/BSSID
    re-program burst at T−34 s, 0x0DA6 wiped between polls, second
    domain (0x0FB8 live counter) alive ≥1 min longer → progressive
    cross-domain wipe. Watchdog+calmond double-fired (34 s recovery).
11. Provocation Nov 11 23:00–00:00: 40 CAM-churn cycles (nmcli
    down/up; only the Pi's own connection flaps) → **clean negative**
    (40/40 assoc, 0 wipes) → CAM correlation decoupled.
12. Nov 11–12: recurring-wipe mode — 21 signature fires at ~85-minute
    period over ~31 h, starting **15 min after** the provocation
    ended; stopped 07:27 Nov 12 leaving a zombie state (RX dead, 0
    BSS, signature VALID, 8051 alive) → **signature marks end stage,
    not onset**. Detector blind spots found+fixed: calmond stale-fd
    (read 0xEA as healthy; now reopens per poll), watchdog ping
    succeeding over eth0 (masked 12 h outage; now `-I wlan0`).

## Current belief state (with evidence strength)

- Wedge mechanism: power/clock domain state loss wipes vendor-reserved
  calibration registers → RX demodulates garbage; 8051 unaffected.
  Strong: register diffs + no-writer proof + live fingerprint.
- Signature 0x0DA2:8 all-FF = end stage (late indicator). RX dies
  earlier. Medium-strong: Nov 12 zombie state with valid signature.
- Wipe is progressive across domains (minutes-scale). Medium: one
  clean observation (0x0DA6 vs 0x0FB8).
- Trigger: firmware-autonomous PS/state-engine timing, NOT host CAM
  activity (40-cycle negative), possibly inducible by sustained churn
  (recurring mode started 15 min post-provocation — suspicious but
  n=1). Weak-medium.
- Recovery: fw re-download always works (n≈30 reloads, zero failures).

## Corrections made this session (honesty ledger)

1. "USB-level hard wedge" (Nov 9) — wrong twice; stick self-recovered.
2. My grep bug (`grep -v reachable` hides "unreachable") briefly
   confused the watchdog-history reading; re-verified with fixed filter.
3. regread ioctl constant wrong for 6.18 (0xC00C5500 vs 0xC0105500);
   root-caused via strace + kernel uapi header.
4. calmond v1.0 stale-fd artifact (all-EA evidence dump misread as
   "device dying deeper"); v1.1 device re-find, v1.3 reopen-per-poll.
5. Watchdog eth0-guard made it blind to wifi death with cable in —
   my own earlier reading called the guard "by design"; it cost 12 h.

## Open questions a reviewer should attack

1. **Early detector**: what changes BEFORE 0x0DA6 wipes? (Candidates
   never checked: 0x0FB8 counter freezing, FWISR2 beacon-DMA rates,
   RSSI-report values from the driver, H2C timing jitter.) We have
   ~40 h of healthy + wedged usbmon evidence in ~/wedge-captures/.
2. **Recurring-mode trigger**: coincidence vs induction by the
   provocation? The 120-cycle retest is planned; is there a better
   experiment design (e.g., randomized spacing)?
3. **The inferred link** (isolation left asserted): could the wipe
   instead be thermal/voltage, or ROM-initiated? We proved no
   image-code writer; ROM is unreadable. What host-side experiment
   discriminates?
4. **Zombie phase mechanism**: broadcast-RX-dead-first while gateway
   unicast worked for 2.5 days. Is our RX-filter-domain story right?
   Could it be router-side (Speedport client-table) instead? The
   router log has known holes (missed the 5 stress-cycle assocs) —
   how much of the zombie story depends on possibly-unreliable
   router logs?
5. **Signature semantics**: 0x0DA2's first 4 bytes read FF even
   healthy; only the low 4 bytes matter. Verified once, live. Single
   observation — is the healthy value stable across conditions
   (channel, temperature, uptime)?
6. **Emulator divergence**: sim8051 boots diverge after ~180 steps
   (ROM-state dependent); all dynamic results came from
   service-entry replay, never full-boot emulation. Any conclusions
   that silently assume full-boot fidelity?
7. **The "40 cycles clean" negative**: cycles were 90 s apart with
   10 s down-time. The recurring mode's ~85-min period hints the
   relevant timescale is much longer — was the experiment even
   capable of triggering the mechanism it tested?

## Where things live

- Repo docs: `analysis/*.md`, `analysis/annotations*.tsv`,
  `analysis/functions.tsv`, `tools/*` (dis8051, sim8051 + sim-scripts,
  fwl, regread, wifi-calmond), `analysis/wedge-regdump-20261110.txt`.
- Local-only (this workstation, NOT in repo — license): usbmon
  evidence in `~/wedge-captures/` (20261106-*, 20261110-*, the
  wipe-1794330159 bundle, wedged/healthy register dumps).
- Pi: /usr/local/sbin/{regread.py,wifi-calmond,wifi-watchdog},
  /etc/systemd/system/wifi-calmond.service, wifi-watchdog.timer,
  /root/wifi-provoke.sh, /var/log/{usbmon-wifi*,wifi-provoke.log,
  wifi-calmond/}. eth0 .200 lifeline stays connected.
- Memory file (cross-session state) mirrors this brief.

## Suggested review angles

- Cross-check wedge.md claims against annotations.tsv (any symbol
  cited in prose that disagrees with the tagged confidence?).
- Audit the causal chain's "proven" labels against the actual capture
  files (are any links actually only n=1 observations?).
- Look for monitoring gaps still present (calmond reopen-per-poll now
  — but does it handle the device disappearing mid-poll? usbmon
  rotation vs evidence-copy race?).
- The 0x0EA/0xEA ambiguity: we treat EA-on-read as "dead/failed" but
  never distinguished kernel-timeout from device-returned-EA bytes.
  Does any conclusion depend on that distinction?
