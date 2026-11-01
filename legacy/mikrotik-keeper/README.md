# zte-keeper (MikroTik container)

Always-on helper for the ZTE MC889, running as a tiny Alpine container on the MikroTik (RB450Gx4, RouterOS 7.25) next to AdGuard:

1. **Keeps the antenna setup.** The ZTE switches its directional antennas back to omni on every reboot (auto-select off survives).
   The keeper notices a reboot (uptime goes down), waits until the ZTE has been up 60 s and sets again:
   auto-select **off**, antenna 5 **omni**, antenna 6 **directional** (env `ANT_AUTO` / `ANT5` / `ANT6`).
2. **Logs the 5G cell 24/7** (no browser needed): every switch (e.g. 534 -> 127), "no 5G" periods, rx dropouts (-140 dBm),
   plus one sample per minute - CSV files in `disk1/zte-keeper/keeper/data/`.

It never changes cell locks or the network mode. If you change the antenna mode by hand (panel / app), the keeper leaves it
alone until the next ZTE reboot or container restart.

## v2: TR-069 keeper (`python3 -m keeper`, current)

The `keeper` package is our own TR-069 ACS (code map: `docs/ARCHITECTURE.md`). The ZTE's Server URL points to the container (`http://192.168.6.250:7557/`):

- every inform (`INFORM_INTERVAL`, default 60 s) it reads the cellular tree -> `samples.csv`:
  5G cell / RSRP / RSRQ / SINR / RSSI / SSB, LTE anchor, **per-carrier LTE CA** (`B7/3350/340:-91/1 B3/1550/340:-90/8 ...`),
  LTE neighbours (`earfcn/pci:rsrp`), RRC, rates, network delay, temperatures, FOTA state
- `events.csv`: boots, 5G switches (with SINR before), antenna apply, operator check-ins
- after a ZTE boot it logs in to the web page **once**, sets the antennas, logs out - no more 10 s polling,
  so the browser is not kicked out (only once, ~1 min after each boot)
- every `CHECKIN_DAYS` (30, `0` = never) it relays one session to the operator ACS (`YETTEL_URL`); the ZTE answers the
  operator's auth itself, nothing is stored. `CHECKIN_MODE=observe` (default): the operator may read, but every change
  (set values, firmware Download, reboot, factory reset, upload...) is answered "9001 Request denied" and only logged as
  `operator wanted (denied)` in events.csv (with the firmware URL / values, passwords masked). `CHECKIN_MODE=full`: passes
  everything (use once to take a firmware the log showed: set it, delete `data/state.json`, restart). Seen on 2026-10-26: the operator only reads the inform interval (sets it
  to 3600 - the keeper sets 60 again right after), the location and 18 signal/ID values. No firmware or config push.
- not available over TR-069: rx0 / rx1 per antenna (so no -140 dBm dropout events) - the userscript still shows them
- the nightly 02:00 reboot (`Device.REBOOTSCHEDULE`) is left as it is
- firmware guard: sets `Device.ManagementServer.UpgradesManaged=1` (updates only via this ACS, which never sends any;
  env `LOCK_UPGRADES=0` to skip) and logs a `FOTA state` event whenever the ZTE's own updater leaves `version_idle/fota_idle`.
  Operator check-ins: off (`CHECKIN_DAYS=0`) or `observe` mode - either way the operator cannot push firmware

Deploy with `python3 tools/deploy.py deploy` (or `./deploy.sh`): it runs the checks (compile, pytest, userscript syntax), refuses
while a lock test is running, backs up what is on the router to `backups/<time>/` (manifest, hashes, DB snapshot), uploads only the
code (never `data/`; the old single-file `keeper.py` / `mikrotik.py` are removed), restarts the container and verifies the live
version, the OpenAPI contract, the config keys, a fresh sample and the DB - and puts the backup back when the verification fails.
`check`, `status`, `logs`, `backup`, `verify` and `rollback <dir>` are the other subcommands. One-time SSH setup on the router:
`/ip service set ssh disabled=no address=192.168.6.0/24`, upload your `~/.ssh/id_ed25519.pub` in WinBox Files, then
`/user ssh-keys import public-key-file=id_ed25519.pub user=admin`. Test: `ssh admin@192.168.6.1 /system identity print`.
If the upload can't create `devices/` (old SFTP), create the folders once on the router and run it again.

First install / by hand (replaces v1, same container, same envs - `INTERVAL`, `SAMPLE_EVERY`, `YIELD` are no longer used):

1. Upload the `keeper/` folder (all `.py` files, the `devices/` subfolder with `devices/zte/`, `start.sh`) to `disk1/zte-keeper/keeper/`
   (replace; WinBox Files takes folders by drag and drop). Old `keeper.py` / `mikrotik.py` there are unused - delete them.
2. `/container stop [find name="zte-keeper"]` ... `/container start [find name="zte-keeper"]` (first start installs python3, ~10 MB).
3. ZTE web page `#tr069config`: Server URL `http://192.168.6.250:7557/`, Apply. `/log print where message~"zte-keeper"` shows the informs.

Tests: `python3 -m pytest -q` from this folder (every `test/scenarios/*.py` runs in its own process; the fake ZTE web + CWMP are in
`test/`). A keeper change bumps `VERSION` in `keeper/__init__.py`; the business rules are in `docs/BUSINESS_RULES.md` and the project
skill `.dsh/skills/keeper/SKILL.md` (repo root) loads them for the agent.

Optional envs: `INFORM_INTERVAL` (60), `MAX_MB` (30 - per file before it rotates to `.1`; samples.csv grows ~2 MB/day, so ~30 days and at most ~65 MB on disk), `CHECKIN_DAYS` (30, 0 = never), `YETTEL_URL`, `PORT` (7557), `RAW=1` (log every TR-069
message to `tr069-raw.log`, contains IMEI/IMSI and unmasked operator values - debugging only). Back to v1: last line of `start.sh`.

### Brain (`brain.py`)

The keeper measures, analyses and decides; the userscript only shows the results (Keeper panel).

- **Call quality** (the main goal - Teams calls / screen sharing): TCP connect time to `LAT_TARGETS` (1.1.1.1:443, 8.8.8.8:443)
  every 2 s, all the time - also during your real calls, so no speed tests are needed (a few KB a minute). Every sample gets
  `lat_p50`, `lat_p95`, `lat_max`, `jitter_ms`, `loss_pct` and `call_ok` (1 = within Teams limits: p95 <= `CALL_P95_MS` 150,
  jitter <= `CALL_JITTER_MS` 30, loss <= `CALL_LOSS_PCT` 3.4 = at most 1 of ~30 probes)
- **History**: last 48 h in memory, reloaded from samples.csv at start
- **Analysis** `GET /report?hours=24`: call-ready share overall and by hour, latency while you upload vs idle (bufferbloat from your
  own traffic), per 5G cell (minutes, call-ready %, SINR, RSRP, latency, jitter, loss), switches, minutes without 5G, per LTE carrier
  (anchor share, SINR, RSRP), and **findings**: plain conclusions plus
  `RECOMMEND:` lines (e.g. "A/B test LTE lock 340@1550", "NR lock 534") - locks stay your decision
- **Decisions** (`AUTO=dryrun` default -> only `would reboot (dryrun)` events; `AUTO=on` acts; `off`): reboot over TR-069 to re-pick
  the 5G cell when 5G SINR < `MIN_SINR` (3) for `BAD_MIN` (15) min, or no 5G for `NO5G_MIN` (10) min, or (with `PREFERRED_NR`
  set, e.g. `636576/534`) off the preferred cells for 15 min. Limits: not in the first 10 min after a boot, one per
  `REBOOT_GAP_H` (2 h), `MAX_REBOOTS_DAY` (3), only when the link is idle (`IDLE_DOWN_BYTES` 250000 = 2 Mbit/s, `IDLE_UP_BYTES` 62500 = 0.5 Mbit/s - the ZTE's rate counters are bytes/s);
  blocked cases are logged as `reboot held`; the cell after a keeper reboot is logged as `reboot result` (learns where reboots land).
  Never touches the network mode or locks.
- **Switch explainer**: every 5G `switch` event gets a `why` (after a boot / 5G re-added after a drop / LTE anchor changed /
  after idle / weak LTE anchor just before / handover while connected) plus SINR and RSRP before -> after, and says when the network
  picked by RSRP into a worse (interfered) cell.
- **LTE cells**: ranking of every LTE cell in the neighbour list (band, RSRP, how often seen, used in CA or not); a finding lists
  stronger cells the ZTE doesn't use (lock-test candidates). No LTE antenna control exists - the antenna 5/6 modes are n78 only,
  TR-069 has no antenna settings; LTE is steered with the LTE cell / band lock.
- **Antenna sweep** (`devices/zte/antennas.py`): the 4 modes of antennas 5/6 (omni/dir), each 10 s settle + 20 readings on the ZTE web page;
  winner = best median 5G SINR on the cell most modes saw (RSRP breaks ties), only if >= `SWEEP_MIN_GAIN_DB` (1) better than the
  current mode. (The 5G lock test's own sweep differs: it internet-tests every mode and the quality decides - see the lock test.) `AUTO=on`: the winner is kept (state.json) and set after every boot; dryrun: recommends and puts the current mode back.
  Every `SWEEP_DAYS` (7) when idle and the ZTE is up > 15 min, or on demand (`POST /antenna-sweep`, panel button). ~2 min; the
  browser's ZTE session is logged out meanwhile. Results: `antenna_sweeps.csv`, `GET /antenna-sweeps`, an `antenna sweep` event.
- **Lock test, LTE and 5G** (`locktest.py`): `POST /lock-test?cells=434@1550,340@100` (LTE, panel: "Run LTE lock test") or
  `POST /lock-test?rat=nr&cells=534@636576,747@629988` (5G, panel: "Run 5G lock test"). One cell per entry; a typo, a `;` list or an
  unknown 5G band is refused with 400 (never silently dropped). Runs a baseline (LTE: unlocked; 5G: the
  anchor pinned, the 5G leg free), then each cell: LTE = that cell locked; 5G = that cell locked **with the LTE anchor pinned**
  (`anchor=PCI@EARFCN`, else the keeper's LTE lock, else the serving cell), so only the 5G leg changes between the candidates; both
  legs go into one lock (one reboot on the ZTE). On the ZTE: lock + reboot (`LTE_LOCK_CELL_SET` / `NR5G_LOCK_CELL_SET` /
  `REBOOT_DEVICE` on the web page), wait for the boot inform; on the ATL: one RouterOS script, no reboot (the interface restart).
  `LOCKTEST_SETTLE_S` (180) settle, `LOCKTEST_MEASURE_MIN` (12) of samples -> `GET /lock-tests`: share of minutes on the locked cell (`on_target_pct`), anchor SINR/RSRP/RSRQ, CA combination, which 5G
  cells came with it and their SINR/RSRP/CA, call-ready %, latency, jitter. Rows carry `rat` (lte / nr), `nr_arfcn` (the ARFCN the 5G
  leg was on, so even the unlocked baseline has a capacity) and `antennas` (the mode the step measured with). A step without samples
  keeps its values empty (`minutes` 0, no loss / SINR), and `GET /lock-tests` reports the running or **last** test (`rat`, `anchor`,
  `plan`), so a finished 5G test is not shown as `lte`. The lock is ALWAYS removed at the end (both legs for a 5G test; and at the next
  start if the keeper was stopped mid-test); keeper reboots and sweeps pause meanwhile. ~17 min per LTE cell, ~19 min per 5G cell with
  the antenna sweep.
  **Two modes** (`align`): `align=1` (on-go) waits after each lock until you aligned the antenna for that cell (peak guide: LTE SINR
  for an LTE test, 5G SINR of the locked / serving cell for a 5G test), then `POST /lock-test-continue?heading=118` (panel:
  "Aligned - measure now"). `align=0` (fixed position) does not wait: the antenna stays where it is - for an already aligned antenna
  (several 5G cells in the same direction) or when the position must stay the best one for the LTE anchor; `heading=108` records that
  position with the rows. `POST /lock-test-skip` / `POST /lock-test-stop` (panel: "skip this step" / "stop the test") end the current
  step / the whole test; what was measured so far is kept (`mode` cut, note "skipped/stopped by you"), the lock is removed at the end
  as always. No "aligned" within `LOCKTEST_ALIGN_TIMEOUT_MIN` (90) -> the test stops, lock removed.
  **5G test only, ZTE only, in both modes (the baseline too):** each of the 4 antenna modes is tested with a short
  internet test (`INTTEST_MB` = 2 MB from `INTTEST_URL`, over `INTTEST_S` = 30 s per mode; the download is a load generator that
  raises the NSA 5G leg) while the latency probe runs, and the mode with the **best internet quality** is left set for the
  measurement: call-ready %, loss %, latency p95, jitter - and only then 5G SINR / RSRP as a tie-breaker. A mode replaces the current one only on a clear win (call-ready +5 pp, loss -1 pp,
  p95 -10 ms, jitter -10 ms; signal +1 dB only when the quality is a tie), so a noisy window does not flip it; the row records the
  mode used (`antennas`). Modes that saw another 5G cell don't count. If no mode sees the cell, the step is skipped with its reason
  (`cell not reachable: no 5G on <cell>`), no 12-minute measurement. The kept (boot) mode changes only when the winner is
  auto-applied, with its mode. Close the
  ZTE page / app during a 5G lock test (the sweep needs the web login); during an LTE test it may stay open.
  **Readiness before a step:** a rebooted CPE's web and WAN come back minutes later, so after the boot the keeper waits for the CPE
  web (`LOCKTEST_READY_TIMEOUT_S` 180; `CPE_READY_TRIES` 3 answers, `CPE_READY_GAP_S` 5 s apart) before the sweep / alignment,
  and for a stable link (`LOCKTEST_ONLINE_TIMEOUT_S` 180, `ONLINE_STABLE` 5 good probes) before measuring. A timeout is written on
  the row and the step goes on (the same wait protects the antenna apply after a boot: `ANT_READY_TIMEOUT_S` 300).
  **A cell that is not up is skipped, not measured:** after each antenna mode the keeper checks whether the target 5G cell is
  actually there (over the last few reads, so a single network handover does not look like "the cell is gone"); if not, it sends wake
  traffic for `WAKE_S` (60 s; a 1000-byte ping to `WAKE_HOST` 1.1.1.1, a small-download fallback) and checks again. A mode is
  invalid when the cell never appears there, when the 5G cell switches `SWEEP_MAX_NR_SWITCHES` (3) times in that mode, or when the
  ZTE refuses the antenna set after `ANT_SET_TRIES` (3; each retry logs in again, because an open browser page takes the single web
  session) - the row records the exact goforms and the next mode is tried. If no mode is usable, the cell's step is recorded with
  its reason (`cell not reachable: ...`, `5G cell switching: ...`, `antenna control failed: ...`) and the test moves on: no
  12-minute measurement of a cell it cannot see or control. A web / antenna-control failure skips the baseline too (a "not measured"
  reference is no reference: the ranking then has no baseline and nothing is auto-applied against it); a 5G-free or switching
  baseline is measured - it is the reference. The test always pushes a summary (also when nothing was ranked, naming the cells it
  could not see), and the antenna mode is applied once per boot (a modem sends two informs per boot; the second no longer starts a
  second apply).
  **Unstable LTE anchor = no 5G result:** the 5G leg follows its anchor, so a step where the serving LTE cell changes after the
  settle (`LOCKTEST_MAX_ANCHOR_SWITCHES` 0 = any), the pinned anchor serves < `LOCKTEST_MIN_ANCHOR_PCT` (80) % of the minutes, or
  there is a > `LOCKTEST_MAX_GAP_MIN` (3) min gap without data (management lost) is stored with `unstable` 1 and the reason - but
  excluded from `/anchors?rat=nr` and the auto-apply, and the Telegram message lists it. The per-mode internet test is gated the same
  way: an LTE switch during a mode's window invalidates that mode (all modes invalid -> the step is measured as is, noted).
- **Locks over TR-069** (`LOCK_VIA=tr069`, default): the first time a lock is needed, the keeper learns how the ZTE shows a lock in
  `Device.Cellular.Interface.1.LockLTECell` (sets one on the web page WITHOUT reboot, reads it over TR-069, removes it again -
  nothing changes on the radio) and stores the format (`lte_lock_fmt` in state.json). From then on lock / unlock =
  SetParameterValues + TR-069 Reboot in the same session; the web page is only the fallback (value refused / format not learnable).
  The web page stays needed only for the antenna modes (TR-069 has no antenna settings).
- **Cell lists don't work on the MC889** (measured 2026-10-27): `LockLTECell` accepts `340,100;340,3350` over TR-069 but keeps only
  `340,100`. So locks are one cell (`PCI@EARFCN`); fallback is the lock guard's job (release + restore).
- **Fast mode**: while the keeper acts (lock test, lock change, learning, a queued TR-069 write) the inform interval is
  `FAST_INTERVAL` (10 s), otherwise `INFORM_INTERVAL` (60 s); samples stay one a minute (`MIN_SAMPLE_S` 55).
- **Best anchor / best 5G cell** (`GET /anchors?rat=lte|nr`): latest lock-test result per candidate of that RAT; only cells the modem
  really stayed on (>= 80 %); ranked for calls. The 5G ranking is **internet quality first** (call-ready %, loss %, latency p95, jitter,
  then 5G SINR / RSRP, capacity last), so a cell cannot win on signal alone; the LTE ranking keeps call-ready %, no-5G minutes, signal
  and anchor capacity. The finding recommends the winner only if it clearly beats the unlocked baseline (the same margins as the
  antenna mode; LTE: call-ready +1 %, 5G SINR +2 dB or anchor SINR +3 dB); a baseline whose newest row was not measured (a skipped
  step) is no baseline - then nothing is auto-applied, only recommended. Unstable 5G rows (`unstable` 1) are not ranked at all.
  A finished 5G test auto-applies its winner when `AUTO=on`
  (the 5G lock only - the pinned LTE anchor stays your decision) and keeps the winning step's antenna mode, so the next boot
  re-applies both. When signal and internet disagree, the Telegram message names the cell signal alone would have picked.
- **Antenna mode to keep** (`POST /antennas?auto=0&ant5=4&ant6=4`, `?none` = back to the env; panel: "keep it in the keeper" under the
  userscript sweep): saved in the keeper, re-applied at every ZTE boot, overrides `ANT_AUTO` / `ANT5` / `ANT6` (1 omni, 4 dir).
  The log and the `antennas kept` event say who kept it - the panel or the lock test's auto-apply (`auto-applied 5G lock <cell> and
  its measured mode`).
- **Per-cell antenna mode** (`locktest.cell_mode`): the boot apply uses the **locked 5G cell's** newest good lock-test mode when
  `AUTO=on` (`dryrun` only names it in `/status` and the log, `off` ignores it); otherwise the kept mode, else the env. So a per-cell
  result overrides the default only while that cell is locked, and the default returns when it is not. "Good" = a completed
  lock-test step, `unstable` 0, on-target >= 80 %; there is no age or heading gate (your choice - the newest good record wins), but
  the record's date and heading are shown in `/status` (`config.antennas_cell` / `config.antennas_source`, the panel's 5G lock line)
  and in the `applied` event, so you can spot a mode measured at an old location. After a move, re-run the test for the cell.
- **No mobile service** (the ZTE reports technology `None`, LTE `255/255`): the LTE lock guard releases a lock after `LOCK_NOSERVICE_MIN` (3) min;
  without a lock the auto reboot (`AUTO=on`) reboots after `NOSVC_MIN` (5) min. Seen 2026-10-28 01:03-01:12 while locked to 340@100.
- **No 5G while idle is normal** (NSA: the network adds the 5G leg when data flows, releases it when quiet): `no5G` minutes count
  as a fault only with traffic (`IDLE_DOWN_BYTES` / `IDLE_UP_BYTES`) - for the auto reboot, the 5G lock guard, lock tests (`no5g_min`) and the report.
- **Download ceiling** (`/status` -> `capacity`, panel: "signal allows ~N Mbit/s"): reference only - per carrier TR 36.942 x bandwidth
  (LTE from the CA list, n78 100/20 MHz per Yettel's blocks, n78 x 0.74 TDD download share) x layers (assumed 2 LTE / 4 n78), before load.
  Upload is not estimated: the ZTE reports no uplink SINR.
- **Lock guard, LTE + 5G** (`POST /lte-lock?cell=340@100`, `POST /nr-lock?cell=534@636576`, `cell=none` removes; panel: "lock it (guarded)"):
  a lock is set when the link is idle (lock + reboot; LTE and 5G due together = one reboot, TR-069 `LockLTECell` / `LockNr5gCell`
  `534,636576,78,30`, web page as fallback). Released at once: LTE after `LOCK_NOSERVICE_MIN` (3) min without service or
  `LOCK_BAD_MIN` (10) min off the cell / calls at risk on anchor SINR <= 0; 5G after `NR_LOCK_LOST_MIN` (10) min off the cell or no 5G
  (the LTE lock stays). Restored when idle, not before `LOCK_RETRY_H` (1) h, once the cell is back: LTE = in the neighbour list at its
  usual RSRP; 5G = served by it again (unlocked) for `NR_BACK_MIN` (3) min. At most `LOCK_MAX_REBOOTS_DAY` (4) lock reboots a day.
  A lock test pauses the guard and re-applies the locks afterwards (a 5G lock stays during the test).
  `LOCK_PAUSE` (e.g. `00:50-06:10`): the LTE lock is released before this window and restored after it once the cell is back - for a
  cell the operator switches off at night. Measured: B1 340@100 gone 00:59-06:0x on 2026-10-28 and 29 (3 min without service each night
  before the release).
- **Router clock after a power cut**: the RB450 has no battery clock; RouterOS boots with an old time and NTP fixes it only once the
  ZTE has internet (2026-10-30: booted at "10-29 18:00", real 06:25, fixed 75 s later). The keeper trusts its clock only while the
  modem's Inform `CurrentTime` agrees (`CLOCK_MAX_SKEW_S`, 300; 0 = don't check); until then LOCK_PAUSE and the night screen wait,
  events `clock not verified` / `clock verified` mark the window (records in it carry the old time). `/status` -> `config.clock`.
  Health releases (no service, wrong cell) don't need the clock. Intervals (sampling, "no inform" watchdog) use a monotonic clock.
- **Self-test scenarios** (read-only health checks, pushed to Telegram; not the lock tests, which reboot): `GET /checks` lists the
  scenarios (`clock`, `link`, `sqm`, `lock`, `night`) and the last run; `POST /checks?name=all` (or a comma list) runs them now and
  pushes one message. `SELFTEST_AT` runs them daily, e.g. `SELFTEST_AT=09:45=all,21:00=link` (local time). Each check is ok / warn / fail.
- **5G secondary carriers** (NR CA, TR-069 `NR_CA.n.*`): samples column `nr_ca` (`629988/747:-90.7/5@100` = ARFCN/PCI:RSRP/SINR@MHz);
  the registry counts them per cell as `ca_min` / `ca_sinr` / `ca_rsrp`.
- **Cell registry** (`cells.json`, `GET /cells`): every LTE cell the ZTE measures (RSRP), and when used: SINR, bandwidth, minutes in
  CA / as anchor, the real site (eNB/cell from the ZTE), lock-test result; 5G cells served (minutes, SINR, RSRP). Built from the
  samples on first start, kept for good. Findings: `NEW:` a strong cell that appeared after watching started, `GONE:` a long-seen
  cell missing for > 24 h -> re-run the lock test. `POST /cells/forget?cells=500/341,3150/341` drops cells that don't belong to this device's registry. Your own `data/sites.json` (optional, reference only) adds site name, distance
  and bearing from the ZTE to the cells whose site the ZTE reported.
- **Night screen** (`NIGHT_SCREEN` 02:00-05:00, `off` disables; `NIGHT_MAX` 3, `NIGHT_IDLE_MIN` 30, `NIGHT_RETEST_DAYS` 7,
  `NIGHT_CELLS` always-re-check list e.g. `340@3350`): once a night, after 30 idle minutes, the keeper lock-tests the current lock (baseline)
  plus `NIGHT_CELLS` (home set, same weekly throttle) plus up to 3 strong B1/B3/B7 cells from the registry not tested in 7 days, then puts the
  lock back. Traffic stops it. Night rows (`mode` night) are an unloaded network: they never enter the daytime ranking. It pushes the full
  ranking to Telegram and **recommends** a daytime test, but **never auto-switches** - the night network has B1 (your lock's band) switched off
  and no load, so a night "winner" can lose by day. A night run whose baseline was not measured (no service on the lock's cell) gives no
  verdict (`WARN: the baseline was not measured`); the finding names the cells it could not measure instead of showing empty numbers.
  Rows are kept, but `/lock-tests` returns only the last 40, so the newest night `run` is what the finding uses.
  Cells a night test found dead (a *measured* row, 12 min, no service) in the last 30 days are skipped; a step that never measured
  (no boot inform, the CPE web / the link did not come back) is no evidence and is retried next time.
  **Auto-apply happens on the daytime lock test instead** (`POST /lock-test`, real load): when it finishes, with `AUTO=on` the keeper locks the
  winning cell if it clearly beats the unlocked baseline and the result is fresh, and pushes what it did to Telegram. Review the night Telegram,
  run a daytime test for a promising cell, and it applies itself.
- **The app's data in the keeper** (`GET/POST /userdata?name=...`, `GET/POST /cell-ids`): saved positions, antenna sweeps, cell
  experiments and learned cell ids (LTE eNB/sector + 5G NCI) live in keeper.db; the browser keeps a cache. On load the app pulls the keeper's
  copy (pushes a list only the browser has, once), every save is pushed. The keeper also learns the serving LTE cell id from TR-069 CELLID.
- **Storage** (`store.py`): SQLite `keeper.db` in the data dir (Python stdlib, nothing to install). Samples, events, lock tests and
  sweeps are JSON records, so new fields never split files again. Old CSVs (incl. `.1`/`.vN`) are imported once and left in place.
  Housekeeping once a day (and right after start), so the router's disk never fills: samples older than `KEEP_DAYS` (90) go, the app's
  lists keep their newest 20 saves, and while the data is over `DB_MAX_MB` (150) the oldest day of samples goes (today's always stays).
  Freed space is reused, so the file stops growing; it only shrinks with a vacuum. `GET /db`: size, free disk, records per device / kind
  (oldest / newest), the imported CSVs (safe to delete). `POST /db/prune?days=30&kind=samples&device=*` deletes older records
  (`kind` samples | events | lock_tests | antenna_sweeps | ui, `days` >= 1); `&vacuum=1` shrinks the file (needs free disk ~ its size,
  pauses the keeper a few seconds); `&csv=1` deletes the imported CSVs. CSV export: `GET /export?kind=samples&from=...&to=...`.
  Files in the data dir: keep `keeper.db` (+ `-wal`, `-shm`), `state*.json`, `cells*.json`, `cell-ids.json`, `sites.json`, `keeper.log`;
  safe to delete: `samples.csv*`, `events.csv*`, `lock_tests.csv*`, `antenna_sweeps.csv*` (imported), v1's `switches.csv`, `tr069-raw.log*` (RAW=1 only).
- **API docs**: Swagger UI at `http://192.168.6.250:7557/docs` (loads swagger-ui from cdn.jsdelivr.net), spec at `/openapi.json`,
  both generated from `ROUTES` in api.py; `test/scenarios/api.py` fails if an endpoint is handled but not listed. POSTs act on the ZTE.
- **API** (JSON, CORS open, same port 7557; writes: `POST /antenna-sweep`, `POST /lock-test?rat=lte|nr`, `POST /lte-lock`, `POST /nr-lock`):
  `/status` (verdict good/fair/poor, current values, findings), `/report?hours=N`, `/events?n=N`, `/antenna-sweeps`,
  `/lock-tests?rat=lte|nr`, `/anchors?rat=lte|nr`, `/cells`. Open `http://192.168.6.250:7557/status` in a browser to check.

- **MikroTik ATL 5G as the uplink** (`DEVICE=mikrotik`, default `zte`; one device per container run - the other one is ignored, a
  ZTE inform is answered nothing and logged once): RouterOS `/tr069-client set enabled=yes acs-url=http://192.168.6.250:7557/
  periodic-inform-enabled=yes periodic-inform-interval=1m`. Reads TR-181 `Device.Cellular` + the `X_MIKROTIK_` values (`mikrotik.py`);
  records are tagged per device in keeper.db (`device` column), device-only fields start with `mikrotik_`. Every `SCAN_MIN` (10, 0 = off)
  a cell-monitor scan (4G + 5G with ARFCN) feeds `cells-mikrotik.json`. Locks: `POST /lte-lock`, `POST /nr-lock` -> the keeper sends a
  RouterOS script (TR-069 Download, only `AT+QNWLOCK` allowed, `GET /mikrotik/script-N.alter`) - `X_MIKROTIK_LteCellLock` is refused (9003).
  The modem forgets locks on reboot: the guard sets them again after every boot. Lock state `state-mikrotik.json`, cell list
  `cells-mikrotik.json`; learned cell ids (`cell-ids.json`) and the app's data are shared. Lock tests run here too (`POST /lock-test`,
  both RATs): a step is one script and NO CPE reboot, so it never waits for a boot inform (the Internet drops only for the LTE interface
  restart). Antennas, the antenna sweep and the night screen are ZTE only. The ATL's SIM has a data plan: only the LAN TR-069 traffic is free, so set `LAT_TARGETS=off` while it is the
  test device (no probe). Failover is manual: move the cable / route, change `DEVICE`, restart the container.
  Summer house (the ATL alone, no RB450 = no keeper): nothing to set up - the ATL runs unlocked on its own, its TR-069 client
  just can't reach the ACS (harmless); the keeper's data has a gap for that time. No operator check-in is ever relayed for the ATL.
- **Adaptive SQM** (`sqm.py`, off until `SQM_URL` is set; needs the latency probe, so not with `LAT_TARGETS=off`): a CAKE queue on the
  main MikroTik whose limit the keeper moves every `SQM_TICK` (10) s - latency `SQM_DELAY_MS` (15) above the idle baseline while a
  direction is > 50 % loaded -> that direction -15 %; loaded > 75 % and latency near the baseline -> +5 %; within `SQM_MIN_UP` / `SQM_MAX_UP`
  (5 / 26) and `SQM_MIN_DOWN` / `SQM_MAX_DOWN` (20 / 150) Mbit/s. So uploads (screen sharing, cloud sync) don't push your call's latency up
  when the cell is weak. `/status` -> `config.sqm` shows the last rate, delay and limit. One-time setup on the main router:
  ```
  /queue type add name=cake kind=cake cake-diffserv=diffserv4 cake-nat=yes      # upload
  /queue type add name=fqc kind=fq-codel                                        # download: CAKE fills one CPU core at ~140 Mbit/s
  # the RB450 routes and NATs (ether5 = WAN, the MC889 is a bridge): the queue sees routed WAN traffic only
  /interface bridge settings set use-ip-firewall=no     # LAN<->LAN bypasses the IP firewall: hardware offload stays on
  /queue simple add name=sqm target=192.168.6.0/24 max-limit=26M/150M queue=cake/fqc
  # (no "lan" exemption queue: with use-ip-firewall=no bridged traffic never reaches /queue)
  /user group add name=sqm policy=read,write,api,rest-api        # REST logs in through the API service: needs api too
  /user add name=keeper-sqm group=sqm address=192.168.6.250/32 password=...   # RouterOS can't limit a user to /queue
  ```
  Undo: `/queue simple disable [find name~"^(sqm|lan)$"]`. Measured 2026-10-31 on the routed WAN (OVH 1 Gb single stream + ping
  1.1.1.1, latency above the ~12-16 ms idle RTT, p95 / max):

  | | throughput | latency p95 / max | note |
  |---|---|---|---|
  | upload unshaped (limit 0) | 28.2-28.4 Mbit/s | 33 / 73 ms | CPE radio queue in control |
  | upload CAKE 26M | 25.2 Mbit/s | 26 / 35 ms | shaper is the bottleneck -> tail improves |
  | download unshaped (limit 0) | ~178 Mbit/s | - | 150M is 84 % of it |
  | download fq_codel 120M | 112 Mbit/s | 58-80 / 113 ms | two runs, queued 1.0-1.4 MB |
  | download CAKE 120M | 109-111 Mbit/s | 62-93 / 102-152 ms | no better than fq_codel |

  So: upload CAKE at `SQM_MAX_UP=26` (above ~28 the shaper never becomes the bottleneck), download fq_codel at `SQM_MAX_DOWN=150`;
  `SQM_DOWN_HOURS=Mon-Sun 07:00-24:00` shapes the download in call hours only (outside: unlimited, full speed).
  `/status` -> `config.sqm.now.cpu` = per-core load; `./cpu.sh` logs it every second.
  Env: `SQM_URL=http://192.168.6.1/rest`, `SQM_USER=keeper-sqm`, `SQM_PASS`, `SQM_QUEUE` (sqm), `SQM_IFACE` (ether5 = the WAN),
  `SQM_MAX_UP=26`, `SQM_DOWN_HOURS` (e.g. `Mon-Sun 07:00-24:00`; empty = always), `SQM_INSECURE=1` only for https with a self-signed certificate.
- **Telegram alerts** (`alert.py`, off until both are set): `TG_TOKEN` (from @BotFather), `TG_CHAT` (your chat id: send the bot a message,
  then open `https://api.telegram.org/bot<token>/getUpdates`). Messages: lock released / restored / missing, reboot queued / held,
  ZTE silent (no inform), FOTA activity, night-screen recommendation, calls at risk (>= 10 busy minutes of the last 15, under 80 %
  call-ready). Each kind at most once per `ALERT_GAP_MIN` (30). A message that fails (no internet, modem rebooting) is sent again within a minute, with its original time. The token is never logged; `/status` -> `config.alerts` shows the last 5.

### Code, docs, tests (development on the Mac)

- Layout and how to add a feature: `docs/ARCHITECTURE.md`. Device specifics live only in `keeper/devices/` (`zte/`, `mikrotik.py`);
  everything else is device neutral. Every module / public function has a docstring.
- `pip install pytest pdoc` (dev only - the container needs nothing but python3), then:
  `pytest` (all scenarios, each in its own process, ~2 min), `python3 test/scenarios/guard.py` (one scenario with its output),
  `pdoc keeper -o docs/api` (HTML docs from the docstrings, front page = ARCHITECTURE.md; `pdoc keeper` = live preview).
- v1 shell keeper tests (need `dash`): `python3 test/v1/run_test.py`.

## v1 (`keeper.sh`)

### Files

| File | Goes to | Purpose |
|---|---|---|
| `keeper.sh` | `disk1/zte-keeper/keeper/` | the loop (POSIX sh + curl + openssl) |
| `keeper/*.py`, `keeper/devices/` | `disk1/zte-keeper/keeper/` | v2: TR-069 ACS package (python3 stdlib), started with `python3 -m keeper` |
| `start.sh` | `disk1/zte-keeper/keeper/` | container entry: installs python3/tzdata once, starts the keeper |
| `install.rsc` | paste into the terminal | creates veth, envs, mount and the container |

## Install

1. **Upload** `keeper.sh` and `start.sh` to `disk1/zte-keeper/keeper/` (WinBox or WebFig -> Files -> upload; create the folders if needed).
2. **Pick a free IP** in the same subnet as the AdGuard container: `/interface/veth print` shows agh's address and gateway.
   Put it into `keeperIp` at the top of `install.rsc`.
3. **Paste `install.rsc`** into the terminal (written for RouterOS 7.25: envs/mounts `list=`, container `mountlists=` / `envlist=`). It copies agh's gateway, joins agh's bridge (or adds a masquerade rule towards
   192.168.6.3 if agh is routed), adds the envs and the container. Wait until `/container print` shows it *stopped* (image pulled and extracted). If it prints a NOTE about `registry-url`, alpine is pulled from Docker Hub:
   `/container/config set registry-url=https://registry-1.docker.io` (AdGuard most likely already uses it).
4. **Add the login yourself** (not shown anywhere else, never paste it into a chat):
   - Firefox with the ZTE page open and auto-login enabled -> Developer Tools -> Storage -> Cookies -> `http://192.168.6.3` ->
     copy the value of `admin_password_hash`, then
     `/container/envs add list=zte key=ZTE_PASS_HASH value="<the value>"`
   - or `/container/envs add list=zte key=ZTE_PASS value="<router password>"` (the keeper hashes it).
   Either one logs in to the ZTE, so treat it like the password: anyone with admin access to the MikroTik can read container envs.
5. **Start:** `/container start [find name="zte-keeper"]`
6. **Check:** `/log print where message~"zte-keeper|logged in|applied"` should show `start ...`, `logged in`, `applied: auto=0 ant5=1 ant6=4`.

## Logs

`disk1/zte-keeper/keeper/data/` (download from Files):

| File | Content |
|---|---|
| `switches.csv` | time, ZTE uptime, `switch` / `rx_dropout`, from, to, 5G SINR just before, LTE cell, LTE SINR |
| `samples.csv` | one line per minute: 5G cell, SINR, RSRP, RSSI, rx0, rx1, LTE cell, LTE SINR, LTE RSSI, network type, 5G lock |
| `keeper.log` | logins, reboots detected, settings applied |

Each file (both CSVs and `keeper.log`) keeps one older copy (`.1`) when it passes ~2 MB, so the folder never grows past ~12 MB.
`samples.csv` grows ~115 KB/day (one ~80-byte line per minute), so it rotates about every 17 days; `switches.csv` and `keeper.log` only grow on events.
Send `switches.csv` + `samples.csv` after a day or two and the switches / dropouts can be compared with the 107 deg numbers.

## Using the ZTE web page while the keeper runs

The ZTE allows **one web session**: logging in on the router page ends the keeper's session and vice versa.
The keeper gives way: when it loses its session (setup already applied, ZTE still connected) it logs
`session taken by the router web page - pausing 900s` and stays logged out for `YIELD` seconds (default 900 = 15 min).
No samples are written during the pause. A reboot during the pause (ZTE unreachable / PPP down) ends the pause
so the setup is still put back. After the pause it logs in again, which logs the web page out.

- Longer browser sessions: raise `YIELD` (e.g. `3600`), or stop the keeper while you work:
  `/container stop [find name="zte-keeper"]` ... `/container start [find name="zte-keeper"]`.
- Right after a reboot (setup not applied yet) the keeper does not give way: it logs in, applies, and only then yields.

## Change settings

Edit the env (e.g. `ANT6` `4` -> `1` for omni, `ANT_AUTO` `1` to give control back to the ZTE), then
`/container stop [find name="zte-keeper"]` and `start` again - the keeper applies the new setup at start.

## Remove

```
/container stop [find name="zte-keeper"]
/container remove [find name="zte-keeper"]
/container/envs remove [find list=zte]
/container/mounts remove [find list=zte-keeper]
/interface/bridge/port remove [find comment="zte-keeper"]
/ip/firewall/nat remove [find comment="zte-keeper"]
/interface/veth remove [find comment="zte-keeper"]
```

## Notes

- The ZTE's antenna readback only echoes the last command, so the keeper cannot read the current antenna state - it re-applies after
  each reboot it detects (and once at container start).
- Login: `password = SHA256(SHA256(password) + LD)`, every setting carries `AD = SHA256(SHA256(wa_inner_version + cr_version) + RD)` -
  the same as the router page. A wrong hash is tried once, then the keeper waits 10 minutes (no lockout).
- Tested against a simulated ZTE (`test/`): login, apply, switches, "no 5G", rx dropout, reboot -> re-login -> re-apply after 60 s, wrong hash back-off; v2 `test/run_test_py.py`: boot apply + logout, switches, interval, operator relay with 401,
  giving way to a web-page login (`run_test3.py`, one session like the real ZTE) and ending the pause on a reboot.
