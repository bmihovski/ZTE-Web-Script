# ZTE Keeper handoff

2026-11-22, Europe/Sofia. Start from the root of ZTE-Web-Script. Paths below are relative to that root.

## Goal

Run the keeper as a low-maintenance home uplink. Preserve call quality and stable NSA pairs, recover service when a lock fails, and use daytime tests only when they can change a decision. The router will normally be powered off overnight. This handoff is for a read-only second opinion on the plan and the live release.

## Current state

- Live `2026-11-22ab`, verified against the OpenAPI contract. At 15:11:51 Sofia the connection was good on `340@3350` + `534@636576`. Both managed locks were active and the kept antenna mode was dir/dir. Code backup `20261122-144533`. The branch is `5G_data_legacy`, one commit ahead of origin. The deployed changes and new reset scenario are still in the working tree.
- The final gate passed all 42 scenarios. The helper bridge self-test, JavaScript syntax check, and panel control test passed. Smoke Start and Stop were exercised live before the last registry fix. The installed macOS helper points to the working-tree app and userscript, marker `2026-11-22-#33`.
- The one-shot reset discarded measurement history and compacted the database from 38.4 MB to 0.1 MB. It retained the app lists, managed locks, antenna choices, site notes, and capacity inputs for SQM. Thirty-four recovered cell IDs were verified across the second reset. New minute rows use typed cell/metric columns. The broader run/leg schema redesign was deferred.
- Morning is scheduled 08:00–12:00 Sofia. It uses fresh signal evidence to discover candidates, ranks known NSA pairs by measured quality, and tries another backup when the first cannot measure. Reachability failures have a per-device cache. Scheduled capacity probes defer during morning or competing LAN traffic.
- Scheduled night screen, arm reminder and separate Saturday confirmation were retired, and the live `LOCK_PAUSE` entry is disabled. The morning job retains its configurable low-band fallback and Saturday low-band option. Keep the lock guard, capacity probes, automatic quality A/B, and on-demand smoke. Manual smoke reports reachability, progress and failure reasons, and restores the managed pair.

## Files in play

| Path | Use |
| --- | --- |
| `AGENTS.md`, `.dsh/skills/keeper/SKILL.md`, `legacy/mikrotik-keeper/docs/BUSINESS_RULES.md` | Invariants, operation and release rules |
| `legacy/mikrotik-keeper/docs/HANDOVER-2026-11-23.md` | Earlier handover for v; use for the historical failure |
| `legacy/mikrotik-keeper/keeper/{morning,captest,locktest,guard,abplan,absmoke}.py` | Candidate selection, load eligibility, modem reservation, smoke and recovery |
| `legacy/mikrotik-keeper/keeper/{store,common,brain}.py` and `test/scenarios/measurement_reset.py` within the keeper | Typed samples, one-shot reset, registry boot order and regression test |
| `legacy/zte-script-legacy.js`, `legacy/app/zte_app.py` | Smoke controls and helper allowlist |
| `legacy/mikrotik-keeper/reports/baseline-20261122ab.json` | Fresh comparison after the second reset |
| `legacy/mikrotik-keeper/reports/morning-rc-20261122z.json` | Earlier B7 comparison for context; the live database has since been reset |

## What failed and what the evidence supports

- PCI 202 previously had usable radio readings and failed to get internet. Discovery establishes a candidate worth trying. A qualified loaded measurement is the evidence for a decision.
- The first reset exposed a startup bug. Rebuilding a missing measurement registry overwrote the independent cell-ID catalog. Recovered 34 IDs from the helper cache, fixed the startup order, added a regression, deployed ab and re-ran the reset. The pre-reset catalog count is unavailable.
- The ab B7 run at 14:47–15:04 recorded an unlocked baseline with `iperf_phases`, 20.0 s of load, 155/27.7 Mbit/s, 50% call-ready, 117.1 ms p95, 21.4 ms jitter, 0% loss and score 73.9. n78 appeared around the transfers. The transfer windows contained zero radio samples.
- The guard rejected both loaded attempts on `340@3350` when LAN upload reached 7.25–7.38 Mbit/s. The sole discovered backup, `375@3350`, failed the 90 s internet budget. Recovery then took about 4½ minutes because TR-069 timed out before web fallback. The run reported `incomplete`, restored the original pair and recorded zero overlapping capacity results.
- The earlier z run scored locked B7 at 89.3 versus 66.8 unlocked with the same test servers. Both showed the same NSA pair around load and had zero in-transfer radio samples. One sequential run gives limited evidence about the effect of locking.

## Next steps and constraints

1. Review the plan and targeted diff. Check `git status` for the untracked regression scenario alongside the deployed changes. The user will decide when to commit.
2. For a fresh operational check, run `.venv/bin/python tools/deploy.py --url http://192.168.6.250:7557 status` from the keeper directory. Checking the first cold boot and ordinary morning results is the remaining acceptance step.
3. Keep the independent review read-only. The current request authorizes analysis of the plan. Leave live locks, deployment, history and scheduling to a later explicit action.

## First critique

- **Keep.** Use call-ready, loss, p95 and jitter under comparable load for network decisions. Use signal for discovery, reachability and tie-breaking. Keep smoke for diagnosis. It can narrow the problem to the uplink or local path, with limited evidence about an individual LAN device.
- **Defer.** A full LTE/NR comparison and any new policy tuning can wait for frequent real symptoms or quiet comparable data. The daily morning probe and restore-only loaded comparison currently serve different purposes. Only the unlocked ab leg is a scored reference.
- **Investigate if it recurs.** Prioritize the restore delay after a dead cell. Check whether web restoration can safely begin earlier after lost informs, and whether the morning catch-up reboots interrupt normal use. Review data eligibility and smoke recovery for bugs that warrant action now.
