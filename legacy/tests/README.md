# Tests

Checks for `zte-script-legacy.js` (and the Mac app) that run without a router.

## Run

Requirements: Node.js 18+ (no `npm install` needed), Python 3 for the app check.

```bash
cd legacy
node --check zte-script-legacy.js                  # syntax
node tests/test_cmp.js zte-script-legacy.js        # logic: prints "ok"/"FAIL" per case, then ALL PASSED
python3 app/zte_app.py --selftest                  # Mac app prompt bridge (no GUI, no router)
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
