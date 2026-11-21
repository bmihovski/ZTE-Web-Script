# web-only tools (2026-11-21): GET /zte/settings (audit) and POST /ps-reconnect (fast PS disconnect/connect, no CPE reboot)
import importlib.util, os, shutil, sys, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18195"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-web"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", DATA=DATA, ZTE_HOST="127.0.0.1:18195", ZTE_PASS_HASH=m.S(m.PW))
sys.path.insert(0, ROOT)
from keeper import common
from keeper.devices.zte import ps, settings

m.st["web_settings"].update(connectionMode="auto_dial", autoConnectWhenRoaming="off", roam_setting_option="off",
                            ps_no_service_restart_flag="1", auto_power_save="0", mtu="1500", tcp_mss="1460",
                            thermal_control_enable="1", thermal_led_enable="0", upnpEnabled="1", net_select_mode="auto_select",
                            net_select="WL_AND_5G", nr5g_band_lock="1,3,7,8,20,28,78")
s = settings.read(); assert "error" not in s, s
v = s["values"]
assert v["connection_mode"] == "auto_dial" and v["roaming"] is False and v["ps_restart"] is True and v["power_save"] is False, v
assert (v["mtu"], v["mss"]) == (1500, 1460) and v["thermal"] is True and v["upnp"] is True, v
assert v["net_select_mode"] == "auto_select" and v["net_select"] == "WL_AND_5G" and v["nr_band_lock"] == "1,3,7,8,20,28,78", v
w = s["warnings"]
assert any("PS no-service restart" in x for x in w) and any("UPnP" in x for x in w), w
assert not any("MTU" in x or "MSS" in x or "n78" in x for x in w), w            # 1500/1460 and n78 are in the list: no warning
assert settings.audit() == w
# the same session's watchdog read is stored too (disabled here)
assert common.state()["watchdog"]["values"]["enabled"] is False
# a setting that hurts: power save on -> warning
m.st["web_settings"]["auto_power_save"] = "1"; s = settings.read(); assert any("power save" in x for x in s["warnings"]), s["warnings"]
m.st["web_settings"]["auto_power_save"] = "0"
# fast PS reconnect: DISCONNECT -> ppp_disconnected -> CONNECT -> ppp_connected
m.st["sets"].clear(); r = ps.reconnect(timeout=10)
assert r["ok"] and r["down"] and r["up"], r
assert [x[1] for x in m.st["sets"] if x[1] in ("DISCONNECT_NETWORK", "CONNECT_NETWORK")] == ["DISCONNECT_NETWORK", "CONNECT_NETWORK"], m.st["sets"]
# a refused disconnect is an error, not a silent success
m.st["refuse"] = {"DISCONNECT_NETWORK"}; r = ps.reconnect(timeout=3); m.st["refuse"] = set()
assert not r["ok"] and "DISCONNECT_NETWORK" in r["error"], r
print("OK test_web")
