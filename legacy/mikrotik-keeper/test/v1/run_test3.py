# keeper vs. browser: one ZTE session at a time
import subprocess, time, os, sys, importlib.util
sh = sys.argv[1] if len(sys.argv) > 1 else "dash"   # e.g. "busybox sh"
here = os.path.dirname(os.path.abspath(__file__))
sys.argv = ["x", "18099"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import shutil; shutil.rmtree("/tmp/zte-keeper-test-data3", ignore_errors=True)
env = dict(os.environ, ZTE_HOST="127.0.0.1:18099", ZTE_PASS_HASH=m.S(m.PW), INTERVAL="1", YIELD="6", DATA="/tmp/zte-keeper-test-data3")
m.browser_login_at(3)          # user logs in on the web page after the keeper applied
m.reboot_at(5, ppp_down=2)     # ZTE reboots while the keeper is pausing -> it must not wait the pause out
p = subprocess.Popen(sh.split() + [ os.path.join(here, "..", "..", "keeper", "keeper.sh")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(4.5); logins_mid = m.st["logins"]; browser_ok = "browser" in m.st["sessions"]
time.sleep(5.5); p.terminate(); out = p.communicate()[0]; print(out)
applies = [s for s in m.st["sets"] if s[1] == "WAN_ANT_SWITCH_SET"]
assert logins_mid == 1, logins_mid                      # did not kick the browser
assert browser_ok
assert "pausing 6s" in out
assert len(applies) == 2, m.st["sets"]                  # applied at start and again after the reboot
assert applies[1][0] < 9, applies                       # well before the 6 s pause (from ~3 s) would have ended
print("OK run_test3", sh)
