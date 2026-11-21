# TR protocols & device feature evaluation — ZTE MC889

Date: 2026-11-21 · Case: `work/zte-tr-discovery` · Owner-operated home uplink (authorized, read-only discovery)

## 0. Executive summary

- **TR-069 does not expose the watchdog.** The whole `Device.` tree is **4,982 names**; no watchdog/ping-monitor object.
- **TR-143 does not either.** The modem implements TR-143 (162 names: Download/Upload/UDPEcho/IPPing/TraceRoute/NSLookup, plus a
  ZTE `Iperf3` vendor object), but these are one-shot diagnostics, not a persistent monitor or a reboot control.
- **The watchdog is web-only** (`net_link_detect_*`, `watch_dog_reboot_enable`, `WATCH_DOG_SWITCH`); the keeper now reads it
  (`GET /zte/watchdog`, and after every boot). It is currently **disabled**.
- **The ZTE's LAN surface is only the web UI: TCP 80 and 443.** TCP 7547 is refused, 22/23 are filtered, no SSDP/UPnP answer.
  There is no TR-064, SSH, Telnet or UPnP automation path. The WAN-side `http://10.47.80.140:7547/...` is the CWMP
  connection-request listener, not a LAN service.
- **Supported TR stack:** CWMP/TR-069 1.0, TR-181 Device:2.11, TR-143, TR-111 STUN (enabled: `SupportedConnReqMethods=HTTP,STUN`,
  STUN `85.162.11.196:3478`), TR-069 Annex G UDP CR; XMPP objects exist but are unused; no TR-064.
- **Highest-value additions** (in order): fast PS/data-session reconnect before rebooting; NR band lock (n78 only) alongside the
  cell lock; connection-mode/roaming/bearer preference; DNS/UDP-echo latency probes; thermal/CPU correlation.
- **Critical live finding:** the "endless reboots" seen today at 11:18–11:27 were **not** the watchdog (already disabled at
  11:20:27). The keeper's 5G lock guard treated the normal NSA idle release (`nr_cell=no5G`, `LockNr5gCell=''`) as a lost lock
  and re-applied it with a reboot every ~5 min. The exact line was
  `nr_lock 534@636576: the ZTE has no such lock (LockNr5gCell = '') - set again when idle` (11:18:52 and 11:23:51), with the event
  `nr lock 534@636576 missing | why: ZTE LockNr5gCell = ''`. Fixed in **2026-11-21f** (skip the NR reconcile on an idle `no5G`
  sample; regression test added), lock removed. Uptime after the fix: stable, verdict `good`; no such line after the 11:29:37 restart.

## 1. Scope and method

- Authorized case: `work/zte-tr-discovery/scope.md` — `auth.status=granted`, `own_system`, `authorized_target_only`;
  assets `192.168.254.1` (ZTE), `192.168.6.1` (MikroTik), `192.168.6.250` (keeper). Read-only discovery; no exploitation.
- Tools: `nmap`/`naabu`/`masscan` were **not installed**; discovery used Python sockets, BSD `nc`, `curl`, the keeper's TR-069
  API and static analysis of the device's own JS. The tool-index file referenced by the skill was absent; `command -v` was used.
- Scan rate lesson: a 250-thread scan of 1–10000 returned **no** ports (the modem rate-limited/refused); a gentle 20-thread scan
  found 80/443. Keep scans ≤20 workers and targeted.

## 2. Service surface

| Target | Open TCP | Evidence |
|---|---|---|
| ZTE `192.168.254.1` | **80**, **443** | `GET /` → 302 `/index.html`, CSP/X-Frame hardening; 443 accepts TCP but did not answer `curl -k` |
| ZTE LAN | 7547 refused, 22/23 filtered, no SSDP | `nc -z`, Python connect, `M-SEARCH` timeout |
| ZTE WAN | `10.47.80.140:7547/<token>` | `Device.ManagementServer.ConnectionRequestURL` (CWMP listener) |
| MikroTik `192.168.6.1` | 22, 80, 8291, 8729, 2000, 53 | RouterOS SSH/Web/WinBox/API-SSL/bandwidth-test/DNS; **8728 closed** |
| Keeper `192.168.6.250` | 7557 | keeper API/ACS |

Consequence: every vendor-only feature must go through the **single-session web UI** or the **ACS-initiated TR-069 session**.
There is no second channel (no TR-064, no SSH/Telnet on the LAN).

## 3. Protocol matrix

| Spec | Supported? | Evidence | Data-plane / automation value |
|---|---|---|---|
| TR-069 (CWMP 1.0) | Yes | `Device.DeviceInfo.SpecVersion=1.0`, `EnableCWMP=1`, ACS URL reaches the keeper | Full tree, 626 writable entries; one read per inform |
| TR-181 Device:2.11 | Yes | `Device.RootDataModelVersion=2.11` | The `Device.` data model |
| TR-143 | Yes | 162 names under `Device.IP.Diagnostics.`; keeper's `tr143.supported=True` | Download/Upload/UDPEcho/IPPing/TraceRoute/NSLookup; heavy, one-shot |
| TR-111 STUN | Yes, enabled | `SupportedConnReqMethods=HTTP,STUN`, `STUNEnable=1`, STUN `85.162.11.196:3478`, `UDPConnectionRequestAddress=149.62.204.191:44950` | Connection requests only |
| TR-069 Annex G UDP CR | Yes | `UDPConnectionRequestAddress`, keepalive 55–65 s | Connection requests only |
| XMPP CR (Amd 4) | Objects present, unused | `Device.XMPP.*`, `ConnReqXMPPConnection` empty | None |
| TR-064 | **No** | LAN 7547 refused; LAN is ZTE goform | None |
| TR-098 | Legacy hint | Process `zte_topsw_tr098db` beside `zte_topsw_tr069_new`; ACS model is TR-181 2.11 | None directly |
| UPnP | Not advertised | No SSDP answer; `Device.UPnP.Device.Enable` exists | Security risk, no latency value |

## 4. Channel pros / cons

| Channel | Pros | Cons |
|---|---|---|
| **TR-069** | Structured, ACS-driven, no login/session contention; 4,982 names / 626 writable; integrates with the keeper's read/write/reboot; works remotely (CPE initiates) | Only firmware-exposed objects; no watchdog/antenna/thermal/PS-reconnect; lock writes reboot (~2 min); one read per inform; ad-hoc reads can take 30–90 s and are empty/transient during a boot |
| **TR-143** | Standardized, modem runs the test (no router CPU/TLS bottleneck); good capacity/path evidence; already used | One-shot, heavy (download ~250 MB), needs a target; no continuous monitor, no control; UDPEcho needs a cooperating server |
| **Web goform** | Richest vendor controls: antennas, NR band mask, connect/disconnect, PS restart, thermal, MTU/MSS, power save, watchdog; immediate (no inform wait) | Single session — a keeper login logs the browser out; password hash + AD token; brittle/undocumented; LAN-only; slow/flaky under load; no structured errors/audit |
| **RouterOS SSH / API-SSL** | Full router control (SQM/CAKE, queues, DNS, firewall); stable, authenticated; keeper already uses SSH | Not the modem; radio behavior unchanged; API adds complexity; SQM ph 3/4 closed by data |
| **UPnP / TR-064 / SNMP** | — | Not available / not advertised / security risk; no data-plane benefit |

## 5. Feature inventory

### 5.1 TR-069 — writable entries relevant to automation (626 total)

- **Radio locks:** `Device.Cellular.Interface.1.LockLTEBand`, `LockLTECell`, `LockNr5gCell`, **`LockNr5gNSABand`**,
  **`LockNr5gSABand`**; `Device.Cellular.Interface.1.Enable`.
- **Connection:** `Device.Cellular.AccessPoint.N.APN/Author_mode/Enable`, `Device.Cellular.RoamingEnabled`,
  `Device.IP.Interface.N.Reset`, `Device.PPP.Interface.1.Reset`, `Device.PPP.Interface.1.Enable`.
- **Modem self-recovery:** `Device.Cellular.Interface.1.Stats.DatastallMonitorSwitch`.
- **Diagnostics:** `Device.IP.Diagnostics.IPPing.*`, `TraceRoute.*`, `UDPEchoDiagnostics.*`, `UDPEchoConfig.*`,
  `NSLookupDiagnostics.*`, `Iperf3.*`, `DownloadDiagnostics/UploadDiagnostics` options.
- **Stability/security:** `Device.IP.Interface.*`, `Device.Ethernet.Interface.*`, `Device.Firewall.*`, `Device.NAT.*`,
  `Device.DHCPv4.Server.*`, `Device.X_ZTE-COM_ROUTER.CSFirewall.*` (SSH, LAN web, WAN ping), `Device.UPnP.Device.Enable`,
  `Device.UserInterface.RemoteAccess.*`.
- **Timing/firmware:** `Device.Time.Enable`, `Device.FOTA.Command/AutoUpdate`, `Device.REBOOTSCHEDULE.*`,
  `Device.ManagementServer.STUNEnable`.
- **Vendor router:** `Device.X_ZTE-COM_ROUTER.Network.IGMP.IGMP_Snooping`, `WanMode`, `LanDevice.lan_eth_check_reboot`.

### 5.2 Web-only (not in TR-069) — the high-value ones

- `CONNECT_NETWORK` / `DISCONNECT_NETWORK` — bring the PS data session down/up without a CPE reboot (the web UI's
  Connect/Disconnect; polls `ppp_status`).
- `PS_NO_SERVICE_RESTART_SET` (`ps_no_service_restart_flag`) — modem auto-restart when there is no service.
- `SET_CONNECTION_MODE` (`connectionMode` = `auto_dial`/manual, `roam_setting_option`) — auto-dial and roaming.
- `SET_BEARER_PREFERENCE` (`BearerPreference`) — bearer/5G preference.
- `WAN_MBN_MODE_SET` (`mbn_mode`) — operator/MBN profile.
- `WAN_PERFORM_NR5G_BAND_LOCK` (`nr5g_band_mask`) and `WAN_PERFORM_NR5G_SANSA_BAND_LOCK` (`nr5g_band_mask`, `type`) — NR
  band selection; the web equivalent of `LockNr5gNSABand` / `LockNr5gSABand`.
- `SET_THERMAL_CONTROL` (`thermal_control_enable`, `thermal_led_enable`), `BSP_THERMAL_THERSH_SET` (`sensor_id`,
  `thermal_type`, `v1..v3` ×1000), 9 thresholds via `bsp_thermal_thersh_1..9`.
- `SET_DEVICE_MTU` (`mtu`, `tcp_mss`), `SET_AUTO_POWER_SAVE` (`auto_power_save`).
- `SET_NETWORK` (`NetworkNumber`, `Rat` 0/2/7/12) — manual operator selection/scan.
- `WATCH_DOG_SWITCH`, `BSP_ANTENNA_STATE_SET`, `WAN_ANT_SWITCH_SET`, `LTE_LOCK_CELL_SET`, `NR5G_LOCK_CELL_SET`,
  `TR069_MODULE_SET`, `SIGNAL_QUALITY_DETECT_START/CANCEL`, `SET_DEVICE_LED`, `SET_AUTO_POWER_SAVE`.

### 5.3 Read-only telemetry (beyond the keeper's current sample)

- `Device.Cellular.Interface.1.Stats.*` (bytes/packets/discards/errors, rates, max throughput, `DatastallMonitorSwitch`,
  `ParamsSwitchStatus`), `LTE_CA.N.*`, `NR_CA.N.*`, `NR5G_SSB_*`, `RRC`, `NetworkInUse`, `CELLID/ECGI/NCGI`, `USIM.*`.
- `Device.IP.Interface.N.Stats` (WAN/LAN errors/drops), `Device.PPP.Interface.1.LastConnectionError/ConnectionStatus`.
- `Device.ThermSensor.*`, `Device.DeviceInfo.ProcessStatus` (CPU, processes), `MemoryStatus`, `FaultMgmt.CurrentAlarm/History`.
- `Device.X_ZTE-COM_ROUTER.WANInfo.1.*` (connect status, rates), `LanDevice.*` (hosts/ARP).

## 6. Ranked automation opportunities

| Priority | Feature | Why | Risk / cost | Proposed test |
|---|---|---|---|---|
| **P0 (done)** | Fix the NR lock reconcile loop | It was rebooting the modem every ~5 min; any new feature on top of it would be noise | none | regression test; live uptime stable |
| **P1** | Fast PS reconnect (`CONNECT_NETWORK`/`DISCONNECT_NETWORK`) as a fallback before a CPE reboot | Recovery in seconds instead of ~2 min; no radio lock change | web session/log-in; may not clear a cell lock (a lock applies at boot) | only when the radio is attached but `ppp_status` is down/stalled; measure down/up time; revert on failure |
| **P1** | NR band lock (n78) via `LockNr5gNSABand`/`SABand` or `WAN_PERFORM_NR5G_*` | Prevents fallback to a slower/contended 5G band; pairs with the cell lock | need the mask/format; an unattachable lock can repeat today's failure | test one band on an idle link; verify the 5G leg returns; keep the cell lock |
| **P2** | Connection mode / roaming / bearer preference | Ensure `auto_dial`, roaming off; an LTE fallback when 5G is poor (the night screen already tests LTE) | a mode change may reconnect/reboot; operator/NSA constraints | read current values; one reversible change on an idle link; compare with the night LTE rows |
| **P2** | DNS + UDP-echo latency probes | DNS is a common "feel" latency source; the current probe is TCP to targets | extra traffic; UDPEcho needs a server | DNS query latency to the AdGuard upstream; compare with the existing p95/jitter |
| **P2** | Thermal/CPU correlation | Explain throughput drops / throttling; outdoor unit | reading thresholds only; changing them is riskier | log temps/CPU with speed; read the 9 thresholds; no change without evidence |
| **P3** | MTU/MSS, power save, IGMP snooping | MTU 1500 expected; power save can add latency; IGMP snooping can reduce multicast flooding | topology-dependent; IPTV path | read current values; set power save off; test IGMP only if IPTV multicast floods the LAN |
| **P3** | Interface/fault stats and alarms | Root-cause evidence (drops/errors, alarms) | low | add to the sample/log, no action |
| **Do not** | SSH (`CSSSH.Enable`), UPnP, remote access, WAN ping | Security exposure, no latency benefit | high | keep off |
| **Do not** | Watchdog | Reboot loop on a failed ping; explains "endless reboots" | high | keep off; the keeper detects it |

## 7. Tooling evaluation

- **TR-069 API (`/tr069`)** is the best structured inventory: `names=all` returned the 4,982-name tree in ~45 s; single values
  take one inform (~30–90 s). It is read-only by design; writes go through the keeper's queued `tr069_set`.
- **TR-143** is best for capacity and path evidence, not continuous monitoring. The keeper already uses `DownloadDiagnostics`
  and `Iperf3`; adding UDPEcho/NSLookup is possible but the keeper's own probes are cheaper and controllable.
- **Web goform** is the only source for the vendor-only controls. Static analysis of `js/service.js` + the view modules found
  the goform IDs and fields without a login; that is safer than probing the device. Use the keeper's single-session `Web` class
  for reads/sets, always log out, and never parallelize web calls.
- **RouterOS** (SSH 22, API-SSL 8729; 8728 closed) is the right place for SQM/CAKE and per-client shaping; it cannot change
  the modem's radio. The user's SQM phases 3/4 are closed by data; reopen only with new latency evidence.
- **Discovery tooling**: nmap/naabu/masscan absent; Python socket + `nc` + `curl` was sufficient. The modem rate-limits
  aggressive scans (250 threads → no result; 20 threads → 80/443). The `work/` tool-index was missing, so `command -v` was used.
- **Evidence quality**: a live 5G lock loop made the TR-069 cellular values read as zeros (the modem was rebooting); do not
  diagnose radio values from an ad-hoc read during a boot — check `uptime_s` first.

## 8. Immediate actions taken

1. Stopped the live loop: removed the NR lock (`nr_lock none`); the guard no longer re-applies it.
   **Reboot attribution:** the line `5g lock 534@636576: 534@636576 set` (11:18:52 / 11:23:52) is the keeper's
   `locks_apply` -> TR-069 `SetParameterValues` + `Reboot`; `reboot accepted by the ZTE` (11:18:53 / 11:23:53) is the CPE's
   `RebootResponse` to that RPC; the next inform carried `1 BOOT+M Reboot` (an ACS-requested reboot). A watchdog reboot would
   be a local `1 BOOT` without `M Reboot` and without a keeper `reboot sent`/`RebootResponse`. So the reboots were **keeper
   logic**, not the modem software; the modem's only contribution was the normal NSA idle `LockNr5gCell=''` read-back.
2. Fixed `guard.lock_reconcile` to skip the NR lock when the 5G leg is idle `no5G` (the ZTE's `LockNr5gCell` read-back is
   empty then); with traffic an empty read-back still reconciles as a lost lock. Regression test in `test/scenarios/guard.py`.
3. Bumped `VERSION` to `2026-11-21f`, full check (35 scenarios, 0 failed), deployed and verified. Modem: uptime stable,
   `verdict=good`, `nr_lock=None`, watchdog disabled.
4. Added the 2026-11-21g tools: `GET /zte/settings` (web-only settings + audit; also read after every boot in the antenna apply's
   web session) and `POST /ps-reconnect` (fast PS disconnect/connect, no CPE reboot; `brain.offline_decide` tries it before an
   outage reboot with `PS_RETRY_MIN=10` / `OUTAGE_PS_MAX_DAY=3`; a failure falls through to the reboot logic). Tests:
   `test/scenarios/web.py` (settings/audit + PS), `test/scenarios/outage.py` (PS fallback), `test/scenarios/api.py` (routes).
   36 scenarios pass.

   Current web settings (read 2026-11-21 11:58): `connectionMode` empty (bridge), `roam_setting_option=off`,
   `ps_no_service_restart_flag=1`, `auto_power_save` empty/off, `mtu=1500`, `tcp_mss=1460`, `thermal_control_enable` empty,
   `upnpEnabled=1`, `net_select_mode=auto_select`, `net_select=WL_AND_5G`, `nr5g_band_lock=1,3,7,8,20,28,78`,
   `ppp_status=ppp_connected`, `modem_main_state=modem_init_complete`. The audit flags the **PS no-service restart** (a reboot-loop
   candidate) and **UPnP** (LAN exposure); both are still ON - decide whether to turn them off (test the PS restart off first).
5. Band scan (2026-11-21h): an active/pending NR cell lock is **refused** - the scan needs the network's own 5G pair, and a band change
   can lose a pinned cell (the guard would re-apply it with a reboot). Per band, after the LTE settle, a short load test
   (`BANDSCAN_TEST_S` 25) raises the NSA 5G leg; the row now carries the 5G cell/ARFCN/SINR/RSRP and call-ready/loss/p95/jitter/down,
   and the (5G cell, anchor) pair goes into the NR database. The night plan's band-scan `why` shows the call-ready; B20/B28 are the
   bad-weather candidates.

   The 08:55:52 `534 -> no5G` / 08:56:52 `no5G -> 534` pair is the normal NSA idle release + re-add (the log says "link idle: normal
   in NSA"), not a lost lock. The band scan at 08:57 was started while the NR lock was still active (the old `start()` checked only
   the LTE lock), so its per-band 5G view was the pinned cell. The new guard refuses that; the per-band load test makes the pair
   visible even when an idle link would show `no5G`.

## 9. Evidence (key commands)

```text
# ZTE LAN, gentle scan
python3 socket-connect scan 1-2048 + high ports, 20 workers  ->  80, 443
nc -z 192.168.254.1 7547  ->  refused
nc -z 192.168.254.1 22 23 ->  timeout (filtered)
M-SEARCH 239.255.255.250:1900 -> timeout (no UPnP)
curl -D- http://192.168.254.1/ -> 302 /index.html, CSP headers
curl -D- https://192.168.254.1/ -k -> no HTTP response

# TR-069
GET /tr069?path=Device.&names=all        -> 4,982 names
GET /tr069?path=Device.RootDataModelVersion -> 2.11
GET /tr069?path=Device.DeviceInfo.SpecVersion -> 1.0
GET /tr069?path=Device.ManagementServer. -> SupportedConnReqMethods=HTTP,STUN, STUNEnable=1
GET /status config.tr143 -> supported=True, n=162

# Web (static, no login)
curl http://192.168.254.1/js/service.js
curl http://192.168.254.1/js/network/dial_setting.js
curl http://192.168.254.1/js/adm/thermal_switch.js
curl http://192.168.254.1/js/adm/ant_settings.js

# Live incident
11:18:52 switch 534->no5G, 11:18:52 nr lock missing, 11:18:53 reboot sent, 11:20:10 boot
11:23:51 switch 534->no5G, 11:23:51 nr lock missing, 11:23:53 reboot sent, 11:25:10 boot
11:25:53 5g lock removed, 11:27:09 boot; after fix uptime 288 s, verdict good, nr_lock None
```

## 10. Next steps

1. Decide whether to re-lock 534 after the fix (the idle release is now safe).
2. Test the fast PS reconnect on an idle link with a known data-stall scenario; measure recovery vs reboot. The tool is live
   (`POST /ps-reconnect`) and mocked; a live idle-link test is the remaining evidence.
3. Test the NR band lock (n78) on an idle link; keep the cell lock as the primary pin.
4. Read the current web-only settings (connection mode, roaming, bearer, PS restart, power save, MTU/MSS, thermal) and record
   them before changing anything.
5. Add DNS latency to the keeper's report; keep the existing TCP p95/jitter as the primary quality metric.
6. Keep the watchdog off; the keeper detects it and logs `watchdog enabled/disabled`.
