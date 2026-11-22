# AGENTS.md — ZTE-Web-Script / keeper

Durable project memory. Read this first; the details live in the keeper docs.

## What this repo is

- `legacy/zte-script-legacy.js` — the browser userscript (ZTE page + keeper panel). Browser-side: the keeper does not serve it.
- `legacy/mikrotik-keeper/` — the keeper: a stdlib-only TR-069 ACS for the ZTE MC889 (production) and the MikroTik ATL (backup),
  running in a container on the MikroTik router.
- `.dsh/skills/keeper/SKILL.md` — the project skill the agent loads for keeper work.
- `legacy/mikrotik-keeper/docs/BUSINESS_RULES.md` — the authoritative business rules; update it with behaviour changes.

## Keeper quick facts

- Live API: `http://keeper.lan:7557` (Swagger at `/docs`). Container `zte-keeper` on `192.168.6.1` (`ssh admin@192.168.6.1`).
- Deploy: `cd legacy/mikrotik-keeper && .venv/bin/python3 tools/deploy.py deploy` — checks, backs up the router code to
  `backups/<time>/`, uploads **code only** (never `keeper/data/`), restarts, verifies version / OpenAPI contract / config /
  fresh sample / DB, and auto-rolls back on failure. `check`, `status`, `logs`, `backup`, `verify`, `rollback <dir>` are the rest.
  Use `--skip-tests` only right after a fresh suite. The deploy refuses while a lock test runs.
  It also refuses when it cannot reach the API (it could not verify, so it would roll back blind): `KEEPER_URL=http://192.168.6.250:7557`
  when `keeper.lan` does not resolve / a proxy is in the way on the Mac (2026-11-17); verify names the last error; `--force` for a keeper that is down.
- Tests: `.venv/bin/python -m pytest -q` (each `test/scenarios/*.py` runs in its own process); in parallel: `tools/test_parallel.py --docker`
  (Mac, OrbStack: one container per scenario, ~70 s - long scenarios are split into parts sharing a test/fixtures/*_env.py setup with their own ports + temp DATA; the deploy gate uses it when docker answers) or `tools/test_parallel.py --jobs 4`. A scenario that patches a function
  must be paired with a real-body test — the `INTEST_MB` typo shipped because every test patched `brain.internet_test`.
- Version: bump `VERSION` in `keeper/__init__.py` (`YYYY-MM-DD<letter>`) on every keeper change; the deploy verify compares it
  with the live `/openapi.json`.
- DSH tool `keeper` exists in this profile (status/logs/lock_tests/anchors/check/backup/deploy/verify/rollback + guarded
  test/lock/antennas actions with `confirm: true`). The Web panel is browser-side and needs a page refresh after a client change.

## Decisions (do not re-litigate silently)

- **Quality first:** call-ready %, loss %, p95, jitter, then 5G SINR, RSRP; strict margins (5 pp / 1 pp / 10 ms / 10 ms);
  signal only breaks a tie. `AUTO=on` acts, `dryrun` reports, `off` ignores.
- **Per-cell antenna mode needs an active NR lock** (`nr_lock` active/pending) with `AUTO=on`; precedence per-cell > kept > env.
  The 534 record (omni/dir, on-target 100 %) is kept for when 534 is locked — do **not** auto-lock 534 (the strict rule said the
  unlocked baseline had better call-ready).
- **Stability:** a 5G row is `unstable` on an LTE anchor switch, < 80 % on the pinned anchor, or a > 3 min gap; it is kept but never
  ranked or auto-applied.
- **A missing/unstable cell is skipped, not measured** (no 12-minute wait): `cell not reachable`, `antenna control failed`,
  `5G cell switching`; antenna sets retry 3× with a re-login. A skipped step keeps its values empty (no 0 % loss) and a web failure
  skips the baseline too — a not-measured baseline is no baseline (nothing is auto-applied against it). The night screen follows the
  same rule: an unmeasured baseline gives `WARN`, no verdict, no "winner".
- **Lock-test cells are validated, not dropped:** `/lock-test?cells=` is one `PCI@EARFCN` per entry; a typo or a `;` list is a 400.
- **No BOOT inform = no measurement:** the ZTE applies a lock only at boot, so a step whose boot inform never arrives was never
  locked — record it (`not measured: no boot inform within N s`) and skip the settle / link wait / 12-min measurement.
- **A 5G cell is reachable only under some LTE anchors:** NSA combines the 5G leg only with anchors that carry it (747 under
  1550/434, not 340@100). Learn the (5G cell, anchor) pairs from the samples; `best_anchor` = the pair with the best measured
  results (call-ready, loss, p95, jitter, then 5G SINR/RSRP; the anchor's signal only as a tie-break), needing 10 min of evidence;
  among pairs no other pair clearly beats, the most minutes wins (a short lucky pair never overrides a long-standing one);
  `/cells` shows them and `POST /cells/refill` rebuilds them. The NR test pins each cell's `best_anchor` (baseline: the best across
  the tested cells) and auto-applies a clear winner as the **pair** (5G cell + anchor); `anchor=` overrides the mapping for all
  cells; the anchors are resolved once at the start (a mid-run re-resolve would make steps cross-anchor). A pair may move the LTE
  lock only to a daytime-measured anchor not clearly worse than the current lock (call-ready -5 pp, anchor SINR -3 dB) - else the
  5G cell alone is applied and the refusal is reported. The refill only enriches known cells (never resurrects a forgotten one). Never call a cell "unreachable" from a test pinned to an anchor it was never served under.
- **5G site notes:** the 5G cells' sites live in `sites.json` (`rat:"NR"` + `pci`), not in the code; `/cells` shows the site and the
  pair's same-site/distance/bearing relation; a co-sited pair wins a tie among equally good pairs; a candidate with no pair history
  at another site is flagged `cross-site NSA, no pair history`. Informational - the learned pairs decide.
- **A released lock waiting for its cell is traceable:** the guard logs/events `lock restore pending` at most hourly while the
  cell is not back (no Telegram - the event name is outside the alert rules). LOCK_PAUSE releases at 00:50 and restores when B1
  returns; the wait is expected, not a bug.
- **Clock:** verified by the modem's Inform time or the internet's HTTP Date (`CLOCK_URL`, only while unverified); whose clock was
  off = did ours jump (NTP) - else the modem's, and nothing is blamed on the router. Once verified in a run it stays (a modem reboot
  is not a router reboot).
  After a power cut run `legacy/mikrotik-keeper/tools/powercut.py --wait --follow 15` (router log -> real time -> PASS/FAIL;
  reports/ is git-ignored); the fixture `test/fixtures/routeros-powercut-2026-11-17.log` is the real 3rd cut of that day.
- **Overnight:** the router and modem are powered off when service is unneeded. Scheduled night tests, reminders and Saturday
  confirmation are retired. The lock guard and clock verification cover cold boots. `LOCK_PAUSE` is optional and disabled at home.
- **A/B smoke is diagnostic:** the panel uses `/ab`, `/ab-smoke`, and `/ab-smoke-stop`. It restores the managed pair on finish,
  stop, or recovery after interruption. Automatic quality A/B still runs on persistent poor quality and can use a current-method
  loaded candidate observed on another site.
- **Measurement reset:** put `data/.reset-measurements` on the container mount while idle and restart. Startup clears measurements
  and learned ranking caches, compacts SQLite, and consumes the marker. Keep managed locks, antenna settings, site notes, app
  lists and SQM inputs. Minute samples store typed cell identifiers and numeric metrics with a compact payload for other fields.
- **One lock change = one reboot** on the ZTE (both legs in one SetParameterValues); the ATL uses one script, no CPE reboot.
  The quality A/B's end puts your locks back as `pending` (both legs, one write) - not `released`, which restored each leg on its
  own 'back' check with its own reboot (2026-11-17). A ZTE reports other-frequency neighbours only ~2-3 min after a boot.
- **Per device, never mixed:** DB rows carry `device`; `store.rows` defaults to the active `DEVICE`; state/cell files are per device.
- **SQM phases 3 / 4 are closed by data** (2026-11-17: busy latency ~ idle + 1-2 ms, setups < 2 % apart); the weekly Telegram
  summary (`CAPTEST_WEEKLY`) carries the evidence - reopen only on its numbers (rules in BUSINESS_RULES).
- The keeper applies the antenna mode once per boot; a superseded boot worker stands down (generation checked after the wait).
- **Daytime ZTE LTE lock tests:** settle for CA, wake NSA with a short download, then measure quality during the two successful
  iperf transfers. Use the same run's complete score to choose the LTE lock. A winner needs at least 5 points over the unlocked
  baseline and no clear internet-quality regression. Treat SINR and RSRP as diagnostics.
- **Morning discovery:** test the serving cell and queue two backups per band. A measured NSA pair gets priority by quality only
  with at least 10 minutes, loss ≤ 2% and jitter ≤ 20 ms. Order other neighbours by RSRP. Try the next backup when the first cannot
  measure with 5G. A locked leg gets 90 s for online readiness and 90 s to wake 5G. Two reachability failures in seven days suppress
  that `PCI@EARFCN` in the per-device morning cache.
- **Morning fallback and comparison:** failure of the unlocked screen can open B20/B8 discovery. A healthy high-band leg
  suppresses the automatic low test. Use `POST /morning?apply=0&low=1` for fresh loaded comparisons with managed locks restored
  and the separate result at `/morning` → `trial`.
- **Local traffic during modem speed tests:** scheduled tests wait for quiet. RouterOS WAN traffic above the idle limits during
  transfers invalidates capacity samples and loaded lock-test measurements. An invalid sample cannot move the learned ceiling.
- **Morning discovery and isolation:** neighbours need two fresh usable RSRP readings in the selected band before a lock attempt.
  Morning and capacity tests reserve the modem under the same start lock so their background jobs cannot overlap.

## Workflow

0. **Waiting:** no fixed `sleep N` for a result - poll the condition under a ceiling and return as soon as it holds:
   `timeout 300 python3 test/scenarios/x.py` (the ceiling only), `timeout 120 bash -c 'until curl -sf http://keeper.lan:7557/openapi.json; do sleep 1; done'`,
   `wait $pid`, `inotifywait`; independent scenarios in parallel (`ls test/scenarios/*.py | xargs -P4 -I{} timeout 300 python3 {}` -
   only those with their own port + DATA dir). `watch` is interactive (never exits) - not for tool calls. In the browser, poll the API
   instead of `setTimeout(N)`.

1. Scenario/test first (fail it), then the code; keep the device isolation; update the docs and the skill with the change.
2. Bump `VERSION`; run `tools/deploy.py check`; `tools/deploy.py deploy`; confirm the verify line; keep the backup name.
   Then validate the run, not just the deploy: `tools/deploy.py validate --rat lte|nr [--wait]` (read-only: verdict
   keep / apply / no-lock / no-result) — the loop is deploy → start/schedule a run → validate → fix/improve → repeat.
3. Userscript changes: bump the console marker (`Loading ZTE Script v...`) and update the installed browser copy.
4. If a new rule/decision is made, add it here and to `docs/BUSINESS_RULES.md`.
