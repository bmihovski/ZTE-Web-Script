# Timeline (append-only)

## 2026-11-21T11:15:47+02:00 | lead | init
- action: case-init
- command_or_ref: skills/scripts/case-init.sh
- result_summary: case directory created; scope ready_for_act=true
- artifacts: [scope.md, workitems.md]
- evidence_ids: []
- decision_delta: [case_initialized]
- carry_forward_refs: [scope.md]
- next: open PRIMARY SKILL.md and ACT within scope

## 2026-11-21T11:35:00+02:00 | lead | recon + live incident
- action: authorized read-only discovery of ZTE MC889 / MikroTik / keeper; live 5G lock loop diagnosed and fixed
- command_or_ref: python socket scan 192.168.254.1 (80/443); nc 7547/22/23; SSDP M-SEARCH; GET /tr069?names=all (4,982); Device.ManagementServer; static js/service.js + menu/view modules
- result_summary: ZTE LAN = web only (80/443); no TR-064/SSH/Telnet/UPnP; TR-069 1.0 + TR-181 2.11 + TR-143 + TR-111 STUN; watchdog web-only; 626 writable TR-069 entries. Live loop: lock_reconcile re-applied the NR lock on the normal NSA idle release (LockNr5gCell='') every ~5 min; fixed in 2026-11-21f (skip NR reconcile on idle no5G; regression test), lock removed, uptime stable, verdict good.
- artifacts: report/TR-PROTOCOLS-2026-11-21.md; test/scenarios/guard.py; keeper/guard.py
- evidence_ids: []
- decision_delta: [nr_idle_release_not_missing]
- carry_forward_refs: [report/TR-PROTOCOLS-2026-11-21.md, scope.md]
- next: test fast PS reconnect and NR band lock on an idle link; read web-only settings before changes
