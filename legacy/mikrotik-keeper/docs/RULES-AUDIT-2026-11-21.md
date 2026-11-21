# Business-rules audit — 2026-11-21

Scope: `docs/BUSINESS_RULES.md` against the implementing code (`keeper/`), the scenarios (`test/scenarios/`) and the **current
container env** (`/container env print detail` on 192.168.6.1). Read-only audit; the behavior changes already deployed are the
2026-11-21f/h fixes (the idle-`no5G` lock reconcile and the band-scan NR-lock guard). This pass aligns the rules with the actual
config, closes the test gaps that mattered, and condenses the test-era rules that are dormant in the current research mode.

## Current policy snapshot (env, 2026-11-21)

| Key | Value |
|---|---|
| `AUTO` | `on` |
| `ANT_AUTO` / `ANT5` / `ANT6` | `0` / `4` / `4` (both directional) |
| `LOCK_PAUSE` | `23:30-08:00` |
| `NIGHT_SCREEN` | `01:30-05:00` |
| `NIGHT_ALWAYS` | `1` (research, every night) |
| `NIGHT_BANDS` | `1,3,7,8,20,28` (changed this pass from `1,3,7`) |
| `BANDSCAN_NIGHT` | default `1,3,7,8,20` (no B28: no service here) |
| `SWEEP_DAYS` | `0` (on demand only; the env default is 7) |
| `CAPTEST` / `CAPTEST_SLOTS` / `CAPTEST_UP` | `1` / `08:30,12:30,18:30,21:30` / `1` |
| `CAPTEST_SHARE_DOWN` | `1.15` |
| `SQM_MAX_DOWN` / `SQM_MAX_UP` / `SQM_MIN_DOWN` | `185` / `28` / `35` (plan 200/30) |
| `CHECKIN_DAYS` | `0` (operator check-in off) |

## Findings — fixed

| # | Rule (before) | Actual / risk | Fix |
|---|---|---|---|
| 1 | `NIGHT_BANDS="1,3,7"` (rules example) | The band scan found B8/B20/B28 cells (e.g. `112@6400` -77 dBm, `394@3774` -78 dBm) but the night planner **silently filtered them out**: the bad-weather low-band fallback never reached a night test. | Env `1,3,7,8,20,28`; rules updated; `/night-plan` now returns `112@6400`, `394@3774`, …; test `night.py` pins the low-band filter. |
| 2 | `NIGHT_RETEST_DAYS (30)` | Two different knobs: `locktest.NIGHT_RETEST_DAYS` = **7** (the `NIGHT_CELLS` home set, currently empty) and `nightplan.RETEST_DAYS` = **30** (the full-test skip). The rule named the wrong one for the planner. | Rules split them; no behavior change. Trap added. |
| 3 | `NIGHT_SCREEN (02:00-05:00)` | Env is `01:30-05:00`; the code default is 02:00. | Rules and docstrings state the env is authoritative and the current is 01:30-05:00. |
| 4 | `LOCK_PAUSE` example `00:50-06:10` / history `00:50` | Env is `23:30-08:00` (B1/ARFCN 100 goes dark at 23:30); `guard.py` and `NIGHTLY-PLAN.md` still said 00:50. | Rules, `guard.py`, `NIGHTLY-PLAN.md` updated to the current window. |
| 5 | `SWEEP_DAYS` 7 / scheduled sweep | Env `0`: no scheduled sweep; the boot/per-cell mode only. | Rules note the current on-demand mode. |
| 6 | `CAPTEST_SLOTS` "incl. night slots" | Env has no night slot: `08:30,12:30,18:30,21:30`; no capacity test during the night screen. | Rules updated. |
| 7 | "Locks are a human decision" | `AUTO=on` **auto-applies** a measured clear winner (the lock-test / A-B policy). The rule contradicted the code and could lead to thinking the keeper never locks. | Rule now: your choice **or** a measured clear winner; never a cell picked by signal alone. |
| 8 | `NIGHT_MAX` / `NIGHT_CELLS` | Current `NIGHT_MAX=0` (no cap; the window budget bounds it), `NIGHT_CELLS` empty. The rule said "up to NIGHT_MAX" without the current values. | Rules state the current values and that the planner's cells are the night set. |
| 9 | Weekly arm/reminder path | Current `NIGHT_ALWAYS=1`: research runs every night; `remind()` never sends, the arm path is dormant. The rules described the weekly path as if active. | Condensed to "dormant while research runs; for when research ends". The code is unchanged. |
| 10 | `NIGHT_SCREEN` start vs night test window | 01:30-05:00 is the **start** window; `night_window()` uses `LOCK_PAUSE` (23:30-08:00) for the test budget, so a started screen can run to 08:00. The rules were ambiguous. | Rules clarified; test `night.py` asserts `night_window()` == the `LOCK_PAUSE` window. |
| 11 | SQM plan vs max | Plan `200/30`, start ceiling `SQM_MAX_*` 185/28, floor `SQM_MIN_DOWN` 35. Not a conflict, but the rule only named the plan. | Rules name the current max/floor; no behavior change. |
| 12 | PS no-service restart | **ON** in the modem (`ps_no_service_restart_flag=1`); a restart source **independent of the watchdog**. The 04:00–06:30 `not measured: no boot inform` steps are consistent with it. | Rules document it; `GET /zte/settings` flags it. **Developer attempt (13:32):** `DEVELOPER_OPTION_LOGIN` with `SHA256(ZTE_PASS_HASH + LD)` succeeds (`developer_option_loginfo=ok`; the developer page password is the admin password; the JS has no separate hash). `PS_NO_SERVICE_RESTART_SET` still returns `failure` for `0`/`false` in the admin and developer sessions; `hidden_page_password` is empty/not exposed, and `HIDDEN_PAGE_LOGIN` with the admin hash fails (separate SuperAdmin hidden password). The flag is **read-only/operator-locked** or needs that hidden password; the audit stays. |
| 13 | Idle-`no5G` lock reconcile | Fixed in **2026-11-21f**: the ZTE releases the 5G leg on idle and reports `LockNr5gCell=''`; the old reconcile re-applied it and rebooted every ~5 min. | Guard test (idle no5G not a lost lock; busy no5G still is); rules/trap. |
| 14 | Band scan under an NR lock | The old `bandscan.start()` checked only the LTE lock. At 08:57 the scan ran with 534@636576 pinned: it saw the lock, not the network's own pair, and a band change could lose it → guard re-apply + reboot. | Fixed in **2026-11-21h**: refuse an active/pending NR lock; test pins it. |
| 15 | Band scan had no internet quality/pair | The scan recorded only LTE serving/neighbours; the network's 5G pair and the band's call-ready were not recorded, so the NR database/night plan got no low-band evidence. | Fixed in **2026-11-21h**: a per-band load test raises the 5G leg; the row carries the 5G cell/ARFCN/SINR/RSRP and call-ready/loss/p95/jitter/down; the pair goes into the NR DB; the night plan's `why` carries the call-ready. Tests in `bandscan.py`/`nightplan.py`. |
| 16 | Band scan `nr_arfcn` empty | The live 13:02 scan rows carry `nr_cell=156510/534` but no `nr_arfcn` (the samples carry only `nr_cell`), so the ARFCN column was empty although the cell string has it. | Fallback from `nr_cell` in `summarize` (version **i**); test in `bandscan.py`. The existing row's `nr_cell` already carries 156510. |
| 17 | Low-band A/B had no tool | The n28 pair (156510/534 under B8/B20) was discovered but not reachable as a decision aid. | **Phase 1** (version **j**): `GET /low-band` previews B8/B20 (serving, n28 pair, call-ready/loss/p95/jitter/down) against the current high-band no-lock with `brain.clearly_better`; `POST /low-band-trial?band=8|20` runs a one-band band scan (LockLTEBand, no reboot, no cell lock, internet test, full list back). Tests `lowband.py`/`api.py`; 37 scenarios. Live: the high band is better now (p95 42.8/jitter 11.6 vs B8 60.3/21.4, B20 68.4/28.3) -> `better:false`, `recommend:null`, correct. Phase 2 (the automatic two-tier A/B) pending. |

## Bad-decision risks still open (documented, not code changes)

- **PS no-service restart ON:** with a lock to a cell the modem cannot attach to, it can restart the PS/reboot before a boot inform;
  it is a reboot-loop candidate independent of the watchdog. The audit flags it; **test it off** and rerun a 227@1550-style step.
- **`NIGHT_BANDS` is a silent filter:** the `/night-plan` `why` still lists a candidate that the band filter removed; the `cells`
  list does not. The low bands are included now, but a future band change can hide a candidate again. Recommendation: add a
  `filtered` list to `/night-plan` (the band scan's cells dropped by `NIGHT_BANDS`).
- **Research runs every night (`NIGHT_ALWAYS=1`):** the router + modem stay on; the weekly arm/reminder is dormant; there is no
  "powered off" default until research ends. This is intended, but it is the main difference from the 2026-11-17 nightly plan.
- **Night tests can run to 08:00** (`LOCK_PAUSE` end), not 05:00 (`NIGHT_SCREEN` end); the start window ends at 05:00. Intended by
  `night_window()`; the rules now say so.
- **No scheduled antenna sweep (`SWEEP_DAYS=0`)** and **no night capacity test (`CAPTEST_SLOTS`)**; the mode comes from the boot
  apply / per-cell record, and capacity tests run in the four daytime/evening slots. Intended current config.
- **Watchdog keep-off:** the web watchdog remains a reboot-loop risk with any unattachable lock; the keeper detects and logs it but
  does not disable it. Keep it off.

## Rules removed / condensed

- The weekly arm/reminder description is now a **dormant** note (research runs every night); the code and API remain for when
  research ends.
- The `NIGHT_CELLS` "home set" is marked **current empty**; the `NIGHT_RETEST_DAYS` 7 throttle applies only when it is set.
- `NIGHT_MAX` is marked **0 = no cap** (the window budget bounds the plan).
- The `LOCK_PAUSE` 00:50 example and the `NIGHT_SCREEN` 02:00 default were replaced by the **current** env values (23:30-08:00,
  01:30-05:00); the code defaults are still named where they matter.
- `SWEEP_DAYS` and `CAPTEST_SLOTS` now state the **current** values (0; 08:30,12:30,18:30,21:30) instead of only the defaults.
- The "Locks are a human decision" sentence was replaced by the actual auto-apply policy.
- The `NIGHT_BANDS` "1,3,7" example was replaced by the current `1,3,7,8,20,28`; the low-band reason is documented.

## Test coverage

Added this pass (green; the code already supported the behavior):
- `test/scenarios/night.py`: a B20 band-scan cell (`112@6400`) is kept by `NIGHT_BANDS=["20"]` and dropped by the old `1,3,7`;
  `night_window()` is the `LOCK_PAUSE` window (23:30-08:00) when set, else the `NIGHT_SCREEN` hours.
- The earlier 2026-11-21 tests: `guard.py` (idle no5G vs busy no5G), `bandscan.py` (NR-lock refusal, pair/quality, NR DB pair),
  `outage.py` (PS reconnect fallback), `web.py` (settings audit, PS reconnect), `nightplan.py` (band-scan call-ready why).

Still config-only (no unit test, verified live): the exact env times/values (`NIGHT_SCREEN`, `LOCK_PAUSE`, `CAPTEST_SLOTS`,
`SWEEP_DAYS`, `NIGHT_BANDS`, `SQM_MAX_*`); `/night-plan` was read live after the `NIGHT_BANDS` change and returned the B8/B20 cells.

## Verification

- `tools/deploy.py check`: 36 scenarios, 0 failed.
- Live after the `NIGHT_BANDS` change and keeper restart:
  `night_bands ['1','3','7','8','20','28']`, `cells ['112@6400','394@3774','374@3774','495@3774','434@6400','323@6400','309@3774','121@6400']`.
- Modem: stable, `nr_lock` none, `lte_lock` none, no busy; no reboot loop since the f/h fixes.

### Live band scan (the user ran it, 13:02–13:15)

The full band list was restored at 13:15:42; the internet was back; the n78 leg re-added at 13:16:51. No lock was left.

| Band | Serving | 5G pair | call-ready | p95 | jitter | down |
|---|---|---|---|---|---|---|
| B1 | 100/340 | 636576/534 (n78) | 100 % | 40.9 ms | 13.1 ms | 30.74 |
| B3 | 1550/434 | 636576/534 (n78) | 100 % | 34.1 ms | 8.4 ms | 34.58 |
| B7 | 3350/340 | 636576/534 (n78) | 100 % | 23.8 ms | 3.1 ms | 36.07 |
| B8 | 3774/394 | **156510/534 (n28)** | 100 % | 60.3 ms | 21.4 ms | 11.20 |
| B20 | 6400/112 | **156510/534 (n28)** | 100 % | 68.4 ms | 28.3 ms | 8.31 |
| B28 | no service | — | — | — | — | — |

This is the bad-weather fallback the audit was missing: the low bands serve the **n28** 5G cell. The NR database now has
`156510/534` under `3774/112` (13 min, `best_anchor`), `3774/394` (3 min, p95 43.2/jitter 8.2) and `6400/112` (2 min, p95 66.4);
the reliability rule keeps the long `3774/112` pair, the `3774/394` B8 cell is the better p95 challenger once it has more minutes.
The night plan's low-band cells (`112@6400`, `394@3774`) and `NIGHT_BANDS=1,3,7,8,20,28` now make this reachable.

**Gap found by the live scan (fixed):** the band-scan rows had `nr_cell` but an empty `nr_arfcn` (the samples carry the cell string,
not a separate ARFCN column). `summarize` now falls back to `nr_cell`'s ARFCN; version **i** deployed, 36 scenarios pass. The 13:02
rows keep their `nr_cell` (the ARFCN is readable there); the next scan fills the column.

## Files

- [BUSINESS_RULES.md](BUSINESS_RULES.md) — the rules (updated).
- [NIGHTLY-PLAN.md](NIGHTLY-PLAN.md) — the night window wording.
- [guard.py](../keeper/guard.py), [locktest.py](../keeper/locktest.py) — stale docstrings.
- [night.py](../test/scenarios/night.py) — the new low-band/night-window assertions.
