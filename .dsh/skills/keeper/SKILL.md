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
  `devices/__init__.py`, `devices/mikrotik.py`, `devices/zte/{__init__,web,antennas,locks,checkin}.py`).
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
- **A 5G lock is a pair:** each NR step pins its 5G cell's `best_anchor` (an NSA cell is served only under some anchors), the
  baseline pins the best anchor across the tested cells, and a clear winning row auto-applies the 5G cell **and** its anchor
  (`anchor=` overrides the mapping for every cell).

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

- Patching a function in tests hides its real body; add a real-body test.
- The ZTE web has ONE session: an open page logs the keeper out; the antenna set retries 3× with a re-login, and the boot apply
  waits for the web (`CPE_READY_*`).
- A reboot sends several informs; one apply per boot (generation checked after the wait, stale workers stand down).
- No BOOT inform after a lock = the step was never locked (the ZTE applies a lock only at boot): it is recorded `not measured: no
  boot inform within N s` and skipped, not measured for 12 min on a dead link.
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
curl -s 'http://keeper.lan:7557/lock-tests?rat=nr' | python3 -m json.tool
python3 tools/deploy.py status|verify|logs -n 20
python3 tools/deploy.py check|backup|deploy|validate|rollback backups/<time>
```
