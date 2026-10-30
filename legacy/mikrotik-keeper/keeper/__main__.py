"""Entry point: `python3 -m keeper` - checks DEVICE, starts the probe / SQM / watchdog threads, serves API + ACS on PORT."""
import os, signal, sys, threading
from http.server import ThreadingHTTPServer

from keeper import VERSION, acs, alert, api, brain, common, devices, selftest, sqm
from keeper.common import DATA, log


def main():
    """Start the keeper (blocks). Exit code 2 for an unknown DEVICE."""
    os.makedirs(DATA, exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))      # PID 1 in the container: no default SIGTERM handling
    try:
        dev = devices.current()
    except KeyError as e:
        log("%s - stopping" % e.args[0]); sys.exit(2)
    log("start (v2 %s): DEVICE=%s, ACS + API on :%d, inform every %ds, %s, actions %s"
        % (VERSION, common.DEVICE, acs.PORT, acs.INTERVAL, dev.start_note(), brain.AUTO))
    alert.send("%s keeper started (v %s) - after a router reboot / power cut / deploy" % (dev.NAME, VERSION), "start")
    brain.start(DATA)
    if brain.TARGETS: sqm.start(brain.lat_buf, log)          # SQM steers by the probe: no probe (LAT_TARGETS=off), no SQM
    selftest.start()          # daily SELFTEST_AT scenarios -> Telegram
    dev.on_start()
    threading.Thread(target=acs.watchdog, daemon=True).start()
    ThreadingHTTPServer.request_queue_size = 64     # Python's default 5 overflowed around reboots ("possible SYN flooding on tcp port 7557")
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer(("0.0.0.0", acs.PORT), api.H).serve_forever()


if __name__ == "__main__":
    main()
