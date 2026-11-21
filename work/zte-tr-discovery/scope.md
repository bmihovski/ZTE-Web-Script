# Case Scope

## meta
- case_id: zte-tr-discovery
- created: 2026-11-21T11:15:47+02:00
- operator: local
- project_root: /Users/boyanmihovski/Privatespace/ZTE-Web-Script
- primary_skill: reverse-engineering/SKILL.md
- primary_id: R0
- lead_role: lead
- specialist_roles: []
- hint: ZTE MC889 TR protocol and feature discovery for stable low-latency automation
- preset: own-system

## auth
- status: granted
- basis: own_system
- evidence_of_auth: owner-operated home uplink; user explicitly requested device discovery 2026-11-21
- MUST NOT proceed if status != granted

## in_scope
- assets:
  - 192.168.254.1
  - 192.168.6.1
  - 192.168.6.250
- surfaces: [network, web, api, tr069]
- activities: [recon, read_only_probe, report]

## out_of_scope
- assets: []
- activities: [dos, phishing_real_users, unrestricted_exfil]

## network_profile
- mode: authorized_target_only
- notes: |
    offline | lab_only | authorized_target_only | unrestricted_lab
    Change mode only after auth.status = granted.
    Presets: offline-sample | ctf-public | own-system

## deliverables
- report: true
- field_journal: true
- diagrams: true
- timeline: true

## constraints
- timebox: {}
- stealth: low
- data_handling: anonymize

## signoff
- ready_for_act: true
- checklist:
  - [x] auth.status = granted
  - [x] in_scope.assets non-empty OR offline sample path set
  - [x] network_profile.mode chosen
  - [ ] out_of_scope reviewed
  - [ ] roles assigned (see skills/ops/role-map.md)

## ops_refs
- skills/ops/scope-contract.md
- skills/ops/evidence-finding-path.md
- skills/ops/role-map.md
- skills/ops/timeline-workitem.md
- skills/ops/IDENTITY.md
