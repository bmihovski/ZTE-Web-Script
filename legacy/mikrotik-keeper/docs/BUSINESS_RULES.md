# Keeper business rules

The keeper is our TR-069 ACS for the home uplink CPE. It monitors the link, keeps the chosen cells locked, runs lock tests and
the night screen, and puts the antenna mode back after a reboot. This file is the authoritative description of the rules the code
implements; change it together with the code and the scenarios. The API is generated from `ROUTES` in `keeper/api.py` and served
at `/docs`.

## Devices and data

- **One uplink device per run:** `DEVICE=zte` (ZTE MC889, production) or `DEVICE=mikrotik` (MikroTik ATL 5G, backup). A CPE of
  the other maker pointed at the keeper is answered and left alone.
- **Measurements are per device.** Every record in `keeper.db` carries a `device` column; `store.add` / `store.rows` default to
  the active `DEVICE`, so a ZTE row never decides a MikroTik result and vice versa. The state file (`state.json` /
  `state-mikrotik.json`) and the cell registry (`cells.json` / `cells-mikrotik.json`) are per device. Learned cell ids
  (`cell-ids.json`) are network facts and shared; the app's `ui:` lists are shared too. `?device=*` on `/export`/`/db` is explicit.
- **No network-mode changes.** Yettel is NSA only; never `Only_5G`, never `net_select`. Locks are a human decision; the keeper
  recommends them, then guards the lock you chose.
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
  (`LOCK_MAX_REBOOTS_DAY`). `LOCK_PAUSE` releases the LTE lock before a nightly window and restores it after (the operator switches
  the B1 cell off at night).
- On the ZTE a lock is applied at boot only: one lock change = one `SetParameterValues` + reboot, and both legs (LTE + 5G) go in
  the same change. The LTE lock value format is learned once (`LockLTECell`), otherwise the web page is the fallback.
- On the ATL a lock is a RouterOS script with `AT+QNWLOCK` (`common/4g` / `common/5g`) and an LTE-interface restart, no CPE
  reboot; the ATL forgets locks on reboot, so the guard sets them again.
- Lock state lives in `state.json` under `lte_lock` / `nr_lock` (`cell`, `state` pending/active/released, `reason`).

## Lock tests (`POST /lock-test`)

- **Modes** (`align`): `align=1` (on-go) waits after each lock for `POST /lock-test-continue?heading=N`; `align=0` is the fixed
  position (no wait; `heading=` is recorded with the rows).
- **Steps:** an unlocked baseline, then each cell (one lock + one reboot on the ZTE; one script on the ATL). A 5G test **pins the
  LTE anchor** for every step (`anchor=` → the keeper's LTE lock → the serving cell) so only the 5G leg changes. The lock is
  always removed at the end (night: the guard's lock is put back).
- **Readiness:** after the reboot, wait for a BOOT inform, `LOCKTEST_SETTLE_S` (180), then for the CPE web (`LOCKTEST_READY_TIMEOUT_S`
  180; `CPE_READY_TRIES` 3 answers `CPE_READY_GAP_S` 5 s apart) and for a stable link (`LOCKTEST_ONLINE_TIMEOUT_S` 180,
  `ONLINE_STABLE` 5 good probes) before measuring. Timeouts are noted, not fatal.
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
  not unstable; the 5G ranking is quality-first, the LTE ranking keeps call-ready, no-5G, signal, capacity. `failed` = off-target /
  not seen; `unstable` = the unstable list.
- **Night screen** (LTE only): in `NIGHT_SCREEN` (02:00-05:00), after `NIGHT_IDLE_MIN` (30) idle minutes with a verified clock, once a
  night: re-tests the current lock (or the unlocked baseline) + `NIGHT_CELLS` + up to `NIGHT_MAX` strong untested cells (registry, main
  bands, not tested in `NIGHT_RETEST_DAYS`, not "dead"), then puts the lock straight back. Traffic stops it. Night rows (`mode` night) are
  an unloaded network: kept for the finding, never in the daytime ranking, never auto-applied. The finding is RECOMMEND when a cell beats
  the baseline, INFO when none did, WARN when the baseline was not measured (a missing baseline is no reference, so no verdict); cells the
  screen could not measure are named, not shown with empty numbers. Rows are kept (lock tests are not pruned automatically); `/lock-tests`
  only returns the last 40, so the latest night run may be mixed with day rows in that list (the finding uses the newest `run`). Only a
  *measured* night row (minutes > 0) with no service counts as a dead cell (skipped for 30 days); a step that never measured (no boot
  inform, the CPE web / the link did not come back) is no evidence about the cell and must be retried. A step that could not even be
  locked carries its `mode` and `run` like the others (a night run stays one unit; the finding then warns about the unmeasured
  baseline instead of treating the next candidate as one).
- **Auto-apply** (`AUTO=on`, real load): the winner is locked when it clearly beats the unlocked baseline (the same quality policy);
  the winning step's antenna mode is kept. No baseline (skipped / not measured) = nothing to beat = no auto-apply, only a
  recommendation. A dryrun only reports; off never acts. The summary is pushed to Telegram for every finished test, also when
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
  only if `SWEEP_MIN_GAIN_DB` better. The lock test's sweep is the internet-tested one above.

## API and docs

- `ROUTES` in `keeper/api.py` is the single description; `/openapi.json` and `/docs` are generated from it, and the deploy tool
  compares the live document with the tree (`contract_diff`). A handled path missing from `ROUTES` fails `test/scenarios/api.py`.
- Read: `/status`, `/report?hours`, `/events?n`, `/cells`, `/samples?from&to&cols`, `/export?kind&from&to&device`, `/anchors?rat`,
  `/lock-tests?rat`, `/antenna-sweeps`, `/db`, `/checks`, `/openapi.json`, `/docs`.
- Writes that reboot the modem: `POST /lte-lock`, `/nr-lock`, `/lock-test`, `/lock-test-stop`, `/lock-test-skip`,
  `/lock-test-continue`, `/antennas`, `/antenna-sweep`, `/cells/forget`, `/db/prune`. Check `/lock-tests` first (a running test).
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
5. **Userscript.** The panel is browser-side (`legacy/zte-script-legacy.js`, loaded by the userscript): the keeper deploy does not
   update it. Bump the console marker and update the installed copy.
6. **Docs.** Update `docs/BUSINESS_RULES.md` (this file), the README and the docstrings, and the project skill
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
