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
- 2026-11-20 ~22:00 `2026-11-20j` Ookla Yettel 69006: 178.45 / 25.47 Mbit/s, loaded +4.6 ms down (peak 100), +5.9 up (peak 77), 0 %
  loss (unshaped earlier: 205-212 with +35-40 ms, peaks 0.8-1.9 s). Boyan: a fair trade.
- 2026-11-20 21:30-21:52 `tools/iperf_sweep.py` (11 servers x 2 rounds, peak, IPTV off): one server swings 110 -> 178 in 10 min (the
  cell, not the server); DataPacket (Sofia, Istanbul, Athens, Zagreb, Vienna, Bucharest-DP) 125-178, ServersCamp 104-120 (slow
  path - NOT a good fallback), Belgrade SOX no answer; modem CPU 46-93 %, not tied to the speed (Sofia 178 at 73 %) -> not the
  limit; 8 streams no gain. Athens is fine (150/134) - the 19:39 81 was the cell. Env: CAPTEST_IPERF=37.19.203.1:5201,
  156.146.52.1:5201,169.150.252.2:5201,169.150.242.129:5201,185.102.217.170:5201. `2026-11-20j`: the fresh-lower override needs
  2 low tests (the higher wins); one low test asks for a confirmation 10 min later.
- 2026-11-20 20:47-20:50 server check (diagnostic /captest, peak): Sofia 4 streams busy (no speed), Sofia 8 streams 102, Bucharest
  ServersCamp (lg.serverscamp.com:5202) 103, Bucharest DataPacket busy, Athens (the 19:39 fallback) 81 -> Athens reads ~20 % low;
  env CAPTEST_IPERF=37.19.203.1:5201,lg.serverscamp.com:5202,lg.serverscamp.com:5203,169.150.252.2:5201. Download queue is already
  fq_codel (README: CAKE fills a core at ~140 Mbit/s), upload CAKE. `2026-11-20i`: the modem's CPU (TR-069
  Device.DeviceInfo.ProcessStatus.CPUUsage) is read while its iperf3 runs, the max goes into the result ('modem CPU max N %');
  `tools/iperf_sweep.py` ranks the servers (diagnostic tests, ~1 min each) with it.
- 2026-11-20 19:46 peak: call-ready 80 % / 30 min (A/B needs < 70 %), 26/30 idle, SINR 5 / 13 -> no trial, as designed (Boyan: no
  "disco lights"). `2026-11-20g`: every A/B reboot waits until no call: not 5 steady minutes >= 4 kB/s in a direction (3 kB/s
  all-quiet held in only 26 % of windows with 2 PCs + 2 phones) - an internet audio call looked "idle". The trend stays passive: calls-at-risk by hour + the probe's idle-delay excess (no iperf). Boyan: IPTV
  stable this evening with 20b-f (yesterday cuts + pixelation) - the ratchet to the 35 floor starved it.
- 2026-11-20 18:30-19:28 peak hour: the 18:31 capacity test measured 154 -> the fresh-lower rule set 177.1 (1.15 x 154) as designed;
  the delay above idle was 13-30 ms with the line IDLE (19:18-19:21) - congestion in the cell, not our queue (only a cell change
  helps); a speedtest's 10-15 s burst (168 Mbit/s, +39 ms, peak 424) ends before the 10-s loop reacts; under load it cut (19:27
  -> 150.5). BUG fixed in `2026-11-20f`: a ceiling drop counted as a cut -> a streak started at +3.5 ms, real cuts after it read as
  useless -> the old higher limit back + cuts paused at peak (`useless` 2). Only cuts with delay > SQM_DELAY_MS count; a restore
  never goes above the ceiling.
- 2026-11-20 `2026-11-20e` + CAPTEST_SHARE_DOWN=1.15 verified (Ookla, Yettel 69006 + Vivacom 1917): SHAPED 185 / 23.8-24.0 Mbit/s,
  loaded +1..+4 ms down (peaks 65-82), +3..+5 up (peaks 40-62) vs UNSHAPED 208-212 / 28.6-29.6, loaded +35..+40 ms (peaks
  959-1874 down, 1040-1083 up). 88 % of the line down for clean latency (was 143.5 = 70 %). Up stays 0.92 (~82 % of the line).
- 2026-11-20 ceiling calibration: shaped Ookla 148 / 24 (loaded +6.7 ms, peak 78) vs UNSHAPED 205 / 28.8 (loaded 115 ms avg, peaks
  836 / 1285 ms); the modem's iperf3 medians 156 / 27.5 -> it reads ~76 % down, ~95 % up. `2026-11-20e`: CAPTEST_SHARE_DOWN /
  _UP (default CAPTEST_SHARE); env CAPTEST_SHARE_DOWN=1.15 -> the 12:30 ceiling 143.5 -> ~179, up unchanged (25.3).
- 2026-11-20 13:24-13:40 LM Studio download (`2026-11-20c`, logger every 2 s): latency data in every busy row, also right after a
  redeploy UNDER LOAD (13:28:59-13:29:33: the first row after it held the ceiling 143.5 with latency); the streak fix live: cuts at
  15-22 ms with +5 % ticks between them (143.5 -> 103.7 -> ... -> 107.1), at 13:35:38 judged useless -> back to 143.5, cuts paused,
  at the ceiling to the end. Rate p50 146 (morning 79), delay p50/p90/max 7.7/13.3/22.4 ms, 6 cut ticks (morning 103). Open
  (cosmetic): the restore tick's why says "+5 % -> 143.5" instead of "useless cuts, back to 143.5". `2026-11-20d` live 13:43.
- 2026-11-20 12:33 smoke test of `2026-11-20b` (deployed during the download): "holding: no latency data" at the inherited 103.8 of
  the 150 ceiling - a restart under load had no idle baseline. Fixed in `2026-11-20c`: the baseline is kept in state.json `sqm_base`
  (atomic, <= every 10 min while idle; `2026-11-20d`, was a separate file in 20c) and read at start.
- 2026-11-20 12:00-12:29 big download (Hugging Face, `tools/sqm_watch.py` every 2 s): the download limit slid 151.8 -> 35 (the floor)
  in 13 min - 103 cuts at ~16 ms "latency", +5 % ticks between them reset the cut-proof streak so it never caught them; the delay
  at < 50 Mbit/s (p50 10.9, p90 17) was WORSE than at >= 130 (6.8 / 13.2): radio / path jitter, not our queue - ~70 % of the
  speed lost for nothing. Fixed in `2026-11-20b`: a streak = cuts < 2 min apart. CPU: >= 90 % in 3.5 % of busy and 17 % of idle
  2-s samples, p50 58 % at 146 Mbit/s - load-independent, no delay effect.
- 2026-11-20: env for one night: `LOCK_PAUSE=23:30-08:00` (unlocked from 23:30 -> the exact minute B1/ARFCN 100 goes dark; so far
  only 10-28 ~01:02 is clean, the 00:50 unlock reboot hid it since) + `NIGHT_SCREEN=01:30-05:00` (tests start after B1 is off).
  CETIN so far: only ARFCN 100 (B1 20 MHz, every PCI, several sites) is off at night; B3/B7/B20 + n78 stay; back 06:04-08:24,
  different each day (load-triggered wake?). `tools/sqm_watch.py` = the shaper's 10-s decisions to test-logs/sqm-*.csv.
- 2026-11-20 09:42-10:39 `2026-11-20a` live check of the iperf-tail fix (`/lock-test?cells=340@1550,340@3350&night=1`, idle line):
  the 10:19:21 sample carried 340@1550's own iperf (3.3 MB/s up) - the run went on (11-20 02:27 the same pattern ended the night).
  Baseline 91.7 (168/26.9, 100 %, p95 53, 7-carrier CA on 3350/340 + n78 534); 340@1550 70.4 (74.2/25.9, 91.7 %, p95 67, B3+B1,
  SINR -2, site 1243 = the lock's site: no B cell); 340@3350 88.6 (157/28.9, 91.7 %, p95 57, same CA as the baseline, site 1243).
  Rows are mode night -> tonight's planner skips both. Locks back 10:39.
- 2026-11-20 night (`2026-11-18p`): the plan was right (7 cells: 340@1550, 340@3350, 112@1550, 493@1550, 202/201/36@3350), the band
  scan 02:01-02:08 (B1 noise -128 dBm ignored), baseline 02:15-02:27 = 92.6 (171/27.4 Mbit/s, 100 %, p95 49, n78 534) - then
  340@1550 "stopped by you" at 02:27:12: the 02:27:06 sample carried the baseline's own iperf (554 kB/s up), it arrived after the
  iperf flag was cleared -> the traffic rule ended the night after its baseline. Fixed in `2026-11-20a` (test first), with:
  the run's own iperf counts for IPERF_TAIL_S (90 s) after it ends; no TRIGGERED capacity test inside LOCK_PAUSE (01:22: the
  released lock read as a setup change -> a test every night, the empty night network went into the 21:30 period's ceiling
  (CORRECTION: the night start at ~02:00 is NIGHT_SCREEN's default 02:00-05:00, not this test) - those values age out of its last 5); the day test skips cells with a no-boot-inform row in
  the last NIGHT_NOINFORM_DAYS (434@100, 2026-11-19: 7 min offline). Locks back 08:00-08:03.
- 2026-11-19 `2026-11-18p` live: `GET /night-plan` on the real data - tonight 7 cells (B3/B7): 340@1550 99 %, 340@3350 87 %, 112@1550
  71 % of night samples, + band scan 493@1550, 202/201/36@3350 (-91..-99 dBm); day_only = 202@100, 434@100 (was 206); night_off 100.
  Day-only test 11:5x-12:42 (5G free): baseline 92.2 (171/26.8, 100 %, p95 51); 202@100 (site 1265) 90.6 = 160/27.8, 100 %, p95 55,
  CA B1/B3/B7 on 202, anchor SINR -7 (weak), n78 534 - a B candidate on a 3rd site; 434@100: NO boot inform within 420 s (the modem
  did not come back on it - like 121@100 / 264@100: not usable here). Locks back 12:42.
- 2026-11-19 night (keeper `2026-11-18n`, NIGHT_BANDS=1,3,7): POWER CUT 00:57 -> ~02:15 (samples gap 00:56:51-02:16:51, modem
  uptime 120 s at 02:16:51); the keeper verified the clock at 02:16:49 (the clock rules worked), still LOCK_PAUSE -> no lock
  restore, the band scan ran at once (02:18-02:24, live, no boot; bandwidth references: B1/B3/B7 20, B8 5, B20 10 MHz), a
  triggered capacity test 02:45 (168/27.4). NO lock tests: 3 bugs, fixed in `2026-11-18o` (test first):
  1. `NIGHT_BANDS` compared band_of() "B3" with "3" -> every candidate dropped (340@3350 84 %, 340@1550 99 %, 112@1550 70 % were in
     the plan); 2. the B1 scan "served" 100/393 at -127 dBm with ARFCN 100 off -> counted as night presence (`SCAN_MIN_DBM` -115);
  3. `day_only` held 206 cells (every neighbour seen by day) -> now only cells on an ARFCN OFF at night, >= 50 % of day samples,
     within 10 dB of the ARFCN's best (the Saturday day test would have tested B8/B20 noise). 08:00 restore: one reboot, 340@100 +
     534 active by 08:03.
- 2026-11-18 `2026-11-18n` SQM fully automatic (Boyan: decide in a week, safeguards if needed): useless cuts ask for a capacity
  test, a fresh LOWER test (< 3 h, any period) is the ceiling. Env: `SQM_DOWN_HOURS=` (always shaped), `SQM_MIN_DOWN=35` (UHD IPTV 25
  + a call + margin). The module self-checks (store, sqm, alert, captest) now run in the parallel runner too (`self_checks.py`).
  Judge after a week: busy p95 <= idle + 15 ms, call-ready under load >= 95 %, the lowest down limit, router CPU by day.
- 2026-11-18 13:04-13:58 daytime full scores for the A/B B cells (day mode, your 5G lock 534 kept): baseline 88.7 (172/28.3 Mbit/s,
  call-ready 83 %, p95 54); 66@1550 (site 1554) 89.7 = 159/26.0, 100 %, p95 58, CA B3/66 + B7/66, SINR -1; 434@1550 (site 1072)
  89.9 = 167/26.3, 100 %, p95 65, B3 only, SINR -2. Both on par with the baseline, n78 534 on both -> real B cells (the throughput
  comes from n78). `guard.ab_alt` now picks 434@1550 (89.9), 66@1550 next.
- 2026-11-18: test split - `nr_locktest` -> nr_locktest / _b / _c (shared setup `test/fixtures/nr_locktest_env.py`), `locktest` ->
  locktest / _b (`test/fixtures/locktest_env.py`); own ports (182xx) + temp DATA per part. Every mock modem now detects a reboot by
  the last REBOOT entry itself, not a count (the night + locktest mocks had the same flake). 34 scenarios: 69 s with 10 jobs
  (was 125-138 s); the slowest part is now ~60 s.
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
