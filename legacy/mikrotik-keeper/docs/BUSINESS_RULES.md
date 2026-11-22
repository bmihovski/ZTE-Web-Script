# Keeper business rules

The keeper is our TR-069 ACS for the home uplink CPE. It monitors the link, keeps the chosen cells locked, runs lock tests and
daytime diagnostics, and puts the antenna mode back after a reboot. This file is the authoritative description of the rules the code
implements; change it together with the code and the scenarios. The API is generated from `ROUTES` in `keeper/api.py` and served
at `/docs`.

## Devices and data

- **One uplink device per run:** `DEVICE=zte` (ZTE MC889, production) or `DEVICE=mikrotik` (MikroTik ATL 5G, backup). A CPE of
  the other maker pointed at the keeper is answered and left alone.
- **Measurements are per device.** Every record in `keeper.db` carries a `device` column; `store.add` / `store.rows` default to
  the active `DEVICE`, so a ZTE row never decides a MikroTik result and vice versa. The state file (`state.json` /
  `state-mikrotik.json`) and the cell registry (`cells.json` / `cells-mikrotik.json`) are per device. Learned cell ids
  (`cell-ids.json`) are network facts and shared; the app's `ui:` lists are shared too. `?device=*` on `/export`/`/db` is explicit.
- **Measurement storage.** New minute rows use SQLite columns for LTE/NR channel and PCI and the frequently queried numeric metrics.
  Driver-specific fields and lower-volume history use compact JSON. The API and CSV preserve existing field names and lock syntax.
  The operator can reset measurement history by placing `data/.reset-measurements` and restarting the keeper while no test runs.
  On startup it discards measurements and learned quality caches, compacts SQLite, and consumes the marker before sampling.
  Managed locks, kept antenna modes, site and cell ID notes, shared app lists and the running SQM configuration and capacity
  inputs survive. Code deployment never uploads measurement data.
- **5G cells carry their site (your notes).** `sites.json` may list a site's 5G cells (`{"rat":"NR","pci":...}`); the registry then
  shows each 5G cell's site, distance and bearing from the ZTE, and for every (5G cell, LTE anchor) pair: same site, the two sites'
  distance and the home-bearing difference (no coordinates = the fields are hidden). A co-sited pair that is equally good (comparable
  evidence, neither clearly better) wins the anchor choice - the operator's NSA combo is more likely intentional; a candidate with no
  pair history at a different site is flagged `cross-site NSA, no pair history` in the summary. Informational only: the learned pairs
  and the quality policy decide.
- **A defect class found once is checked forever.** `tools/deploy.py review` (also part of `tools/deploy.py check`) statically
  scans the keeper for the patterns that caused the 2026-11-03 bugs: a live `brain.HIST` deque iteration, a live `REG[...]` dict
  iteration, a wall-clock wait (`time.time()` in a `while ... - t0`), a loss average with `or 0` for missing probes, and a sweep
  `except` without a logout. Add a pattern there (with a test) whenever a new class of bug is found.
- **No network-mode changes.** Yettel is NSA only; never `Only_5G`, never `net_select`. A lock is either your choice or a measured
  clear winner auto-applied with `AUTO=on` (never a cell picked by signal alone); the keeper guards the lock you chose.
- **A 5G cell is only reachable under some LTE anchors.** NSA serves the 5G leg only when the LTE anchor it is combined with
  carries it (measured: `636576/747` appears under `1550/434`, not under `100/340`; the 2026-10-27 434@1550 test served 747 and 127,
  the 340@100 tests saw no 747). So the keeper learns the **(5G cell, LTE anchor) pairs** from the samples: per 5G cell, every
  anchor that served it, with minutes, call-ready %, loss, p95, jitter and 5G SINR/RSRP. `best_anchor` = the pair with the best
  measured quality (call-ready, loss, p95, jitter, then 5G SINR/RSRP; the anchor's own LTE signal only as a tie-break), needing
  `ANCHOR_MIN_MINUTES` (10) minutes of evidence; the refill never creates or resurrects a cell (the registry owns the cells - a
  forgotten cell stays out, a partially formed entry must not break `/cells`; a 5G cell with ARFCN 0 or 255 is a post-boot
  placeholder and is not tracked); among the pairs no other pair *clearly* beats (the quality margins) the one with
  the most minutes wins, so a short lucky pair never overrides a long-standing one. The pair **keys are the sample format**
  `earfcn/pci` (`1550/434`); a lock spec is `pci@earfcn` (`434@1550`) and must be converted with `brain.anchor_key()` before a
  pair lookup - using a spec as the key misses every pair and silently falls back (2026-11-22u). Shown per 5G cell in `/cells` (`anchors`, `best_anchor`), rebuilt from the samples
  on the first start after this exists or on demand (`POST /cells/refill?days=7`); a failed 5G step in the Telegram summary names
  the anchor the cell is known under. "Not seen" under one anchor is not "unreachable".
- **Secrets stay in the container env** (`ZTE_PASS_HASH`, `SQM_PASS`, `TG_TOKEN`); never log them. Only masked fixtures are in git.

## Formats

- `lte_cell` = `EARFCN/PCI`, `nr_cell` = `ARFCN/PCI` or `no5G`; a lock spec is `PCI@EARFCN` / `PCI@ARFCN`.
- `lte_ca` = `B<band>/<EARFCN>/<PCI>:<RSRP>/<SINR>@<MHz>` (anchor first); `nr_ca` = secondary 5G carriers.
- Lock-test rows: `on_target_pct` = share of minutes on the locked cell (LTE: `lte_cell`, 5G: `nr_cell`); `pin` = the LTE anchor
  a 5G test held; `unstable` 1 = the anchor was unstable; `antennas`/`ant5`/`ant6` = the antenna mode the step measured with;
  `nr_arfcn` = the ARFCN the 5G leg was on (the unlocked baseline has no lock spec, so its capacity comes from this). A step without
  samples keeps its measured values empty (`minutes` 0, no loss / switches / SINR) - never a fabricated 0 % loss.

## Locks

- `POST /lte-lock?cell=PCI@EARFCN|none` (guarded LTE anchor) and `POST /nr-lock?cell=PCI@ARFCN|none` (guarded 5G cell). The guard
  releases a lock when its cell is lost, restores it when it is back (idle only, `LOCK_RETRY_H`), and caps lock reboots
  (`LOCK_MAX_REBOOTS_DAY`). `LOCK_PAUSE` (current `23:30-08:00`; the operator switches B1/ARFCN 100 off at 23:30) releases the LTE
  lock - and your 5G lock in the same write (2026-11-21: the night sees the network's own NSA pairs) - before the window and
  restores both together after it. A released lock whose cell is not back yet logs `lock restore pending` (event + log, at most once
  an hour), so the events explain a long wait instead of staying silent between the release and the restore (2026-11-03: released
  00:50, cell back 08:18, restored 08:24).
- On the ZTE a lock is applied at boot only: one lock change = one `SetParameterValues` + reboot, and both legs (LTE + 5G) go in
  the same change. The LTE lock value format is learned once (`LockLTECell`), otherwise the web page is the fallback.
- **The NSA idle release is not a lost lock.** When the link is idle the ZTE releases the 5G leg (`5G 534 -> no5G`) and its
  `LockNr5gCell` read-back is empty while the lock is still configured; `lock_reconcile` skips an idle `no5G` read-back. With traffic
  an empty read-back is a real lost lock and the guard sets it again (2026-11-21: the old code re-applied it and rebooted the modem
  every ~5 min; the boot inform carried `M Reboot`).
- **The ZTE watchdog is web-only:** TR-069 does not expose it (the whole `Device.` tree, 4,982 names, has no watchdog object;
  2026-11-21). The web UI's watchdog (ping a host, reboot after N failures) is `net_link_detect_enable/url/time_gap/ping_times/
  timeout` + `watch_dog_reboot_enable` (set via `WATCH_DOG_SWITCH`). The keeper reads it after every boot in the antenna apply's
  web session (and `GET /zte/watchdog`), stores it in `state.watchdog`, and logs `watchdog enabled` / `watchdog disabled` on a
  change. An enabled watchdog + a lock to a cell the modem cannot attach to = a reboot loop (no WAN -> ping fails -> reboot): it
  explains "endless reboots" that are not the keeper's own, and a lock test run with it on can lose its boot informs.
- On the ATL a lock is a RouterOS script with `AT+QNWLOCK` (`common/4g` / `common/5g`) and an LTE-interface restart, no CPE
  reboot; the ATL forgets locks on reboot, so the guard sets them again.
- Lock state lives in `state.json` under `lte_lock` / `nr_lock` (`cell`, `state` pending/active/released, `reason`).

## Web-only settings and fast PS recovery (2026-11-21)

- TR-069 exposes none of the ZTE's connection / PS / thermal / MTU / UPnP / NR-band settings. `GET /zte/settings` reads them over
  the web (one login; the browser's ZTE page logs out), stores `state.web_settings`, and returns an audit. After every boot the
  same read rides the antenna apply's web session (with the watchdog read).
- Audit warnings: PS no-service restart ON, UPnP ON, auto power save ON, roaming ON, MTU != 1500, MSS != 1460, n78 missing from the
  NR band list, network select != `auto_select`, the watchdog enabled.
- `POST /ps-reconnect`: `DISCONNECT_NETWORK` -> `ppp_disconnected` -> `CONNECT_NETWORK` -> `ppp_connected`, no CPE reboot. It helps
  a stuck PS/data session, not a lock to a cell the modem cannot attach to. `brain.offline_decide` tries it first when the modem is
  registered with signal but has no data connection (`PS_RETRY_MIN` 10, `OUTAGE_PS_MAX_DAY` 3); a failure falls through to the
  outage reboot logic.
- Current (2026-11-21): PS no-service restart **ON**, UPnP **ON**, roaming off, MTU 1500 / MSS 1460, network select `auto_select` /
  `WL_AND_5G`, NR band lock `1,3,7,8,20,28,78` (all bands + n78), thermal empty, watchdog off. The audit flags the first two; the
  PS no-service restart is a reboot-loop candidate (test it off), UPnP is a LAN exposure. The NR band lock already includes n78; a
  n78-only lock is a candidate after a controlled test.
- The PS no-service restart is a **restart source independent of the watchdog**: with it ON, a lock to a cell the modem cannot attach
  to (no service) can restart the data session or reboot before a boot inform; the 2026-11-21 04:00-06:30 `not measured: no boot
  inform` steps are consistent with it. The keeper does not change it (a human decision); the audit flags it.

## Lock tests (`POST /lock-test`)

- **Modes** (`align`): `align=1` (on-go) waits after each lock for `POST /lock-test-continue?heading=N`; `align=0` is the fixed
  position (no wait; `heading=` is recorded with the rows).
- **Steps:** an unlocked baseline, then each cell (one lock + one reboot on the ZTE; one script on the ATL). A 5G test **pins the
  LTE anchor** for every step (`anchor=` → the keeper's LTE lock → the serving cell) so only the 5G leg changes. The lock is
  always removed at the end (night: the guard's lock is put back).
- **Readiness:** after the reboot, wait for a BOOT inform, `LOCKTEST_SETTLE_S` (180), then for the CPE web (`LOCKTEST_READY_TIMEOUT_S`
  180; `CPE_READY_TRIES` 3 answers `CPE_READY_GAP_S` 5 s apart) and for a stable link (`LOCKTEST_ONLINE_TIMEOUT_S` 180,
  `ONLINE_STABLE` 5 good probes) before measuring. Timeouts are noted, not fatal - except a missing BOOT inform: the ZTE applies a
  lock only at boot, so the step was never locked. It is recorded as `not measured: no boot inform within N s` and skipped (no
  settle, no 12-minute measurement of a link that is not there).
- **Measured under load:** settle for the CA (a sample with >= 2 carriers, `LOCKTEST_CA_WAIT_S`
  60; a single-carrier cell is a note, not an error), **raise** the NSA 5G leg with a short download (`LOCKTEST_RAISE_S` 15 from
  `INTTEST_URL`), then run the full down/up (the modem's iperf3, `LOCKTEST_IPERF`) **while the latency probe measures**. The row's
  call-ready % / p95 / jitter / loss for a daytime ZTE LTE test come from the two successful transfer intervals
  (`load_method=iperf_phases`, `load_s` is their combined duration). Each interval starts at the modem's Enable acknowledgement
  and is bounded by the configured test duration and the result read. This estimates when traffic ran and excludes setup, retries,
  time between transfers and later polling. Call-ready is the share of roughly 10 s slices meeting the call limits. Missing probe
  coverage in either direction leaves loaded quality empty. Later samples provide stability evidence (on-target, switches, gap).
  Older rows and other test modes use elapsed down/up time. B8/B20 showed idle p95 ~40 ms and 100% call-ready on 2026-11-21,
  then p95 139 ms, jitter 69 ms and 0% call-ready under load.
- **Per-mode internet test (5G, ZTE):** for each of the 4 antenna modes, check the target 5G cell over the last 3 reads; if it is
  not up, send wake traffic for `WAKE_S` (60 s; `WAKE_HOST` 1.1.1.1, `WAKE_SIZE` 1000, a small-download fallback) and look again;
  then run the internet test (`INTTEST_MB` 2 MB from `INTTEST_URL` over `INTTEST_S` 30 s) while the latency probe runs.
- **Antenna set** failures are retried `ANT_SET_TRIES` (3) times, re-logging in between (a browser page takes the single web
  session); the row records the refused goforms. A mode is invalid when the target cell never appears, the 5G cell switches
  `SWEEP_MAX_NR_SWITCHES` (3) times in it, the antenna control fails, or the LTE anchor switches during it.
- **Mode choice:** the best mode by internet quality is left set for the measurement; the current mode is replaced only on a clear
  win (`brain.clearly_better`). The kept (boot) mode changes only when the winner is auto-applied, with its mode.
- **Quality policy** (`brain.quality_values`): call-ready %, loss %, latency p95, jitter, then 5G SINR, then RSRP; capacity last for
  the cell ranking. A clear win needs call-ready ±5 pp, loss 1 pp, p95 10 ms or jitter 10 ms beyond the margin; signal (+1 dB SINR,
  then +2 dB RSRP) only breaks a quality tie. Internet quality always beats signal when they disagree.
- **Stability:** a 5G row is `unstable` when the serving LTE cell changes after the settle
  (`LOCKTEST_MAX_ANCHOR_SWITCHES` 0 = any), the pinned anchor serves < `LOCKTEST_MIN_ANCHOR_PCT` (80) % of the minutes, or there is
  a > `LOCKTEST_MAX_GAP_MIN` (3) min sample gap (management lost). The row is kept with the reason but never ranked or auto-applied.
- **Skipping:** a cell that is not reachable in any mode (`no 5G`), switches in every mode, or whose antenna control fails in every
  mode is recorded with its own reason (`cell not reachable: ...`, `5G cell switching: ...`, `antenna control failed: ...`) and
  skipped — no 12-minute measurement. A web / antenna-control failure skips the baseline too (a "not measured" reference is no
  reference); a 5G-free or switching baseline is measured (it is the reference). A baseline row without a measurement is not a
  baseline: the ranking drops it, so nothing is auto-applied against it.
- **Ranking** (`GET /anchors?rat=lte|nr`): the latest result per candidate, only if it stayed on the cell (on-target ≥ 80 %) and is
  not unstable. Complete ZTE LTE transfer scores rank first by score. The 5G ranking is quality-first. Each returned row (and
  the baseline) also carries the lock-test score computed when read (`score`, `score_parts`, `score_partial`, `score_gate`); `score`
  is None when the row is partial (a metric - e.g. iperf - was not measured) or gated, because a partial score is no A/B score (the
  A/B high pair ranks by it). `failed` = off-target / not seen; `unstable` = the unstable list.
- **Lock-test rows** also carry `boot_s` (lock -> boot inform), `load_s` (the loaded window the quality was measured over) and
  `down_mbps` / `up_mbps` (the modem's iperf3 as the step's load, at the start of the measuring window after the short raise, still
  locked; `LOCKTEST_IPERF=0` off; never a ceiling). `POST /lock-test?...&night=1` = a hand-picked night screen (LTE). Traffic stops a night run only after `NIGHT_BOOT_QUIET_S` (300) of modem uptime - before that it is the
  modem's own reconnect (2026-11-18 08:56: a B8 step was stopped 3 s after its boot). Night LTE steps free the 5G lock in the same write
  (the anchor's own pairing shows; your 5G lock returns with the LTE lock at the end); daytime LTE tests keep your 5G lock.
- **Lock-test score** (`brain.lt_score`, `/lock-tests` rows: `score`, `score_parts`, `score_partial`, `score_gate`): download 25 (0-200),
  upload 10 (0-30), call-ready 20, loss 15 (0-5 %), p95 10 (20-200 ms), jitter 10 (0-50 ms), 5G 10 (n78 10 / n28 5 / none; from `nr_arfcn` +
  `nr_iperf` = the 5G cells while the step's iperf ran); gates: on the locked cell >= 80 %, a stable anchor. The quality inputs are
  the **loaded** window (2026-11-22v), so the score reflects the internet under traffic, not the idle link. Computed when read.
- **Band scan** (`POST /band-scan?bands=8,1,...`, `keeper/bandscan.py`): per band `LockLTEBand` = that band over TR-069 (no reboot),
  `BANDSCAN_WAIT_S` (90) of samples (until one is on the band, at most 2x), then `BANDSCAN_TEST_S` (25) of load (an internet test) to
  raise the NSA 5G leg; a `band_scans` row (serving cell + median RSRP/RSRQ/SINR, neighbours, the network's own 5G cell + ARFCN/SINR/
  RSRP, and the band's call-ready/loss/p95/jitter/down) + an event; the (5G cell, anchor) pair goes into the NR database (night / A-B
  candidates, incl. the low bands B20/B28). Only without an LTE cell lock **and no NR cell lock**: an active/pending 5G lock is
  refused (it pins the scan's 5G leg and a band change can lose it -> the guard re-applies it with a reboot); busy meanwhile.
- **Low-band A/B phase 1** (`GET /low-band`, `POST /low-band-trial?band=8|20`, `keeper/lowband.py`, 2026-11-21): the low bands
  (`LOW_BANDS` 8,20; B28 has no service here) and the network's n28 5G under them (156510/534 under 3774/394 / 6400/112) are the
  bad-weather fallback. The preview compares the last band-scan row per low band (serving, pair, call-ready/loss/p95/jitter/down)
  with the current high-band no-lock quality using `brain.clearly_better`; a trial is a one-band band scan (`LockLTEBand`, no reboot,
  no cell lock; the network's low-band cell + n28; the internet test; the full list back), only unlocked. Phase 2 (not yet): the
  automatic two-tier A/B - the high-band no-lock is the default; a low quality triggers the low-band band-lock trial; a clear win
  keeps the low-band no-lock; the high band returns when it recovers.
- **A/B smoke** is the on-demand diagnostic in the Keeper panel. Start with `POST /ab-smoke`, read phases, completed legs and
  the last result with `GET /ab`, and stop with `POST /ab-smoke-stop`. The current managed pair is tested first without a reboot.
  The plan includes an unlocked baseline, other anchors with a complete daytime transfer score from the current measurement method,
  and low bands with an observed n28 pair. An unproven LTE anchor lets NR attach naturally. A changed lock requires a BOOT inform,
  a fresh serving-cell observation and fresh probes. A working alternate after the current configuration fails points to the
  configuration. Failed fresh probes on a registered unlocked modem point to the mobile uplink. A reachable uplink supports
  checking the LAN or PC if the problem persists there. Missing evidence gives an inconclusive result.
  Smoke always restores the managed locks. Stop waits for any in-flight reboot and then restores them. A persisted restore request
  lets the guard retry that restoration after an interrupted run. The smoke holds the modem reservation until its result is published.
- **Morning cell check** (`POST /morning?low=1|0`, `GET /morning`, `keeper/morning.py`, `MORNING` 08:00): the daily cell decision.
  One no-lock leg (full CA), then **band-lock discovery** (no reboot) for B1/B3/B7. Allow up to `MORNING_BAND_WAIT_S` (125) seconds
  for two samples after the band write. A neighbour needs two fresh usable RSRP readings (default at least −110 dBm), and the
  serving cell also needs usable signal. Discovery returns the readings and exclusion reasons. Each band queues up to
  `MORNING_CANDIDATES` (2) backups. A measured 5G pair needs `ANCHOR_MIN_MINUTES` (10) minutes, loss at or below
  `MORNING_PAIR_MAX_LOSS` (2%) and jitter at or below `MORNING_PAIR_MAX_JITTER` (20 ms) to get priority. These pairs rank by pair
  call-ready, loss, p95 and jitter, with same site and signal as tie-breakers. Other neighbours rank by RSRP. The B1 `100/202` pair
  measured 3.01% loss and 28.87 ms jitter on 2026-11-22 and both PCI 202 frequencies failed to get online that morning.
  Lock #1 and the first backup individually (cell locking keeps CA) for a 25 s probe. Try the next backup only when the current
  one cannot measure internet with a real 5G cell observed during its wake/probe. Each failed attempt is recorded. Locked legs
  share `MORNING_DEAD_S` (90 s) between web readiness and fresh online probes, then wait up to `MORNING_5G_WAIT_S` (90 s) under
  wake traffic for 5G. Expiry skips the download and leaves quality empty. After `MORNING_DEAD_N` (2) consecutive reachability
  failures (offline or no 5G) in `MORNING_DEAD_DAYS` (7), skip the exact `PCI@EARFCN` for morning locks until the evidence expires.
  The cache persists per device, a successful measurement clears the streak, and control errors add no failure evidence.
  A = the best measured #1, B = the best measured backup. A band with only one measured leg contributes it to A. A new pair must
  clearly improve on a usable unlocked baseline. If no configuration qualifies, restore the keeper-managed locks. The regular
  probe uses a short download and latency checks. On Saturday (`MORNING_LOW_SATURDAY`), or when the unlocked screen fails or is poor,
  discover B20 then B8 while the CPE is unlocked. The automatic fallback tests low only when all high screens fail the quality
  bar (down < `MORNING_LOW_DOWN` 20, binary call-ready below `MORNING_LOW_CALL` 90, or unavailable), then stops after the first low
  band that meets it. `low=1` and Saturday test both low bands. Low tests let NR attach naturally and require n28, and low backups
  need a discovered LTE neighbour with recorded n28 pair evidence. One Telegram; once a day at/after `MORNING`, a missed day as the
  first thing; busy (the guard/captest/night wait). A typical run takes 8 reboots (~25 min), or 12 with the low bands (~40 min).
  A failed first backup can add one reboot per band.
- **Morning comparison** (`POST /morning?apply=0&low=1`): test the unlocked baseline, high bands and both low bands with full
  down/up measurements over the two iperf transfer windows. It records discovery, reachability, observed radio samples, speed,
  loaded quality and score under `/morning` → `trial`. A locked candidate needs two radio samples, at least 80% on its LTE anchor
  with the same NR cell, and agreement from any sample recorded during a transfer. The comparison uses the daytime LTE score
  rules and requires matching iperf servers against the baseline. Competing LAN traffic can trigger one retry on the same cell
  after three quiet WAN checks. `bands=` selects configured bands for a focused comparison. The run restores managed locks,
  keeps a separate result and leaves the production morning failure cache alone.
- **One-call refresh** (`POST /ab-refresh`, `GET /ab-refresh`, `keeper/abrefresh.py`, 2026-11-21): the full band scan (B1,B3,B7,
  B8,B20,B28) then the full A/B smoke, then **one** Telegram with both (the smoke's own Telegram is suppressed in the chain); ~25 min;
  the wife's one request. The band scan refuses while an LTE or NR **cell lock** is held, so the refresh 409s in the normal locked
  state: release the locks first (`POST /lte-lock?cell=none` + `POST /nr-lock?cell=none`, or a LOCK_PAUSE/test window) - it ran
  2026-11-21 17:26 only because the keeper was unlocked then. `GET /ab` embeds the refresh. The band scan refreshes the B1/B8/B20 rows + the NR database; the smoke uses
  the refreshed rows and the lock-test A/B scores (A = the max scored, B = the next); the low pair is the newest **B8 and B20** row
  (the scan also writes B1/B3/B7, which are high bands and not low candidates); the smoke restores the managed locks. The chain does the band scan
  first and only then the smoke (the stage is `band scan` -> `smoke` -> `done`); it waits per stage and **aborts** instead of
  starting the next one when a stage is still running after its ceiling (`REFRESH_SCAN_MAX_S` 1800 - above the 6-band worst case
  6 x (2 x `BANDSCAN_WAIT_S` + `BANDSCAN_TEST_S`) ~ 1230 s; `REFRESH_SMOKE_MAX_S` 2400), so the smoke never takes a cell lock on
  top of the scan's band lock. The Telegram never prints a row of blanks: a band with no sample is named as such (`B28: no service
  (no sample on the band)`), and a band whose scan saw no 5G leg says `nr cell missing` instead of an empty `+ ,` (2026-11-21q).
  The band rows are **this run's only**: every band-scan row carries the scan's `run` stamp (`bandscan.st["run"]`, set
  synchronously by `start()`), and `_bands(run)` drops a band this run did not write - an aborted or failed scan never prints an
  older scan's rows as fresh (2026-11-22u).
- **Development mode** (`POST /dev?on=1`, the state `dev`, 2026-11-21): disables the automatic A/B and allows the tests outside their
  windows; the normal service mode is `dev` off. The wife can switch it on and keep working while an issue is fixed later.
- Band scan bandwidth watch: each band's serving carrier bandwidth (`bw_mhz`, as logged '@N') is compared with its last scan; a change
  -> one Telegram note (the refarm signal: B8 5 -> 10 MHz after the 3G release). Unknown bandwidth or a first scan: no note.
- Quality A/B (`guard.quality_ab`, bad call-ready with good signal on an idle link, once a day): trial 1 = the B cell
  (`guard.ab_alt`): a daytime lock-test result (night rows never count), 5G paired in that test, ANOTHER site (eNB) than the lock,
  seen in the last 6 h; best complete loaded score under the current method. Trial 2 = everything unlocked (the modem's choice).
  EVERY A/B reboot (the start, the next arm, the winner back, your locks back) waits until no one is in a CALL: blocked while every
  one of the last `AB_QUIET_MIN` (5) samples carries >= `AB_CALL_BYTES` (4000 B/s) in the same direction - a
  Teams / Zoom call is steady (~5-12 kB/s, also down when muted), update / mail / browsing bursts are not (2026-11-20: up p50 0.6
  kB/s, steady >= 4 kB/s in 8.5 % of 5-min windows). The current minute must be idle as well. A missed trial is fine; a dropped call is not.
- **Overnight operation.** The router and modem can be powered off overnight or when no service is needed. There is no scheduled
  night screen, arm reminder, or Saturday confirmation test. Keep clock verification and ordinary outage recovery for cold boots.
  `LOCK_PAUSE` is optional for sites that need to release a lock while a cell is off. The production power-off setup leaves it disabled.
  Historical night-mode results remain readable and are excluded from daytime decisions.
- **Auto-apply** (`AUTO=on`, real load): a daytime ZTE LTE candidate needs a complete transfer score at least 5 points above that
  run's unlocked baseline, and its internet quality cannot be clearly worse under the call/loss/p95/jitter margins. The highest
  eligible score wins. SINR and RSRP remain diagnostics. A missing or partial loaded baseline gives no decision. For a 5G test,
  the quality policy decides whether the winner clearly beats its baseline.
  a 5G winner is applied as the **pair** - the 5G cell and the LTE anchor it was measured with (an NSA cell is served only under some
  anchors, so the cell alone would not reproduce the result) - and the winning step's antenna mode is kept. The pair may move the LTE
  lock only to an anchor measured as a daytime LTE anchor and not clearly worse than the current lock (call-ready -5 pp, anchor
  SINR -3 dB); otherwise the 5G cell alone is applied and the refusal is reported (a 5G pair must not move the LTE lock to a weak
  anchor). The baseline pins the
  best anchor across the tested cells; without an explicit `anchor=` each 5G cell pins its `best_anchor`. The anchors are resolved
  once at the start of the test (the run's own samples update the pairs, so a per-step re-resolve could flip a pin mid-run and make
  the comparison cross-anchor - seen 2026-11-02). No baseline (skipped / not
  measured) = nothing to beat = no auto-apply, only a recommendation. A dryrun only reports; off never acts. The summary is pushed to Telegram for every finished test, also when
  nothing is ranked (it names the cells it could not see).

## Antenna modes

- Modes are antennas 5/6: `1` omni, `4` directional (n78 only). `ANT_AUTO`/`ANT5`/`ANT6` are the env defaults.
- **Applied at boot:** `effective()` = the locked 5G cell's newest good lock-test mode when `AUTO=on` (see below), else the kept
  mode from the panel/sweep, else the env. One apply per boot (the ZTE sends two informs per boot; a superseded worker stands down).
- **Per-cell mode** (`locktest.cell_mode`): the newest lock-test row for the locked cell with `rat=nr`, the cell match, stored
  `antennas`/`ant5`/`ant6`, `unstable` 0, on-target ≥ 80 %, mode not cut/night. No age/heading gate (your choice); the record's date
  and heading are shown (`config.antennas_cell` / `antennas_source`, the panel, the `applied` event). It applies only while that 5G
  cell is locked (`nr_lock` active/pending) with `AUTO=on`; there is no per-cell mode without a lock. After a move, re-run the test.
  `POST /antennas` and the auto-apply both go through `antennas.keep`, whose log / `antennas kept` event says who kept the mode
  (`set from the panel` vs `auto-applied 5G lock <cell> and its measured mode`).
- The scheduled/manual antenna sweep (`POST /antenna-sweep`) is a different thing: 4 modes by median 5G SINR, kept with `AUTO=on`
  only if `SWEEP_MIN_GAIN_DB` better. Current `SWEEP_DAYS=0`: on demand only (the env default is 7). The lock test's sweep is the
  internet-tested one above.

## Capacity tests and the shaper (SQM)

- **Shaper** (`sqm.tick`, every 10 s): busy + latency above `SQM_DELAY_MS` -> limit -15 %; busy + latency < half of it -> +5 %; idle ->
  back up toward the ceiling. Cuts that do not lower the latency (a streak of 4: cuts less than 2 min apart, +5 % ticks between
  them included) are undone, cuts pause 10 min (`earn_keep`) and
  a capacity test is asked for (the line may have lost capacity: rain, a busy cell).
  `/status sqm.now.why` and the panel say why the limit is what it is; a limit that only follows a changed learned ceiling (new
  period or test) says `new period ceiling X (was Y)`, an idle limit at the ceiling `idle, limit parked at the ceiling X`.
- **Ceiling per period** (`captest.ceilings`): `CAPTEST_SHARE_DOWN` / `_UP` (default `CAPTEST_SHARE` 0.92) x the median of the last 5 counted tests of that period of the
  day, 2 needed, capped at the plan (`SQM_PLAN_DOWN`/`UP`, 200/30); without them `SQM_MAX_*`. Upload only with `CAPTEST_UP=1`.
  Calibration 2026-11-20: the modem's iperf3 (4 streams down / 1 up) reads ~76 % of the line down, ~95 % up (unshaped Ookla 205 / 28.8
  vs medians 156 / 27.5) -> `CAPTEST_SHARE_DOWN=1.15` (~179, ~87 % of the line), up stays 0.92.
  When the 2 newest counted tests of the last `CAPTEST_FRESH_H` (3) hours, any period, are BOTH lower, the higher of the two wins
  (0.92 x it); ONE low test changes nothing but asks for a confirmation test `CAPTEST_CONFIRM_S` (600 s) later - inside the 1-h gap,
  within the daily budget, after 10 idle minutes, never in LOCK_PAUSE (2026-11-20 sweep: one peak-hour test swings +-25 %).
  the plan) and its test at +10 %. Extra tests (setup change, deep cuts, useless cuts, climb): `CAPTEST_TRIGGER_MAX` 6 a day, >= 1 h apart.
  Current `CAPTEST_SLOTS=10:30,12:30,18:30,21:30`; `SQM_MAX_DOWN/UP=185/28`,
  `SQM_MIN_DOWN=35`, the plan 200/30.
  Never a triggered test inside `LOCK_PAUSE` (the released lock is no setup change; the empty night network is no ceiling).
- **Tests** = the modem's own iperf3 (TR-143 cannot measure on the MC889 B07): download 4 streams, upload 1 stream (the MC889 gives no
  upload result with 4). Scheduled at `CAPTEST_SLOTS` incl. night slots, only after 10 idle minutes, never while busy (lock test,
  night screen, sweep, A/B trial) or < 15 min after a boot. Optional Iperf3 settings the modem reports read-only (TR-143 probe,
  Writable flags) are never written; `tools/ip3_args.py` dry-runs the settings against them.
- **Local traffic during capacity tests:** with RouterOS telemetry configured, use the routed WAN port's rates to separate LAN
  traffic from modem-originated iperf. Minute peaks and a recent rate can defer a scheduled probe within its slot. Check the WAN
  directly before and during each measured transfer. Traffic above the existing idle limits (default 2 Mbit/s down or 0.5 up),
  or a failed traffic read, discards the attempt. `/captest` shows `traffic_skip` separately from the last usable result. Skipped
  attempts do not feed the ceiling history. A contaminated loaded comparison or lock-test step provides no loaded score.
- **Morning and capacity tests share the modem.** Both reserve it under the same start lock. A scheduled capacity slot that finds
  the morning job busy keeps its eligibility until its usual deadline. The morning comparison's own iperf belongs to the morning
  job and does not enter the capacity history.
- **Counted vs check:** `POST /captest` without options counts for the current period; any option (`secs`, `conn`, `reverse`,
  `server`) makes it a diagnostic - shown, never in a ceiling.
- **Climb** (`sqm.climb`): busy at the ceiling with clean latency for `SQM_CLIMB_S` (600 s in total) -> the ceiling x1.05, again and
  again up to the plan; the first latency rise while busy -> back to the learned value; a new learned value restarts at x1.
  `SQM_MAX_*` is only the start ceiling of a period without tests; the plan is the hard top.
- **Triggered tests** (`captest.trigger`): the setup (LTE anchor | last 5G cell) changed and held 15 min, the climb passed +10 %, or 3
  cuts below 70 % of the ceiling within an hour. Idle only, >= 1 h after the last counted test, none within 1 h before a slot,
  `CAPTEST_TRIGGER_MAX` (6) per calendar day (local; `GET /captest trigger.today` counts the same way). They count for the current period; records keep `setup` and `trigger`.
- **Weekly summary** (`captest.weekly`, `CAPTEST_WEEKLY` Mon 09:05, empty = off): one Telegram per ISO week - counted tests (extra),
  down/up range and median, ceilings per period, the shaper's busy / at-ceiling minutes and their latency vs idle, cut minutes, climbs,
  router CPU >= 90 % minutes and the last spike. Sent at the first minute at/after the time that day (a power cut delays, not skips it).
- **Phases 3 / 4 closed by data (2026-11-17, 5744 shaped minutes over 4 days):** latency (minute max above idle) median idle 9.7 ms,
  busy (>= 50 % of the limit) 10.8 ms, at the ceiling 11.7 ms - own traffic does not raise the delay at the learned ceilings, so there
  is no rate to lower a ceiling toward (phase 3); day (100/340) and night (3350/340) setups differ < 2 % (phase 4). Reopen phase 3 when
  the weekly summary shows busy latency >= idle + 10 ms for weeks; phase 4 when two setups' medians differ > 10 %.
- **CPU spikes** (`sqm.cpu_spy`): a core at >= 90 % -> RouterOS `/tool/profile` for 2 s, top 3 processes kept in
  `/status sqm.now.cpu_spikes` and the keeper log (`router CPU spike`), at most every 10 min. 2026-11-13..17: 100 % every ~8 min in
  hour-long windows, mostly idle - not the shaper's traffic.
  A permission error (`not enough permissions`, the SQM_USER group lacks the policy) turns the probe off until the next restart,
  said once in `cpu_spikes` and the log; other errors retry after 10 min. An answer with nothing to rank is kept raw
  (`no profile data (raw: ...)`, 300 chars) so its format can be read. Skipped in the keeper's first 2 min (its own start load);
  each spike line ends `keeper N %` = the keeper's own CPU (all threads, % of one core) since the last tick. API requests taking
  `SLOW_REQUEST_S` (2) or more are logged `slow request: GET /report?hours=24 3.4 s` - the ZTE panel polls ~9 endpoints every 60 s.
  `/status` (its `findings_24h`) and `/report` share `brain.report_cached`: the 24 h analysis (~2.5 s of a router core) at most once
  per 5 min (it feeds no automatic decision - display only), and a burst of requests waits for the one computation in progress (2026-11-17 20:09: the ZTE app
  start sent ~16 `/status` in 6 s - 14..51 s each); the keeper's own logic keeps calling the fresh `brain.report`. The userscript's
  `keeper_get` shares an identical GET while it is in flight (v 2026-11-17-#31).
- **Telegram:** only a moved ceiling (`capacity ceiling`, with "up stays N (measured M)" while the upload is not learned); single
  tests and climbs are events / `/captest` only.

## API and docs

- `ROUTES` in `keeper/api.py` is the single description; `/openapi.json` and `/docs` are generated from it, and the deploy tool
  compares the live document with the tree (`contract_diff`). A handled path missing from `ROUTES` fails `test/scenarios/api.py`.
- Read: `/status`, `/report?hours`, `/events?n`, `/cells`, `/samples?from&to&cols`, `/export?kind&from&to&device`, `/anchors?rat`,
  `/lock-tests?rat`, `/antenna-sweeps`, `/db`, `/checks`, `/tr069?path` (read-only GetParameterValues of one name/subtree, or
  `names=1|all` for GetParameterNames one level below / the whole subtree's names - discovers what the firmware exposes without
  dumping values), `/zte/watchdog` (the web-only watchdog; logs in, so the browser's ZTE page logs out), `/zte/settings` (the
  web-only settings + audit; logs in too), `/openapi.json`, `/docs`.
- Writes that reboot the modem: `POST /lte-lock`, `/nr-lock`, `/lock-test`, `/lock-test-stop`, `/lock-test-skip`,
  `/lock-test-continue`. `POST /antennas`, `/antenna-sweep`, `/cells/forget`, `/db/prune` do not reboot; `POST /ps-reconnect`
  drops the PS data session for seconds (a fast reconnect, not a reboot). Check `/lock-tests` first (a running test).
  `POST /lock-test?cells=` is one cell per entry (`PCI@EARFCN`, `rat=nr`: `PCI@ARFCN`); a typo, a `;` list or an unknown 5G band is
  refused with 400 (never silently dropped).
- `GET /lock-tests?rat=` returns `running`, `phase`, `plan`, `align`, `rat`, `anchor`, `waiting`, the last 40 results; `rat` and
  `anchor` describe the running test, else the last one (they stay until the next test starts, like `plan`), so a finished 5G test
  is not reported as `lte` with no anchor. `/anchors?rat=` returns `baseline`, `ranked`, `failed`, `unstable`.

## Development workflow

1. **Tests first.** Add or extend a scenario under `test/scenarios/` (each runs in its own process; fake ZTE web + CWMP, the real
   ATL dumps). A scenario that **patches** a function (e.g. `brain.internet_test`) must be paired with a real-body test of that
   function — the `INTEST_MB` typo shipped because every test patched it.
2. **Version.** Bump `VERSION` in `keeper/__init__.py` (`YYYY-MM-DD<letter>`) on every keeper change; the deploy verification
   compares it with the live `/openapi.json`.
3. **Local gate:** `python3 tools/deploy.py check` (compile + pytest + userscript `node --check`).
4. **Deploy:** `python3 tools/deploy.py deploy` (or `./deploy.sh`): preflight (refuses while a lock test runs), backup to
   `backups/<time>/` with a manifest, upload only the code, restart the container, verify version / OpenAPI contract / config keys /
   a fresh sample / the DB, and roll back automatically on failure. `verify`, `status`, `logs`, `backup`, `rollback` are separate.
5. **Validate a run:** `python3 tools/deploy.py validate --rat nr|lte [--run ID] [--wait] [--expect-lock CELL] [--json]` reads one
   lock-test run and applies the same policy to that run's baseline vs its candidates (unstable / not measured / off target listed
   separately): verdict `keep` / `apply` / `no-lock` / `no-result` (exit 2), exit 3 on an `--expect-lock` mismatch. The loop is
   deploy -> start or schedule a run -> validate -> fix / improve -> repeat; the run itself is the keeper's, so it needs no Mac once
   started (a scheduled run via the harness scheduler starts it without one).
6. **Userscript.** The panel is browser-side (`legacy/zte-script-legacy.js`, loaded by the userscript): the keeper deploy does not
   update it. Bump the console marker and update the installed copy.
7. **Docs.** Update `docs/BUSINESS_RULES.md` (this file), the README and the docstrings, and the project skill
   `.dsh/skills/keeper/SKILL.md`, when a rule changes.

## Traps we hit

- Tests patching the real function → the real body is never executed; add a real-body test.
- The ZTE web page has ONE session; an open browser page logs the keeper out. The antenna set retries with a re-login; the sweep
  readiness wait protects the boot apply.
- A reboot sends several informs (BOOT, M Reboot, PERIODIC, VALUE CHANGE), sometimes seconds apart: one apply per boot, generation
  checked after the long wait.
- The sweep's busy flag must be set before any wait (the readiness wait once allowed a scheduled and a manual sweep to overlap).
- A lock test removes its lock at the end; a per-cell mode therefore needs `nr_lock` to be active (auto-applied or set by you).
- The deploy restarts the container: a running lock test is interrupted (the next start clears its locks). The tool refuses unless
  `--force`.
- TLS for the internet test: the container needs `ca-certificates` (`start.sh` adds it); a failed download is non-fatal and shows in
  `inttest`.
- The API returns the last 40 rows for `/lock-tests` and `/antenna-sweeps`; the panel's tables scroll.
- A band scan needs **no LTE and no NR cell lock**: with the 5G cell pinned the scan sees the lock, not the network's own pair, and a
  band change can lose it -> the guard re-applies it with a reboot. The scan wakes the 5G leg with a short load test and records the
  pair + call-ready; `NIGHT_BANDS` must include the low bands (current 1,3,7,8,20,28) or the night plan filters them out.
- `NIGHT_RETEST_DAYS` (7) is the home-set re-check; `nightplan.RETEST_DAYS` (30) is the full-test skip. They are different knobs.
- The PS no-service restart is ON and is a restart source independent of the watchdog; the audit (`/zte/settings`) flags it; the
  watchdog and PS restart can both reboot the modem when a lock loses service.
