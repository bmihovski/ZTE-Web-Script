# Handoff for the morning and n78 antenna audit

24 November 2026, Europe/Sofia. Start from the ZTE-Web-Script repository root. The earlier root `HANDOFF.md` describes a different release.

## Goal

Audit the scheduled morning check and the loaded antenna comparisons. Verify which LTE/NR profiles were actually measured, which mode the modem applied, and whether another n78 antenna mode or cell lock gives a repeatable improvement over unlocked service. Turn proven measurement or recovery defects into targeted regressions. Preserve the working profile while collecting evidence.

## Current state verified on 24 November

- Live keeper `2026-11-23j`, source commit `c3cbda4` on `5G_data_legacy`, aligned with origin at handoff. The prior deploy passed 43 scenarios, with code backup `legacy/mikrotik-keeper/backups/20261123-212527`. Installed panel marker `2026-11-23-#38`.
- At 08:48:51 Sofia the modem was idle, call-ready and serving B7 `340@3350` + n78 `534@636576`. Both cell locks were released. The kept and effective modes were manual `dir/dir` (`auto=0, ant5=4, ant6=4`). The clock was verified and profile restoration was clear.
- The scheduled run started at **08:00:52**, completed at **08:26:54**, reported `modem good`, and returned LTE and NR locks to `none`. It verified `dir/dir` before each of the six probe attempts. The default scheduler runs an HTTPS/latency screen and a final capacity check. The per-leg iPerf3 measurement and loaded score belong to the separate restore-only comparison. The scheduled run did not choose among antenna modes.
- The live `/ab.plan` displayed only **no lock**. The scheduled report's A and B are the best serving and backup legs within that report. They do not populate the panel's A/B plan, which requires eligible loaded lock-test results. The scheduled A/B labels also do not imply promotion.

### Scheduled run at 08:00

All successful rows observed n78 `534@636576` and were call-ready. Speeds below came from the short HTTPS probe.

| Leg | HTTPS down (Mb/s) | Probe p95 (ms) | Result |
| --- | ---: | ---: | --- |
| Unlocked | 29.89 | 18.9 | Valid scheduled baseline |
| B1 `340@100` | 22.44 | 21.5 | Measured |
| B1 `434@100` | | | No internet during probe, with `URLError(gaierror(-3, 'Try again'))`. Mode readback and an NR observation alone do not establish usable service. |
| B3 `434@1550` | 37.66 | 20.7 | Measured |
| B3 `340@1550` | 36.98 | 17.5 | Scheduled B |
| B7 `340@3350` | 36.11 | 19.3 | Scheduled A |

B1 `493@100` and the B7 backups each had only one usable discovery reading. B3 `66@1550` was queued behind the backup that measured successfully. B8/B20 did not run because the high-band screen was healthy. Verify candidate gaps against `discoveries[].candidates`, `rejected` and the actual legs before opening a bug.

### Current-profile loaded sweep at 08:40

The separate sweep started at **08:40:36**, finished at **08:48:31**, and scored **2 of 4** modes. It restored the unlocked profile and kept mode. The scored transfers used the same endpoint and observed n78 `534@636576` with zero recorded NR changes.

| Mode | Down / up (Mb/s) | Call-ready | Loaded p95 / jitter (ms) | Score | Evidence |
| --- | --- | ---: | --- | ---: | --- |
| `omni/omni` | 155.0 / 27.3 | 100% | 67.5 / 13.3 | 88.2 | Verified |
| `dir/omni` | | | | | LAN upload crossed the idle limit twice |
| `omni/dir` | 159.0 / missing | | | | The modem returned `Status=Complete, UplinkSpeed=, Report=`. The successful download supplied two observations on `534@636576`. |
| `dir/dir` | 155.0 / 28.2 | 100% | 75.7 / 13.4 | 88.0 | Verified |

The 0.2-point difference and 8.2 ms p95 difference do not establish a better unlocked mode. The mode gains for B1 and B7 locks remain unmeasured.

## Evidence and code

Paths are relative to the repository root. The archived JSON is in the git-ignored `legacy/mikrotik-keeper/reports/` folder.

| Path | Use |
| --- | --- |
| `AGENTS.md`, `.dsh/skills/keeper/SKILL.md`, `legacy/mikrotik-keeper/docs/BUSINESS_RULES.md` | Read before interpreting results or changing behavior. |
| `legacy/mikrotik-keeper/reports/morning-20261124080052-handoff.json` | Complete November 24 scheduled result, prior loaded trial, and live status. |
| `legacy/mikrotik-keeper/reports/n78-antenna-sweep-20261124084036-handoff.json` | November 24 four-mode sweep with each failure reason, partial throughput, radio observations and post-run status. |
| `legacy/mikrotik-keeper/reports/morning-20261123-175430-early.json` | Same-server loaded comparison. All six scored legs verified `dir/dir`. B3 `340@1550` scored 89.4 vs unlocked 86.9, below the 5-point rule. B7 `36@3350` had no BOOT inform and ended the run with a successful restore. |
| `legacy/mikrotik-keeper/reports/morning-20261123-121349-user.json` | Rainy comparison. B1 `493@100` looked promising but used a different download server from unlocked. |
| `legacy/mikrotik-keeper/reports/n78-antenna-ab-20261123-145150.json` and `n78-antenna-ab-retest-20261123-151254.json` | Loaded antenna tests with LTE unlocked and LTE-only B3 `434@1550`, NR automatic in both. B3 `dir/dir` led the measured modes in each run. Both unlocked controls in `dir/dir` were invalidated. |
| `legacy/mikrotik-keeper/reports/n78-antenna-sweep-20261123205815.json` and `n78-antenna-sweep-20261123212703-diagnostic.json` | Same-evening unlocked checks. `dir/omni` led one incomplete sweep. In the repeat it had a blank download result, while `dir/dir` scored 90.8. The apparent ranking varied within the evening and both runs had incomplete coverage. |
| `legacy/mikrotik-keeper/reports/low-b8-394-retest-20261123-140849.json` | Same-server B8/n28 vs unlocked/n78. B8 carried traffic with n28 and was much worse for loaded p95 and call readiness. Exact restoration was verified. |
| `legacy/mikrotik-keeper/keeper/morning.py` | `_leg`, `_record_load`, `_discover`, `_test_band`, `_decision`, scheduled behavior and restore. |
| `legacy/mikrotik-keeper/keeper/absmoke.py`, `captest.py`, `brain.py` in the keeper directory | Loaded four-mode tests, iPerf3 result freshness and WAN guard, ranking and quality validation. |
| `legacy/mikrotik-keeper/keeper/devices/zte/{antennas,profile,web}.py` and `keeper/{locktest,guard,abplan}.py` | Saved/effective antenna choice, modem readback, crash recovery, NR lock-test source, and fallback selection. |
| `legacy/mikrotik-keeper/test/scenarios/{morning,absmoke,captest,sweep,cell_mode,low_profile}.py` | Existing test fixtures and regressions to extend. |

## What has been tried and what remains open

- **Mode verification is working.** The keeper reapplies the configured mode at boot and reads it back before scoring a high-band or unlocked morning leg. The applied mode and its source are observable. The scheduled check measures one configured mode per leg. The loaded antenna tests exercise all four modes and restore the original settings.
- **Antenna ranking is still incomplete.** A valid B3 result with NR automatic does not establish a preferred mode for a pinned B3/NR pair, B1 lock, B7 lock, or arbitrary unlocked NR cell. Several earlier runs lacked the same-server control or lost one direction. A high SINR or speed can coexist with high p95 or poor call readiness on the same NR cell.
- **Incomplete iPerf3 pairs have two observed causes.** WAN traffic invalidated some windows. In others the modem returned `Status=Complete` with the direction's speed and `Report` empty. This has occurred in both directions and on modes that measured successfully elsewhere. Release `j` retains the good direction and its radio samples while leaving the combined score empty. A cell change or a particular iPerf server failure is unproven for an empty modem result.
- **Earlier web write refusals were recoverable.** Fresh web login per loaded mode now avoids reusing expired sessions. The ZTE has one web login. The panel asks operators to close modem clients and gives 20 seconds before the start, with a Telegram all-clear after restoration for UI-started tests.
- **Profile-specific learned preferences are a design idea.** ZTE `data/state.json` stores the kept default, and an active/pending exact NR lock can use its newest eligible NR lock-test mode with `AUTO=on`. Mode readbacks and loaded test results do not change that selection. The proposed SQLite preference keyed by device + exact LTE/NR lock profile, with the resolved target journaled for recovery, is absent. A and B labels can change.

## Next steps

1. **Read fresh state before starting any job.** Check `GET /openapi.json`, `/status`, `/morning`, `/ab`, `/lock-tests?rat=lte`, `/lock-tests?rat=nr`, `/events?n=80` and `/captest`. Check run timestamps in Sofia time. In `/morning`, `result` is the scheduled or regular check and `trial` is the last restore-only loaded comparison. In `/ab`, `antenna_sweep` is the current-profile test and `antenna_trial` is the unlocked-versus-LTE-lock test. Archive the JSON before a repeat replaces the latest result. The dated snapshots above are on this Mac.

   ```bash
   KEEPER_AUDIT_URL=http://keeper.lan:7557
   curl --noproxy '*' -fsS --max-time 10 "$KEEPER_AUDIT_URL/morning"
   curl --noproxy '*' -fsS --max-time 10 "$KEEPER_AUDIT_URL/ab"
   curl --noproxy '*' -fsS --max-time 10 "$KEEPER_AUDIT_URL/status"
   ```

   If local DNS fails, use `http://192.168.6.250:7557`. Keeper status and logs are available from the keeper directory via `.venv/bin/python3 tools/deploy.py status` and `tools/deploy.py logs -n 100` with that interpreter.

2. **Build an evidence matrix before deciding.** For unlocked and each attempted LTE lock, record requested LTE/NR locks, actual serving LTE/NR IDs, antenna settings and source, boot generation, phase coverage, iPerf endpoints and mode scores where valid. Include B1 `340@100`, B3 `434@1550`, B3 `340@1550`, and B7 `340@3350`. Preserve B1 `434@100` as failed and B1 `493@100` as not attempted this morning. Add and label additional candidates based on live evidence. For a profile with automatic NR, record each observed NR cell and its SINR/RSRP/RSRQ independently.

3. **Check each measurement gate.** Separate antenna write/readback, lock acknowledgment, BOOT, raw band/cell readback, actual serving cells, WAN quiet, transfer success and probe coverage. For loaded results require both down/up windows, matched endpoints against the control, the expected cells when locked, and fresh radio observations inside both windows. Record per-mode NR changes and any observed n78 loss when unlocked. Show stable-pair results separately from behavior with reselection. Partial throughput is diagnostic. A failed BOOT or restore is a separate outcome. Inspect final raw LTE bands, LTE and NR locks, antenna settings, pending restore, and service recovery.

4. **Run the smallest useful live validation in an authorized quiet window.** First fill the gaps that can change a decision. Use the same iPerf endpoint per direction, 4 reverse download streams, 1 upload stream, and stable heading. Current comparisons use at least two fast informs plus 5 seconds per transfer, typically 25 seconds. Repeat the control around a challenger when the earlier result came down to one short window, an outlier, or changing cell traffic. Compare actual call readiness, loss, p95 and jitter as well as throughput and SINR/RSRP/RSRQ. If practical, repeat across different mornings or tag wet/dry conditions without treating weather as a proven cause.

   | Question | Restore-only entry point |
   | --- | --- |
   | Four modes on the actual current profile, including an existing NR lock | `POST /ab-smoke?antenna_lte=current` |
   | Four modes unlocked vs a candidate LTE anchor | `POST /ab-smoke?antenna_lte=340@3350` or substitute another eligible high-band LTE cell. This leaves NR automatic in both profiles. |
   | Fixed-mode loaded high-band cell ranking | `POST /morning?apply=0&bands=1,3,7` |
   | B8/n28 fallback behavior | `POST /morning?apply=0&cell=394@3774&nr=534@156510` when low-band evidence is needed. The n78 mode sweep is irrelevant to n28. |

   The UI versions of the antenna tests include a close-the-app countdown and Telegram notices. Close ZTE Helper, modem browser tabs and the Android app so they do not claim the modem web session. Archive reports and verify restoration before starting the next test. Report remaining gaps rather than claiming coverage for an untested exact lock pair.

5. **Investigate reproducible defects.** For `Complete` with empty speed, capture direction-specific TR-069 state, Enable acknowledgment, start/finish evidence, last-test timestamp and whether result fields actually changed. Inspect `captest._ip3_values`, `_iperf_once` and `ip3_result` before changing acceptance rules. Audit whether antenna verification after the wake window could discard a viable candidate on a cold boot. Check discovery's observation timing if candidates repeatedly miss the two-reading gate. Reproduce any wrong shared-PCI attribution with the full EARFCN. Use a failing scenario before changes and a real-body test for mocked paths.

6. **Report operational decisions and evidence.** For each profile, say whether it showed a repeatable gain, tied the control within the margins, failed measurement, or lacks coverage. Use the keeper's quality margins (5 pp call-ready, 1 pp loss, 10 ms p95 or jitter), and require the 5-point loaded score gain and no clear quality regression when promoting an LTE lock under the loaded policy. The signal tie-break stays in place. A validated improvement may justify a narrower follow-up or a proposed profile preference. Full coverage is required before claiming the best mode for all candidates. Keep the current preference until comparable evidence supports changing it.

## Constraints

- This handoff involved read-only live inspection and local archives. It grants no new deployment, scheduling, reset, or network configuration authority.
- Keep ZTE and ATL evidence separate. Treat PCI with EARFCN or ARFCN as the identity. `534@636576` n78 and `534@156510` n28 are different.
- Keep the one-reboot lock transaction, exact restoration and shared modem reservation. A run with pending recovery blocks another disruptive test. Avoid concurrent WLAN traffic or a live call, and leave guard thresholds in place. Do not force NR `534` based on an unlocked antenna result.
- After an authorized code change, follow the workflow in `AGENTS.md`. Write the regression first, bump the version, run the full `tools/deploy.py check`, follow standard backup/deploy/verify, and validate the specific run. The deploy tool's `validate --rat lte|nr` reads lock-test rows. Validate morning trials and antenna sweeps from their corresponding API records.
