# outage reboots need the modem's OWN evidence of a fault, with a backoff - an outage without it is the provider's: wait.
# Running modem (TR-069 informs go on): decide() reads USIM.Status / ConnectionStatus / NetworkInUse from the samples.
# Silent modem after a boot without data (TR-069 never starts - 2026-11-13): the watchdog asks the LAN web page.
import datetime, importlib.util, os, shutil, sys, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18191"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-outage"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", LOCK_VIA="web", DATA=DATA, ZTE_HOST="127.0.0.1:18191", ZTE_PASS_HASH=m.S(m.PW), AUTO="on")
sys.path.insert(0, ROOT)
from keeper import acs, brain, common, guard, store
def last_ev(): return store.rows(DATA, "events")[-1]
from keeper.devices import zte as z
ev = []; log = ev.append

# --- classification ---
ok = {"sim": "Valid", "conn": "ppp_connected", "net": "Yettel BG", "lte_cell": "3350/340", "lte_rsrp": "-92"}
assert brain.device_fault(brain.row_health(ok)) is None                                                 # healthy
assert brain.device_fault(brain.row_health(dict(ok, sim="Error"))) == "SIM / modem not ready"
assert brain.device_fault(brain.row_health(dict(ok, conn="ppp_disconnected"))) == "registered with signal but no data connection"
assert brain.device_fault(brain.row_health(dict(ok, conn="ppp_connecting"))) == "registered with signal but no data connection"
nocell = dict(ok, lte_cell="", net="", lte_rsrp="", conn="ppp_disconnected")
assert brain.device_fault(brain.row_health(nocell)) is None                                             # no cells: the provider's
assert brain.device_fault(brain.row_health({})) is None                                                 # old samples / unknown: no evidence
assert brain.conn_up("ppp_disconnected") is False and brain.conn_up("ipv4_ipv6_connected") and brain.conn_up("") is None

# --- backoff: 15, 30, 60, 120, 240 min; 6 a day; an hour of working internet resets the streak ---
T = 1_800_000_000; brain.outage["times"] = []
assert brain.outage_gate(T) is None
brain.outage["times"] = [T]; assert "backoff: 15 min" in brain.outage_gate(T + 14 * 60) and brain.outage_gate(T + 15 * 60) is None
brain.outage["times"] = [T, T + 900]; assert "backoff: 30 min" in brain.outage_gate(T + 900 + 29 * 60) and brain.outage_gate(T + 900 + 1800) is None
brain.outage["times"] = [T + i for i in range(5)]; assert "backoff: 240 min" in brain.outage_gate(T + 239 * 60)    # capped at the last step
brain.outage["times"] = [T + i for i in range(6)]; assert "6 outage reboots in 24 h" in brain.outage_gate(T + 86000)
brain.lat_buf.clear(); now = time.time()
for i in range(60): brain.lat_buf.append((now - 120 + 2 * i, 20.0, "1.1.1.1"))
brain.outage["times"] = [now - 99]; brain.note_health(now); assert brain.outage["times"]                    # healthy, not an hour yet
brain.outage["healthy_since"] = now - 3601; brain.note_health(now); assert brain.outage["times"] == []      # an hour healthy: reset
brain.lat_buf.clear(); brain.note_health(now); assert brain.outage["healthy_since"] is None                 # down again: the hour restarts

# --- decide(): running modem, no service ---
def row(i, **kw):
    t = datetime.datetime.fromtimestamp(time.time() - (60 - i) * 60).strftime("%Y-%m-%d %H:%M:%S")
    r = dict(ok, time=t, uptime_s="3600", nr_cell="no5G", down_bps="0", up_bps="0", lte_ca="", lat_p95="", call_ok="0"); r.update(kw); return r
brain.HIST.clear(); brain.ACTIONS.clear(); brain.ctx.update(pending=None); brain.ctx["last_hint"].clear(); brain.outage["times"] = []
for i in range(6): brain.add(row(i, **{k: v for k, v in nocell.items()}))
assert brain.decide(row(6, **nocell), log, time.time()) is None and ev[-1]["event"] == "reboot held"          # no cells, SIM ok -> provider
assert "no device evidence - the provider's outage?" in ev[-1]["to"] and brain.ctx["pending"] is None, ev[-1]
n = len(ev); assert brain.decide(row(6, **dict(nocell, lte_rsrp="-140")), log, time.time() + 60) is None and len(ev) == n   # no repeat (30 min)
brain.HIST.clear(); brain.ctx["last_hint"].clear()
for i in range(6): brain.add(row(i, **dict(nocell, sim="Error")))
r = brain.decide(row(6, **dict(nocell, sim="Error")), log, time.time())
assert r and "no mobile service" in r and "SIM / modem not ready" in r and brain.ctx["pending"] == "reboot" and ev[-1]["event"] == "reboot queued", ev[-1]
assert len(brain.outage["times"]) == 1
brain.ctx.update(pending=None); brain.ctx["last_hint"].clear()                                             # 5 min later: still down -> backoff
assert brain.decide(row(6, **dict(nocell, sim="Error")), log, time.time() + 300) is None and "backoff: 15 min" in ev[-1]["to"], ev[-1]
# registered, signal, but no data for DATA_DOWN_MIN -> device (stuck data session)
brain.HIST.clear(); brain.ctx["last_hint"].clear(); brain.outage["times"] = []
for i in range(10): brain.add(row(i, conn="ppp_disconnected"))
r = brain.decide(row(10, conn="ppp_disconnected"), log, time.time())
assert r and "no data connection for 10 min" in r and "registered with signal" in r, (r, ev[-1])
brain.ctx.update(pending=None)

# --- silent modem (no informs after a boot without data): the LAN web page decides ---
def offline(n=60):
    brain.lat_buf.clear(); t = time.time()
    for i in range(n): brain.lat_buf.append((t - 2 * (n - i), None, "1.1.1.1"))
common.state({"lte_lock": None}); brain.outage.update(times=[], asked=0); brain.ctx["last_hint"].clear(); offline()
h = z.offline_health(); assert h["sim"] and h["registered"] and h["signal"] and h["data"] and "modem_init_complete" in h["detail"], h   # parsed from the page
calls = []; reboot = lambda: calls.append(1)
assert brain.offline_decide(5 * 60, z.offline_health, reboot) is None                                       # < 10 min quiet
offline(); m.script.append((0, {"network_type": "No Service", "lte_rsrp": "", "Z5g_rsrp": ""})); m.st["ppp_up_at"] = time.time() + 9999
assert brain.offline_decide(11 * 60, z.offline_health, reboot) == "held" and not calls                        # no service, SIM ok: provider
assert last_ev()["event"] == "reboot held" and "provider's outage?" in last_ev()["to"] and "No Service" in last_ev()["to"], last_ev()
assert brain.offline_decide(11 * 60, z.offline_health, reboot) is None                                        # asks only every 5 min
m.script.clear(); m.script.append((0, {"modem_main_state": "modem_sim_undetected"})); brain.outage["asked"] = 0
assert brain.offline_decide(11 * 60, z.offline_health, reboot) == "reboot" and calls == [1]                   # SIM fault: device
assert brain.outage["times"] and brain.ACTIONS[-1][3] == "on" and last_ev()["event"] == "reboot queued" and "SIM / modem not ready" in last_ev()["to"], last_ev()
brain.outage["asked"] = 0; assert brain.offline_decide(11 * 60, z.offline_health, reboot) == "held" and calls == [1]   # backoff 15 min
m.script.clear(); brain.outage.update(times=[], asked=0)                                                      # registered + signal, ppp down
assert brain.offline_decide(11 * 60, z.offline_health, reboot) == "reboot" and calls == [1, 1]
brain.outage.update(times=[], asked=0); brain.ctx["last_hint"].clear()
assert brain.offline_decide(11 * 60, lambda: None, reboot) == "held" and "web page does not answer" in last_ev()["to"], last_ev()
m.st["ppp_up_at"] = 0; brain.outage.update(times=[], asked=0)
assert brain.offline_decide(11 * 60, z.offline_health, reboot) == "held" and calls == [1, 1]                  # all healthy on the modem: provider/router side
brain.lat_buf.clear(); brain.outage["asked"] = 0
for i in range(60): brain.lat_buf.append((time.time() - 120 + 2 * i, 20.0, "1.1.1.1"))
assert brain.offline_decide(11 * 60, z.offline_health, reboot) is None                                        # internet works: not offline
# the real web reboot posts REBOOT_DEVICE
m.st["sets"].clear(); z.offline_reboot(); assert [s[1] for s in m.st["sets"]] == ["REBOOT_DEVICE"], m.st["sets"]
print("OK test_outage")
