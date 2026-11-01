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
- Tests: `.venv/bin/python -m pytest -q` (each `test/scenarios/*.py` runs in its own process). A scenario that patches a function
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
- **One lock change = one reboot** on the ZTE (both legs in one SetParameterValues); the ATL uses one script, no CPE reboot.
- **Per device, never mixed:** DB rows carry `device`; `store.rows` defaults to the active `DEVICE`; state/cell files are per device.
- The keeper applies the antenna mode once per boot; a superseded boot worker stands down (generation checked after the wait).

## Workflow

1. Scenario/test first (fail it), then the code; keep the device isolation; update the docs and the skill with the change.
2. Bump `VERSION`; run `tools/deploy.py check`; `tools/deploy.py deploy`; confirm the verify line; keep the backup name.
3. Userscript changes: bump the console marker (`Loading ZTE Script v...`) and update the installed browser copy.
4. If a new rule/decision is made, add it here and to `docs/BUSINESS_RULES.md`.
