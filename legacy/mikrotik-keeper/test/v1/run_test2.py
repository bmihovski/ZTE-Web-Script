import subprocess, time, os, sys, importlib.util
sys.argv = ["x", "18098"]; spec = importlib.util.spec_from_file_location("m", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
env = dict(os.environ, ZTE_HOST="127.0.0.1:18098", ZTE_PASS_HASH="0000", INTERVAL="1", DATA="/tmp/zte-keeper-test-data2")
p = subprocess.Popen(["dash", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "keeper", "keeper.sh")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(6); p.terminate(); print(p.communicate()[0]); print("login attempts in 6 s:", m.st["logins"], "sets:", m.st["sets"])
