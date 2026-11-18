# Nightly cell tests - plan (decided with Boyan 2026-11-18, not built yet)

Goal: the best home-office link (Teams/Zoom/VoWiFi, remote desktop, downloads, IPTV) - measured, not advertised. Router + modem
are powered off at night except an armed test night (`/night-screen?arm=1`, Friday reminder) or research (`NIGHT_ALWAYS=1`).

## Build order

0. **DONE 2026-11-18: parallel test runner** `tools/test_parallel.py [--docker] [--only a,b] [--jobs N]` (stdlib Python, testable
   without docker; `tools/deploy.py check` uses `--docker` when docker answers, else pytest). Was planned as (`tools/test_docker.sh`, run on the Mac - OrbStack): one `python:3.13-slim` container per
   scenario (`--network none`, repo mounted read-only, `PYTHONDONTWRITEBYTECODE=1 LOCKTEST_IPERF=0 CHECKIN_DAYS=0`), `xargs -P ${JOBS:-8}`,
   logs + `summary.txt` (OK/FAIL per scenario) in a git-ignored `test-logs/`, exit != 0 on any FAIL. Each container has its own
   loopback + `/tmp`, so the fixed ports (18100-18192) and shared `/tmp/zte-keeper-test-*` dirs cannot collide. Test first: a
   deliberately failing scenario shows as FAIL and fails the run. Then `tools/deploy.py check` uses it when docker is there (else the
   sequential pytest). Suite time ~ the slowest scenario (nr_locktest ~2 min) instead of the sum. The agent's VM has no docker: Boyan
   starts it, the agent reads `test-logs/`.
0b. **Band scan to fill the candidate table** (Boyan 2026-11-18; BUILT in `2026-11-18c`: `POST /band-scan?bands=8,1,3,7,20,28`,
   `GET /band-scans`, `keeper/bandscan.py` - live TR-069 proof pending; bands B1 B3 B7 B8 B20 B28): ARFCNs active but never in the CA / neighbour list (e.g. **B8 ARFCN
   3774, 112@3774**) are invisible to item 1. Per band: lock LTE to that band only (`Device.Cellular.Interface.1.LockLTEBand`,
   writable, today `1,2,3,4,5,7,8,12,17,20,25,26,28,30,32,34,38,39,40,41,42,46,48`; set `8`), wait for the serving cell + neighbour
   list (~60-90 s), record them (serving RSRP/RSRQ/SINR too - better than neighbour RSRP), restore the full list. No reboot (Boyan:
   band lock applies live - PROVE it for the TR-069 write first; a reboot per band would change the cost). Runs in the test windows
   (night: the night bands; Sat 08:30: the day bands), a short drop per band. Fills the registry for items 1-3.
1. DONE `2026-11-18h`: items 1-3 = `keeper/nightplan.py` (night presence per ARFCN + cell, band-scan evidence, best night RSRP
   first, weak < best-10 dB out, full-test rule, unseen 3 days out, `day_only` kept for item 7), the budget (as many as fit before
   LOCK_PAUSE ends, 15-min margin, `step_s()` ~ 90 s boot + settle + measuring + iperf), a hard stop inside the run (`deadline`), a
   band scan first each test night (`BANDSCAN_NIGHT` 1,3,7,8,20; B28 dropped in `2026-11-18k`: no service here). `/lock-tests` shows `night_plan`.
2. DONE `2026-11-18i`: item 7 = `locktest.maybe_day` (`DAY_TEST` "Sat 08:30-11:30", '' = off): once a window, after `DAY_IDLE_MIN`
   10 idle minutes; plan = the day lock (baseline) + `night_plan.day_only` + the latest night run's cells within 5 score points of
   its baseline (`nightplan.day_confirm`, fully scored only); a normal day test (5G lock kept), budget + hard stop at 11:30, a
   Telegram note at start. The Saturday band scan for the day bands: not yet (night scan covers all six bands).
3. DONE `2026-11-18j`: item 6 = `guard.ab_alt`: daytime results only (night rows never), 5G paired in the test (score's 5G part > 0),
   another site, seen in 6 h; order: the score as the share of the points measured (rows >= 60 % of the weight; older rows lack
   only down/up), sparser rows after in the old quality/signal ranking. FIXED in `2026-11-18l`: 18j ranked fully scored rows
   first, so B20 394@6400 (68/100) beat 66@1550 (61/65 = 94 %). No 5G-paired cell on another site -> only the modem's own choice.
4. DONE `2026-11-18m`: low bands = change detection only. The band scan logs each band's serving bandwidth (`bw_mhz`, from '@5'
   in the CA string) and sends a Telegram note when it differs from the last scan ("B8 bandwidth 5 -> 10 MHz - worth a lock test
   again"): Yettel holds 11.6 MHz at 900 (B8 5 MHz LTE today; the 3G release can widen it to ~10), 800 is full 10 MHz LTE already.
   Env: `NIGHT_BANDS=1,3,7` (no B8/B20 lock tests; the scan still covers 1,3,7,8,20).

## Decisions

1. **Day: collect candidates per ARFCN** from TR-069 only (`LTE_NeighbourCell*`: EARFCN, PCI, RSRP - RSRQ/RSSI/SINR exist only on
   the ZTE web page, not read: it would log the helper out). Per cell: median RSRP, presence % (minutes seen / minutes sampled).
   Re-queue a tested cell when its median RSRP rises >= 6 dB over its value at the last test, or after 30 days; drop it after 3 days
   unseen (PCI removed).
2. **Night window = LOCK_PAUSE (00:50-08:00)**: the number of cells = what fits (baseline + per cell ~18 min incl. boot, settle,
   measure, throughput; the 08:00 restore keeps a margin).
3. **Only ARFCNs present at night**, judged from night samples (2026-11-18: 100/B1 absent from 01:00; 1550, 3350, 6400 present).
   A cell needs high night presence (2026-11-18: 493/202/236@1550 were picked from daytime RSRP, present 1-12 % at night - each lock
   hung the ZTE up to 31 min).
4. **Each locked step measures quality AND throughput** (DONE in `2026-11-18b`: `down_mbps`, `up_mbps` = the modem's iperf3 after
   the measuring window while still locked, `LOCKTEST_IPERF=1`; `boot_s` = lock -> boot inform; its own iperf never stops a night
   screen as 'traffic'): call-ready, loss, p95, jitter, SINR/RSRP, the 5G cells seen (pairing) and the
   modem's iperf3 (down 4 streams + up 1). Night = unloaded network: a night result is only a candidate.
5. **Weighted score (0-100)** (DONE `2026-11-18f`: `brain.lt_score`, computed when read - `/lock-tests` rows carry `score`,
   `score_parts`, `score_partial`, `score_gate`; 5G from `nr_arfcn` + `nr_iperf` (`2026-11-18e`: the 5G cells while the iperf ran)), fixed ranges: download 25 (0-200 Mbit/s), upload 10 (0-30), call-ready 20 (%), loss 15 (0-5 %, lower
   better), p95 10 (20-200 ms, lower better), jitter 10 (0-50 ms, lower better), 5G pairing 10 (n78 10, n28 5, none 0). Gates: on
   the locked cell >= 80 % and a stable anchor. Note: ARFCN 6400 (B20) is 10 MHz and pairs n28 at most; 100/1550/3350 are 20 MHz with n78.
6. **A/B B cell**: from the score, plus 5G pairing, ANOTHER site than the A cell (backhaul faults), and a daytime confirmation.
7. **Day-only ARFCNs (e.g. 100) + daytime confirmations: Saturday from 08:30 (to ~11:30)** - not before 08:00: the night-off
   cells come back ~08:00-08:10 (2026-11-15/16/17: 340@100 back 08:03 / 08:10 / 08:01); a configurable window, start >= 08:30.

First research run: 2026-11-18 04:41-06:28 (keeper `2026-11-18b`). RESULT: B20 112/121/394/323/434@6400 scored 52-70 vs 90 for the
unlocked baseline (3350/340 + n78 534: 156/28 Mbit/s); B20 down 6.5-74 Mbit/s, SINR -8..0 dB, boot 74-84 s, iperf OK every step.
INVALID for 5G: the 5G lock on 534 (n78) stayed active (LOCK_PAUSE frees only LTE) -> every B20 step 'no5G' = our lock. Fixed in
`2026-11-18d`: night LTE steps free 5G in the same write (no extra reboot), your 5G lock comes back with the LTE lock. RETEST B20
(n28 pairing? Boyan: maybe n28 is switched off at night) + B8 112@3774 with 5G free. Also record the 5G cell DURING the step's iperf
(NSA adds the leg on traffic). Original note: B20 cells 112/121/394/323/434@6400 (`/lock-test?...&night=1`, quality only - throughput
per step comes with item 4).

## Log
- 2026-11-18 10:57-12:17 daytime test (keeper `2026-11-18j`, LTE + 5G locks removed). BAND SCAN live proof: TR-069 LockLTEBand
  applies WITHOUT a reboot (uptime kept), ~90 s per band, full list written back. B1 100/340 -83; B3 1550/434 -88; B7 3350/340
  -90; B8 3774: 394 -79, 112 -79, 374 -81, 309 -85, 196 -90 (never in the CA); B20 6400: 112 -78, 394 -80; B28: NO service (3 min
  offline -> dropped from `BANDSCAN_NIGHT` in `2026-11-18k`). FIRST n28: 156510 PCI 534 / 212 (RSRP -75..-83, SINR 2-7).
  Lock test (day mode, 5G free; the night-mode run was stopped at 11:16 by Boyan's traffic): baseline (B7 3350/340 + B1 + B3 +
  B20/121, n78 534) 90.8 = 164/27.5 Mbit/s, 100 % call-ready, p95 55 ms; B8 112@3774 59.4 = 13.8/15.8, 91.7 %, p95 69, B8 alone
  (no CA), anchor SINR -4, n28 534 11 of 12 min; B20 394@6400 68.1 = 50.8/28.9, 91.7 %, p95 74, CA B20 + B3/B1 434, SINR -4,
  n28 212 5 of 12 min. VERDICT: B8 and B20 pair with n28 by day, but n28 adds little (SINR ~3-7); both well below the baseline -
  not A/B B cells. n78 + the B7/B1/B3 CA stays far ahead.
- 2026-11-18: `2026-11-18i` Saturday day test (item 7, `DAY_TEST`), `2026-11-18j` A/B B cell by score + 5G pairing + daytime only
  (item 6). nr_locktest scenario flaky under parallel load: its mock modem counted reboots, `sets.clear()` + one
  new reboot gave the same count -> no boot inform; now it tracks the last reboot entry itself. 31 scenarios green.
- 2026-11-18 08:36 B8 112@3774 (`night=1`, 5G freed): baseline 3350/340 + n78 534 = 170/28.6 Mbit/s, 100 % call-ready; the B8 step was
  STOPPED 3 s after its boot by the night run's traffic rule (the modem's reconnect, 82 kB/s up at uptime 72 s) - no B8 data (1 sample:
  RSRP -79, SINR 0, CA B8 only, no 5G, 87 % loss right after the boot). Fixed in `2026-11-18g` (`NIGHT_BOOT_QUIET_S` 300). Retest B8 +
  B20 with 5G free in the next night window.
- 2026-11-18: `2026-11-18e` nr_iperf (5G during the iperf), `2026-11-18f` weighted score in /lock-tests, `tools/test_parallel.py` (29
  scenarios in ~130 s with 4 processes in the agent VM).
