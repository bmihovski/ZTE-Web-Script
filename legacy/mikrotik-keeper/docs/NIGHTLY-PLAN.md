# Nightly cell tests - plan (decided with Boyan 2026-11-18, not built yet)

Goal: the best home-office link (Teams/Zoom/VoWiFi, remote desktop, downloads, IPTV) - measured, not advertised. Router + modem
are powered off at night except an armed test night (`/night-screen?arm=1`, Friday reminder) or research (`NIGHT_ALWAYS=1`).

## Build order

0. **First: parallel test runner in Docker** (`tools/test_docker.sh`, run on the Mac - OrbStack): one `python:3.13-slim` container per
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
1. Then items 4-5 below (throughput per step is done; the weighted score), then 1-3, 6, 7.

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
5. **Weighted score (0-100)**, fixed ranges: download 25 (0-200 Mbit/s), upload 10 (0-30), call-ready 20 (%), loss 15 (0-5 %, lower
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
