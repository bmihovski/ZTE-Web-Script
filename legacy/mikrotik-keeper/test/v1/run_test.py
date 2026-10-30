import subprocess, time, os, sys, shutil, importlib.util
sys.argv = ["x", "18099"]; spec = importlib.util.spec_from_file_location("m", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
data = "/tmp/zte-keeper-test-data"; shutil.rmtree(data, ignore_errors=True)
# timeline: 4 s: switch to 127 with SINR 6; 6 s: rx1 dropout; 7 s: back; 9 s: no 5G (LTE-NSA); 11 s: back to 534
m.script += [(4, {"nr5g_pci": "7f", "Z5g_SINR": "6.0"}), (6, {"5g_rx1_rsrp": "-140"}), (7, {"5g_rx1_rsrp": "-88"}),
             (9, {"network_type": "LTE-NSA", "nr5g_pci": "", "Z5g_SINR": ""}), (11, {"network_type": "ENDC", "nr5g_pci": "216", "Z5g_SINR": "9.0"})]
m.reboot_at(13, ppp_down=3)   # real ZTE: web + ppp gone for ~1 min while booting
env = dict(os.environ, ZTE_HOST="127.0.0.1:18099", ZTE_PASS_HASH=m.S("secret").lower(), INTERVAL="1", SAMPLE_EVERY="5", DATA=data)
p = subprocess.Popen(["dash", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "keeper", "keeper.sh")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(20); m.st["boot"] = time.time() - 70     # pretend 70 s since the reboot so the keeper re-applies
time.sleep(4); p.terminate(); out = p.communicate()[0]
print(out); print("SETS", m.st["sets"]); print("logins", m.st["logins"], "bad_ad", m.st["bad_ad"], "no_referer", m.st["no_referer"])
print(open(data + "/switches.csv").read()); print(open(data + "/samples.csv").read())
