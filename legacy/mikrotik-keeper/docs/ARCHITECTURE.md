# Architecture

The keeper is our own TR-069 ACS for the home uplink modem. It runs in a container on the main MikroTik (RB450Gx4) next to
AdGuard, 192.168.6.250:7557. It monitors the link, analyses it, keeps chosen cells locked (guarded), runs lock tests and the
night screen, and puts the ZTE's antenna mode back after a boot. The userscript panel and the pywebview app only display what
it measured. Python 3 standard library only; development tools (pytest, pdoc) never go into the container.

## Ground rules

- **Measured values only.** Findings come from our own samples and lock tests. Same-site or bearing guesses from a PCI are not
  facts; CellMapper data is not used (licence). The CRC public register is used for site positions.
- **Network mode is never touched** (Yettel is NSA only: never `Only_5G`, `net_select` unchanged). Locks are a human
  decision; the keeper only recommends them, then guards the lock you chose.
- **One uplink device per run**: `DEVICE=zte` (production) or `DEVICE=mikrotik` (ATL backup). A CPE of the other maker
  pointed at the keeper is answered and left alone.
- **Secrets stay in the container env**: ZTE_PASS_HASH, SQM_PASS, TG_TOKEN. They are never logged. TR-069 dumps hold
  IMEI / IMSI / ICCID: only masked fixtures go into git.
- **Small router**: the database is capped (`DB_MAX_MB`), logs rotate (`MAX_MB`), every background job is bounded.

## Package layout

```
keeper/                    the package; on the router disk1/zte-keeper/keeper/ = /keeper in the container
  __main__.py              python3 -m keeper: start threads, serve API + ACS on PORT
  common.py                env, data dir, log, state file (per device), records / events, busy registry
  store.py                 SQLite keeper.db: one table rec(device, kind, time, JSON); housekeeping, prune, export
  acs.py                   CWMP server (device neutral): session steps, queued writes, reboot, fast mode, watchdog
  monitor.py               per-minute pipeline: read -> sample -> store -> brain -> driver jobs -> alerts -> decisions
  brain.py                 latency probe, 48 h history, cell registry, report / findings, auto reboot, GET API
  guard.py                 lock guard: set when idle, release when lost, restore when back, reconcile with the modem
  locktest.py              lock tests (LTE / 5G, pinned anchor, un/stable-anchor gate, internet-tested antenna sweep, on-go / fixed position, skip / stop) and the night screen
  api.py                   HTTP: ROUTES (-> /openapi.json, /docs), GET / POST handlers; POST / = CWMP
  sqm.py                   adaptive CAKE limit on the main router (RouterOS REST)
  alert.py                 Telegram messages, rate-limited per kind
  devices/__init__.py      the driver interface (table) + current()
  devices/zte/             ZTE MC889: __init__ (driver), web (goform page), antennas (modes, sweep, per-cell mode at boot), locks, checkin (operator relay)
  devices/mikrotik.py      MikroTik ATL 5G: driver, cell-monitor scans, AT-command locks via RouterOS script Download
  start.sh, keeper.sh      container entry; v1 shell keeper (legacy fallback)
docs/ARCHITECTURE.md       this file (also the front page of the API docs)
test/test_scenarios.py     pytest entry: each scenario / self-check in its own process
test/scenarios/*.py        assert scripts against fake modems (mock ZTE web + CWMP sessions, real ATL dumps)
test/fixtures/             mock_zte.py, mikrotik-dump*.csv (masked)
```

Dependency direction: `devices/*` -> core modules (`acs`, `guard`, `locktest`, `common`, `brain`) -> `store`.
Core modules reach the driver only through `devices.current()` and the interface table. Platform code (goform, AT commands,
X_MIKROTIK_ names, operator relay) lives only under `devices/`.

## One minute in the life of the keeper

1. The modem informs (every 60 s, or 10 s in fast mode). `acs.handle` answers and asks for the driver's `READ` paths.
2. Values arrive. The driver's `on_values` runs first (ZTE: its own lock values for the guard, the firmware guard write).
   Then, at most once a minute, `monitor.sample`: the driver's `row()` maps the values to the shared sample fields.
3. `monitor.finish` stores the sample and explains 5G switches (`brain.explain_switch`). It feeds the history and the cell
   registry, then runs the driver's `minute_jobs()`:
   - ZTE: scheduled sweep -> lock guard -> night screen.
   - ATL: lock guard -> scan.
   Then the calls-at-risk alert and `brain.decide` (auto reboot: dryrun / on).
4. Back in the session: inform interval (fast while a job runs), queued writes (locks), driver extras -> SetParameterValues;
   then the driver's pending RPC (ATL script Download), then a queued Reboot.

Background threads: the latency probe (`brain.probe_loop`), the SQM loop, the watchdog, and one-off jobs (lock change,
lock test, sweep, antenna apply after boot). Jobs that change the modem check `common.busy()` first, so they never overlap.

## State and data

| where | what |
|---|---|
| `data/keeper.db` | samples (90 days, capped at DB_MAX_MB), events, lock tests, sweeps, the app's lists - per `device` |
| `data/state.json`, `state-mikrotik.json` | per device: lock wishes + state, kept antenna mode, last sweep / night / check-in, learned lock format |
| `data/cells.json`, `cells-mikrotik.json` | per device cell registry (what that antenna position measures) |
| `data/cell-ids.json` | shared: learned ECI / NCI per cell (network facts) |
| `data/sites.json` | optional, yours: site names / positions (CRC register) for distance and bearing |
| `data/keeper.log` | the log (rotates at MAX_MB) |

Shared sample fields have the same meaning on both devices, so reports compare them. A field only one device has is in that
driver's `EXTRA` (`mikrotik_*`). Locks never mix: each device has its own state file.

## Devices

- **ZTE MC889** (production): TR-069 for everything it exposes. It needs the web page for antenna modes, and the web page is
  the fallback for locks. A lock costs a reboot (~2 min offline), so locks change only when the link is idle.
- **MikroTik ATL 5G** (backup, data-limited SIM; only the LAN TR-069 traffic is free): monitoring, guarded locks as AT
  commands, scans. It forgets locks on reboot, so the guard sets them again. Lock tests run on both (an ATL step is one script, no
  CPE reboot); night screen and antennas are ZTE only.
- **ATL without the keeper** (summer house, no RB450): the ATL runs standalone and unlocked; its TR-069 client keeps trying
  the ACS, which is harmless. Nothing to install. A static queue on the ATL itself is optional. The keeper only sees a gap in
  the ATL's data.

## Adding a feature (workflow)

1. **Device neutral or not?**
   - Platform-specific code goes into the driver: a new hook, or a `CAN` flag plus a function the API calls.
   - Neutral logic goes into the core module that owns the subject (`guard`, `locktest`, `brain`, ...). Don't create a module
     for one function.
2. **Config** is an env var read at import (`E("NAME", "default")`), documented in the module docstring and the README.
3. **API**: add the route to `api.ROUTES` (capability in the 5th field) and handle it in `api.H`. `test/scenarios/api.py` fails when a
   handled path is not listed. /docs updates by itself.
4. **Test**: extend the scenario that covers the subject, or add `test/scenarios/<name>.py` (plain asserts, prints
   `OK <name>`). Pure functions can carry an `if __name__ == "__main__"` self-check; add the module to
   `test_scenarios.SELF_CHECKS`.
5. **Docstrings**: every public function gets one (what, returns, units). Module docstrings say what the module owns and
   list its env vars. Deliberate simplifications carry a `ponytail:` comment that names the limit.
6. **Run**: `pytest` (all scenarios, ~2 min) and `pdoc` (docs below). Bump `keeper.VERSION`. Deploy: upload the changed
   files, then restart the container.

## Docs and tests

```
pip install pytest pdoc          # dev only, on the Mac (or: pip install -e ".[dev]")
pytest                           # every scenario in its own process
python3 test/scenarios/guard.py  # one scenario, with its output
pdoc keeper -o docs/api          # HTML API docs from the docstrings (this file is the front page)
pdoc keeper                      # live preview at http://localhost:8080
```
