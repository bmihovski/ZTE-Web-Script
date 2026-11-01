# Tests

Checks for `zte-script-legacy.js` (and the Mac app) that run without a router.

## Run

Requirements: Node.js 18+ (no `npm install` needed), Python 3 for the app check.

```bash
cd legacy
node --check zte-script-legacy.js                  # syntax
node tests/test_cmp.js zte-script-legacy.js        # logic: prints "ok"/"FAIL" per case, then ALL PASSED
node --check mikrotik-script.js
node tests/test_mikrotik.js mikrotik-script.js     # MikroTik parsers + AT command builders
python3 app/zte_app.py --selftest                  # Mac app prompt bridge, both router origins (no GUI, no router)
```

`test_cmp.js` exits with code 1 when any case fails, so it can gate a commit:

```bash
# .git/hooks/pre-commit (chmod +x)
node --check legacy/zte-script-legacy.js && node legacy/tests/test_cmp.js legacy/zte-script-legacy.js >/dev/null
```

Run all three after every edit of the script, before pasting it into Firefox or restarting the app.

## What is covered

- **NGBR table sorting:** ascending/descending with negative/negative, negative/positive and positive/positive values,
  0 kept as a real value, missing values always last, tie-breaks.
- **NGBR content:** BETTER hint, MIN/MAX, connected cells (no `NaN` PCI, stale 5G CA cells filtered), learned cell id
  (no wrong id during cell ping-pong).
- **Alignment table:** change since start (sign, colour, no red `-0.0`), peak, no crash after Reset with an invalid sample.
- **Saved positions / results:** best value per column, HTML escaping of labels.
- **Tones:** targets (RSRP -70 dBm, RSRQ -10 dB, 5G SINR 15/20 dB, LTE SINR 6 dB), closeness direction, relative pitch,
  rx0/rx1 direction, branch warning.
- **Lock experiment:** waits through the reboot delay, waits again when the target cell is lost, records a 60 s result.
- **RSSI:** 5G / LTE RSSI rows shown grey (no green/red), no "best" highlight in saved tables, per-cell average in the 5G log.
- **Peak guide:** new peaks on a rising sweep (negative values too), "past" only after a 1.5 dB drop, "go back" seconds
  include the 2 s lag, "back" within 0.5 dB, "confirmed" after 3 s there, no repeats, noise below the drop stays silent.
- **5G cells seen log:** time per cell (gaps > 5 s not counted), switches with the SINR just before, `no 5G` state,
  rx dropouts (-140) counted once per run, negative SINR kept, sorting by time, event list cap.
- **Antenna mode sweep:** 5G + LTE SINR score with signs, missing readings rank last, `-20.0` no-value marker skipped,
  hex PCI and cell change shown (`636576/534 > 636576/786`), best mode applied, a failed command restores the previous
  auto-select with omni/omni. `zte_set`/`ant_get`/`ant_sleep` are stubbed, timings shortened.
- **Login / SuperAdmin:** saved admin hash = automatic, macOS app asks via `api.ask` (no 10 s limit), browser via
  `prompt()`; SuperAdmin `HIDDEN_PAGE_LOGIN` posts `SHA256(hash + LD)` + `AD`, saves the hash on success, drops it on a
  wrong hash and stays quiet on automatic injection.
- **MikroTik (`test_mikrotik.js`):** `ca-band`/`primary-band` parsing, `AT+QNWLOCK` answers (off, multi-cell, PCI 0), lock
  input validation and AT commands, NR SCS, `cell-monitor` dedupe/sort (newest section wins, missing RSRP last),
  colours for negative/zero/missing values, HTML escaping; antenna alignment: `AT+QENG`/`AT+QRSRP` parsing (NSA, LTE only,
  SA, "no value" markers -140/-32768, SINR 0 kept), 3 s average / start / peak / 5 min window, change colours (no red -0.0),
  rx-path branch warning, tone targets and closeness, saved positions (best per column, escaping) and CSV quoting.
  Cell evaluations: serving cell from `primary-band`/`ca-band` (LTE, NSA 5G, SA), settle delay, waiting for the target,
  60 s averages (SINR 0, negatives, bit/s -> Mbit/s), target lost restarts the minute, 3 min deadline.
  Fixtures are real RouterOS 7.24.4 / RG520F answers (SA built from the Quectel field order).

## How it works

The test reads the script as text and does not load the router UI:

- `grab('name')` cuts a named function out of the script and evaluates it in Node.
- `vars(/regex/)` evaluates global tables (`NGBR_COLUMNS`, `LTE_BANDS`/`NR_BANDS`, `ALIGN_METRICS`, `TONE_TARGET`,
  `TONE_RES`, `EXP_COLS`, `EXP_FIELDS`) as globals.
- jQuery `$`, `tools_store`/`tools_save` and `localStorage` are small stubs; `$(sel).html(v)` is captured in `html[sel]`.
- Router values are set as globals in the `Object.assign(global, {...})` fixture, as strings exactly like the router sends
  them: `lte_pci`, `nr5g_pci`, `cell_id` are **hex**; PCIs inside `ngbr_cell_info` and `lte_multi_ca_scell_info` are **decimal**.

## Maintaining

- **Renamed or new function used by a test:** update the list in `for (const f of [...])` near the top.
- **Changed a global table:** keep it a single `var NAME = ...;` statement, or update the matching `vars(...)` regex.
- **New router field read by a tested function:** add it to the fixture (missing globals fail with `ReferenceError`).
- **Changed a target or threshold** (`TONE_TARGET`, `TONE_RES`, BETTER 3 dB, branch 6 dB): update the expected values in the
  matching cases - a failing case is a reminder that behaviour changed, not necessarily a bug.
- **Adding a case:** `t('what it proves', () => { ... assert ... });`. For any new comparison cover negative/negative,
  negative/positive, positive/positive, zero and missing values. Keep inputs as router-style strings.
- When a case fails, the message shows the actual values (e.g. the sorted column) - fix the script, not the expectation,
  unless the behaviour change was intended.
