---
name: keeper
description: "Work on the zte-keeper (TR-069 ACS for the ZTE MC889 / MikroTik ATL): lock tests, antenna modes, cell locks, the API/Swagger, tests and deploy. Use for any change to legacy/mikrotik-keeper, its scenarios or the userscript keeper panel."
whenToUse: "Any change, debug, test or deploy of legacy/mikrotik-keeper: lock test / antenna sweep / lock guard / ACS / API changes, a failing scenario, a live keeper check, or the userscript keeper panel."
---

# Keeper

Authoritative rules: `legacy/mikrotik-keeper/docs/BUSINESS_RULES.md` — read it before changing behaviour; keep it, the README,
the docstrings and this skill in sync.

## Layout

- `keeper/` — the package (`__main__`, `api`, `acs`, `monitor`, `brain`, `guard`, `locktest`, `store`, `common`,
  `devices/__init__.py`, `devices/mikrotik.py`, `devices/zte/{__init__,web,antennas,locks,checkin,watchdog,settings,ps}.py`;
  `lowband.py`, `absmoke.py`, `abplan.py`, `abrefresh.py`, `morning.py`).
- `test/scenarios/*.py` — plain assert scripts, each in its own process, run by `test/test_scenarios.py` (pytest). Fake ZTE web +
  CWMP, real ATL dumps in `test/fixtures/`.
- `tools/deploy.py` — check / backup / deploy / verify / rollback against the router. `./deploy.sh` wraps `deploy`.
- `legacy/zte-script-legacy.js` — the userscript with the keeper panel (browser-side, not served by the keeper).

## Invariants

- **Per device, never mixed:** every DB record carries `device`; `store.rows` defaults to the active `DEVICE`. ZTE rules/records
  never drive MikroTik and vice versa. State and cell files are per device; `cell-ids.json` and the app's `ui:` lists are shared.
- **One lock change = one reboot** on the ZTE; both legs in one `SetParameterValues`; the ATL uses one script (no CPE reboot).
- **Quality before signal:** call-ready %, loss %, p95, jitter, then 5G SINR, RSRP; margins 5 pp / 1 pp / 10 ms / 10 ms; the
  signal only breaks a tie. `AUTO=on` acts, `dryrun` reports, `off` ignores.
- **Stability:** a 5G row is `unstable` (not ranked) on an LTE anchor switch, < 80 % on the pinned anchor, or a > 3 min data gap.
- **A missing/unstable cell is skipped, not measured** (no 12-minute wait); the row says why (`cell not reachable: ...`, `5G cell
  switching: ...`, `antenna control failed: ...`). A skipped step has no measured values (no 0 % loss) and a baseline that was not
  measured (web failure skips it too) is not a baseline: the ranking drops it and nothing is auto-applied against it.
- **Per-cell antenna mode requires a lock:** the locked 5G cell's newest good lock-test mode applies at boot with `AUTO=on`; else the
  kept mode (panel/sweep); else the env. One apply per boot.
- Before scoring the ZTE morning baseline or a high-band leg, confirm the configured antenna mode with modem readback. A failed
  baseline check restores the starting profile. The morning check records the mode and the boot it was verified on.
- **A 5G lock is a pair:** each NR step pins its 5G cell's `best_anchor` (an NSA cell is served only under some anchors), the
  baseline pins the best anchor across the tested cells, and a clear winning row auto-applies the 5G cell **and** its anchor
  (`anchor=` overrides the mapping for every cell).

- Explicit ZTE B8/B20 cell locks restrict LTE to that EARFCN's band until replacement or release. The keeper saves the
  previous LTE band list and journals temporary band/cell settings for Stop and restart. Use
  `POST /morning?apply=0&cell=394@3774&nr=534@156510` for a quiet, restore-only comparison. Inspect the radio observations
  for both transfer windows and the final lock readback before interpreting loaded quality. A low-band wake timeout reports
  LTE attachment separately from n28 pairing.

- ZTE band discovery uses fresh observations with the requested band readback and matching serving EARFCN. If the reported
  primary CA cell disagrees with the serving cell, skip the observation. Neighbours and pair evidence stay keyed by full PCI
  and EARFCN, so PCI 112 at 3350 and 3774 stays distinct. On-demand TR-069 reads reuse values covered by a regular subtree request.

## Workflow

1. Add/extend a scenario first (fail it), then the code. If the scenario **patches** a function, also call the real body somewhere —
   the `INTEST_MB` bug shipped because every test patched `brain.internet_test`.
2. Bump `VERSION` in `keeper/__init__.py` (`YYYY-MM-DD<letter>`) — the deploy verify compares it with the live `/openapi.json`.
3. `python3 tools/deploy.py check` (compile + pytest + userscript `node --check`; use `.venv/bin/python3` if needed).
4. `python3 tools/deploy.py deploy` — preflight refuses while a lock test runs; backs up the router code to `backups/<time>/`,
   uploads only the code (never `data/`), restarts, verifies version / OpenAPI contract / config keys / fresh sample / DB, and rolls
   back automatically on failure. `verify`, `status`, `logs`, `backup`, `rollback <dir>` are the other commands.
5. **Validate the run**, not just the deploy: `python3 tools/deploy.py validate --rat lte|nr [--run ID] [--wait] [--expect-lock CELL]
   [--json]` reads one lock-test run and applies the keeper's policy to that run's baseline vs its candidates (unstable / not
   measured / off target listed apart). Verdict `keep` / `apply` / `no-lock` / `no-result` (exit 2); exit 3 on `--expect-lock` mismatch.
   The loop: deploy → start (or schedule) a run → validate → fix/improve → repeat. The run itself is the keeper's: once started it
   needs no Mac, and a scheduled run (harness scheduler) starts without one.
6. Update the userscript copy in the browser when the panel changes (console marker `Loading ZTE Script v...`).
7. Update `docs/BUSINESS_RULES.md`, the README and this skill when a rule changes.

## Traps

- **A/B smoke diagnoses and restores:** `POST /ab-smoke` runs the current pair, selected alternatives and an unlocked control.
  `GET /ab` exposes serving and locked cells, the proposed legs and progress. The panel shows these above the SQM section.
  `POST /ab-smoke-stop` interrupts measurement and restores after an in-flight reboot.
  Report inconclusive when there is no fresh evidence. The smoke never commits a new winner. A persisted restoration request
  survives an interrupted run. A/B alternatives need a complete current-method score, or an observed n28 pair for a low scan.
- For an n78 antenna matrix, use `POST /ab-smoke?antenna_lte=434@1550` and read `/ab` → `antenna_trial`. NR remains automatic
  under both the unlocked and LTE-locked profiles. Check the transfer evidence, per-cell signal and observed NR changes before
  comparing loaded quality. The restore journal covers both the original locks and the effective antenna mode.
- **Power-off workflow:** scheduled night tests, weekly arming, and Saturday confirmation are retired. The cold-boot clock
  and lock guard are still required. Disable `LOCK_PAUSE` in production when the router is off overnight.
- **Clean measurement history:** a one-shot `data/.reset-measurements` startup marker clears samples, results and learned
  quality caches and compacts SQLite. It preserves managed locks, kept antenna modes, shared app lists, site and cell ID
  notes, and capacity inputs for SQM. The new sample layout uses typed cell and metric columns with compact driver JSON.
- **The one-call refresh never overlaps its stages:** the chain waits per stage and aborts if the band scan is still running after
  `REFRESH_SCAN_MAX_S` (1800 s; the 6-band worst case is ~1230 s) instead of starting the smoke on the scan's band lock (2026-11-21p).
  Its Telegram (`abrefresh._msg`) names a no-service band (`B28: no service (...)`), never a row of blanks, and prints
  `nr cell missing` when the scan saw no 5G leg (2026-11-21q). The band rows are **this run's only** (`_bands(run)`, the scan's
  `run` stamp set by `bandscan.start()`): an aborted/failed scan must not print an older scan's rows as fresh (2026-11-22u).
- **A run clears `running` last:** `absmoke._run` sets `st["running"] = None` after `st["result"]`, the state, the Telegram and the
  event; a waiter (the refresh chain's `_wait` -> `st["result"]`, a test, `GET /ab`) that sees the run done must never find an
  empty result (2026-11-22u).
- **The one-call refresh needs the locks released:** its band scan refuses (409) while the keeper holds an LTE/NR cell lock, which
  is the normal state - `POST /lte-lock?cell=none` + `POST /nr-lock?cell=none` first (the 17:26 run was from an unlocked state).
- **Daytime ZTE LTE lock tests:** settle for CA, use a short download to raise NSA 5G, then measure call-ready/p95/jitter/loss in
  the modem's two successful iperf transfer intervals (`load_method=iperf_phases`). `load_s` is their combined estimated duration
  from the Enable acknowledgements. The later idle samples provide stability evidence. A lock needs a complete score at least
  5 points over the same run's unlocked baseline and no clear internet-quality regression. SINR and RSRP are diagnostic.
  The `5g` score component reflects the band. B8/B20 read p95 ~40 ms and 100% call-ready idle, then p95 139 ms, jitter 69 ms and
  0% call-ready under load (2026-11-21).
- **Pair lookups convert the key:** the pair DB is keyed `earfcn/pci` (`1550/434`, the sample format); a lock spec is
  `pci@earfcn` (`66@1550`). `brain.anchor_key()` converts. `absmoke._nr_for` used the spec as the key, so it missed every pair and
  silently used the pre-run sample's 5G cell - the A/B high leg would lock the n28 534@156510 after a band scan instead of the
  anchor's n78 pair (2026-11-22u); morning's pair lookup had the same miss (the backup pair priority never ran). The
  scenarios had seeded the wrong key format and hid it - seed the REAL registry format.
- **Morning check (`/morning`, `morning.py`):** discover the serving cell and up to `MORNING_CANDIDATES` (2) backups per band.
  Backups need two fresh RSRP readings of at least `MORNING_MIN_RSRP` (−110 dBm), and the serving cell needs usable signal.
  Pairs with at least 10 measured minutes, loss ≤ 2% and jitter ≤ 20 ms rank by measured quality, then same site and signal.
  Other neighbours rank by RSRP. Only probe another backup when the previous one cannot measure with 5G. The first working backup
  fills the band's second slot. A locked cell has a 90 s web/online budget. High bands get a 90 s 5G wake window. On B8 and
  B20, one guarded 30 s modem download wakes n28 before scoring. Verify the same exact LTE/N28 pair during the probe or both
  scored transfers and keep the warm-up observations separate. Two reachability failures in
  seven days suppress that exact LTE lock in morning discovery. A successful measurement clears the persisted per-device streak.
  High-band cell locks allow CA. Discover B20/B8 on Saturday or when the unlocked screen is poor or unavailable, and use the fallback
  when every high screen is poor. Low tests allow the natural n28 pairing. `POST /morning?apply=0&low=1` compares all bands under
  full down/up load, records observed radio paths and scores in `/morning` → `trial`, and restores the keeper-managed locks.
  On regular legs, an unavailable HTTPS download rate keeps its reason in `https_error` and leaves successful latency probes
  usable. Per-leg iperf3 fields appear in loaded comparison logs.
  Each ZTE comparison transfer spans two fast informs plus five seconds at minimum. Inspect `trial.coverage` for missing
  bands. A failed configuration change with rollback pending ends the comparison and restores the starting profile.
- **Capacity test traffic guard:** the modem's iperf bypasses the router's WAN port. Defer scheduled runs when that port already
  carries traffic above the idle limits. The in-transfer guard discards a contaminated capacity result or a loaded lock-test
  leg. `/captest` records the latest skipped capacity test under `traffic_skip`. A morning comparison can retry once on the same
  cell after the LAN quiets, and `bands=` limits the comparison to the bands being rechecked.
  The morning start and capacity-probe start share the same modem reservation lock.
- Patching a function in tests hides its real body; add a real-body test.
- The ZTE web has ONE session: an open page logs the keeper out; the antenna set retries 3× with a re-login, and the boot apply
  waits for the web (`CPE_READY_*`).
- A reboot sends several informs; one apply per boot (generation checked after the wait, stale workers stand down).
- No BOOT inform after a lock = the step was never locked (the ZTE applies a lock only at boot): it is recorded `not measured: no
  boot inform within N s` and skipped, not measured for 12 min on a dead link.
- **The ZTE watchdog is web-only:** no TR-069 object (the whole `Device.` tree, 4,982 names, has none; 2026-11-21). An enabled
  watchdog (ping host, reboot after N failures) + a lock to a cell the modem cannot attach to = a reboot loop that looks like
  "no boot inform". `GET /zte/watchdog` reads it (web login; also read after every boot and stored in `state.watchdog`); an
  `watchdog enabled` / `watchdog disabled` event marks a change.
- A band scan needs **no LTE and no NR cell lock** (it refuses an active/pending 5G lock): with the 5G cell pinned the scan sees the
  lock instead of the network's own pair, and a band change can lose it -> the guard re-applies it with a reboot. The scan wakes the
  5G leg with a short load test (`BANDSCAN_TEST_S`) and records the pair + call-ready; B20/B28 are the bad-weather candidates.
- **The web-only PS no-service restart is ON** (`ps_no_service_restart_flag=1`, 2026-11-21): it can restart the modem when service
  is lost - a reboot-loop candidate independent of the watchdog. `GET /zte/settings` audits it (with UPnP, power save, MTU/MSS,
  roaming, the NR band list); `POST /ps-reconnect` is the keeper's own fast disconnect/connect (no reboot) and is tried before an
  outage reboot (`PS_RETRY_MIN` 10, `OUTAGE_PS_MAX_DAY` 3).
- An NSA 5G cell is reachable only under some LTE anchors (747 needs 1550/434, not 340@100): check `/cells` (`anchors`,
  `best_anchor`) before calling a cell unreachable; `POST /cells/refill?days=7` rebuilds the mapping from the samples.
- A lock test removes its lock at the end; the per-cell mode therefore needs `nr_lock` active (AUTO=on applied it, or you did).
- Deploying restarts the container and interrupts a running test; check `/lock-tests` (the tool does).
- The container needs `ca-certificates` for the internet-test download (`start.sh` adds it); a failure is non-fatal (`inttest`).
- **Before a deploy, run `tools/deploy.py review` (the `check` runs it too).** It scans for the 2026-11-03 defect classes: live
  deque/dict iteration (`list(...)`), wall-clock waits (`time.monotonic()`), a loss average with `or 0`, and a sweep `except`
  without a logout. When a new bug class is found, add its pattern + a regression scenario there - the review is the memory of
  the mistakes.
- `/lock-tests` and `/antenna-sweeps` return the last 40 rows; the panel tables scroll. `/lock-tests` `rat`/`anchor` describe the
  running test, else the last one (they are not reset when it ends).
- A 5G row's capacity needs `nr_arfcn` (the lock spec is `none` for the baseline); don't derive it from `lock` alone.
- The night screen gives no verdict (`WARN`) when its baseline was not measured, and names the cells it could not measure; a bad
  `cells=` spec is a 400, never a silently shortened plan.

## Quick commands

```
curl -s http://keeper.lan:7557/status | python3 -m json.tool
curl -s http://keeper.lan:7557/zte/settings | python3 -m json.tool
curl -s http://keeper.lan:7557/low-band | python3 -m json.tool
curl -s http://keeper.lan:7557/ab | python3 -m json.tool
curl -s -X POST http://keeper.lan:7557/ps-reconnect
curl -s 'http://keeper.lan:7557/lock-tests?rat=nr' | python3 -m json.tool
python3 tools/deploy.py status|verify|logs -n 20
python3 tools/deploy.py check|backup|deploy|validate|rollback backups/<time>
```
