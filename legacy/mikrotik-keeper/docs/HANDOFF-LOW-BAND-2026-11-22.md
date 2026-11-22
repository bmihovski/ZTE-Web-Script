# Keeper handoff for low-band cell locks

2026-11-22, Europe/Sofia. Start at the repo root, read `AGENTS.md` first, then the keeper skill and relevant business rules. Paths below are relative to `legacy/mikrotik-keeper`.

## Goal

Give an explicit B8 or B20 cell lock a matching LTE band restriction for its lifetime. The operator observed a more stable low-band setup after applying both. Preserve the exact prior LTE band setting when the low-band lock ends, alongside the existing LTE/NR restoration rules. In a comparison, apply the restriction only to the low-band leg. For a low profile deliberately kept in service, retain the restriction until release or replacement. Internet quality under comparable load and recovery determine whether the profile is useful.

## Evidence and current state

- Screenshot 1 shows B20 `112@6400` at 10 MHz with B1 `112@100` and B3 `112@1550`, both 20 MHz. The shared PCI is a reason to key tests by **PCI and EARFCN**.
- Screenshot 2 and live samples at 19:17–19:18 show B8 `394@3774` at 5 MHz, B1 `434@100` and B3 `434@1550` at 20 MHz, and n28 `534@156510`. Both samples had 0% loss and were call-ready. At 19:20:51 the modem recorded a BOOT inform and served B7 afterward. The evidence establishes the wider CA combination and a change across a boot. The cause of the repeated switching reported by the operator still needs to be isolated from modem reselection and keeper recovery actions.
- At handoff the live keeper is `2026-11-22ac`. At 19:24:51 it was serving B7 `340@3350` + n78 `534@636576`, **unlocked**, with 0% loss and call-ready. The keeper was idle, both raw cell locks empty, and the raw LTE band list was `1,2,3,4,5,7,8,12,17,20,25,26,28,30,32,34,38,39,40,41,42,46,48`. Recheck all of this before changing the modem. The earlier managed B7 pair is historical and should not be treated as the current desired configuration.
- Branch `5G_data_legacy`, clean at `40ca7a0` and three commits ahead of origin when inspected. Live code backup `20261122-160719`. The ac release passed 42 scenarios and the panel tests. It has a read-only A/B plan preview.
- Prior comparisons tried B20 `112@6400` and `394@6400` without observed n28. B8 `112@3774` and `394@3774` had n28 observations, with LAN traffic invalidating loaded results. Keep those records as diagnostics. The new band-restricted variants need fresh evidence. See `reports/morning-rc-20261122y.json` and `reports/morning-rc-20261122z.json`.

## Where to start

| Path | Relevance |
| --- | --- |
| `keeper/devices/zte/locks.py`, `keeper/acs.py` | `locks_apply(lte=None, nr=None)` groups LTE/NR cell writes and reboots once. `LockLTEBand` is already read into `zte_locks_raw`. Inspect the queued write, timeouts, readback and web fallback. The legacy userscript uses web `BAND_SELECT` with a hex mask. Verify the actual firmware behavior and the Python fallback before relying on it. |
| `keeper/bandscan.py`, `keeper/morning.py` | Scanning and discovery temporarily write `LockLTEBand` as a TR-069 band list, then restore the configured full list. Actual morning legs and the final apply/restore currently call `locks_apply` with cell locks only. Keep band-only discovery separate from the low-profile restriction. |
| `keeper/absmoke.py`, `keeper/guard.py`, `keeper/locktest.py` | Smoke freezes the plan and persists the original pair for Stop/startup recovery. Guard changes, releases and restores managed locks, including on the web when informs stop. Generic lock tests can run against an existing managed low profile. Trace any path that might drop or orphan the band restriction. |
| `test/scenarios/{tr069lock,bandscan,morning,absmoke,guard,quality_ab,measurement_reset}.py` | Reuse existing fake modem/ACS tests. Exercise the real write and restore bodies in addition to mocked policy tests. |

## Red/green acceptance

1. **Apply and identify.** Start with a failing test for a low LTE `PCI@EARFCN` plus its derived single LTE band and optional NR lock. Verify the requested band and cell data reach the modem before the one allowed reboot. Cover same PCI across bands, unknown/conflicting band inputs, and old two-lock calls. Leave the NR band setting under its existing policy. Keep high-band CA and the MikroTik driver behavior compatible.
2. **Own and restore the restriction.** Capture the exact prior band list before replacing it, including a custom list. Test low-to-low, low-to-high and unlock, scoped comparisons, retaining a selected low profile, and the guard's managed and offline release/restore. Make the transaction recoverable across Stop, exceptions, keeper/container restart and cold boot. Keep existing `state.json` and persisted two-cell smoke recovery records readable.
3. **Treat failures as measurements only when verified.** Test rejection, partial apply, missing/stale readback or BOOT inform, and absent service. Application acknowledgement, matching fresh band/cell readback and service recovery are separate facts. Keep recovery pending when appropriate. Bound fallback, ensure retries are idempotent, and cover a lost TR-069 path where web-based release must free the temporary band as well as the LTE cell. A no-op should avoid a reboot.
4. **Validate the hypothesis safely.** Snapshot current locks and band lists read-only. After the regression and deploy gate, run one quiet restore-only comparison on a selected low cell, preferably B8 `394@3774` with its observed n28. Verify the band and exact serving LTE/NR pair with fresh observations during load. Record CA, anchor changes, transfer duration, throughput, call-ready, loss, p95 and jitter, plus final configuration and recovered connectivity. A before/after on the same low cell provides stronger evidence if both legs can be measured safely. Keep LAN traffic checks and the shared modem reservation. Report gaps and prefer a single focused live run over reseeding all profiles.

Follow `AGENTS.md` for VERSION, business rules, the keeper skill, check/deploy/verify, and any userscript bump. The A/B preview can show the LTE band restriction when it is part of the profile. Keep this work scoped to the low-band lock lifecycle.
