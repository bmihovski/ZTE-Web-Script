#!/usr/bin/env python3
"""
ZTE MC889 Helper - dedicated macOS window for the router web UI with
zte-script-legacy.js injected on every page load (no browser needed).

Run:   pip3 install pywebview && python3 zte_app.py
Build: pip3 install py2app && python3 setup.py py2app   ->  dist/ZTE Helper.app
Env:   ZTE_ROUTER=http://192.168.6.3  (plain http: WKWebView rejects the router's self-signed https cert)
"""
import json
import os
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROUTER = os.environ.get("ZTE_ROUTER", "http://192.168.6.3").rstrip("/")
HERE = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(16)


def find_script():
    # app/ next to the script in the repo, or Contents/Resources inside the .app bundle
    for p in (HERE / "zte-script-legacy.js", HERE.parent / "zte-script-legacy.js"):
        if p.exists():
            return p.read_text(encoding="utf-8")
    sys.exit("zte-script-legacy.js not found next to zte_app.py or in its parent folder")


def ask(msg, default):
    """Native macOS input dialog. Returns None on Cancel (same as JS prompt)."""
    hidden = " with hidden answer" if "password" in msg.lower() else ""
    lines = [
        "on run argv",
        "activate",
        'set d to display dialog (item 1 of argv) default answer (item 2 of argv) with title "ZTE Helper"' + hidden,
        "return text returned of d",
        "end run",
    ]
    cmd = ["osascript"] + [a for line in lines for a in ("-e", line)] + [msg, default]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


class PromptHandler(BaseHTTPRequestHandler):
    """WKWebView (pywebview) has no window.prompt(); the injected shim calls this synchronously."""

    def do_POST(self):
        if self.path != "/prompt?t=" + TOKEN or self.headers.get("Origin") != ROUTER:
            return self.send_error(403)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        out = json.dumps({"value": ask(str(body.get("msg", "")), str(body.get("def", "")))}).encode()
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", ROUTER)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


def start_prompt_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), PromptHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def prompt_shim(port):
    # text/plain POST = "simple" CORS request, no preflight; sync XHR blocks like the real prompt()
    return """
window.prompt = function (msg, def) {
    var x = new XMLHttpRequest();
    x.open("POST", "http://127.0.0.1:%d/prompt?t=%s", false);
    x.setRequestHeader("Content-Type", "text/plain");
    x.send(JSON.stringify({ msg: String(msg == null ? "" : msg), def: def == null ? "" : String(def) }));
    return JSON.parse(x.responseText).value;
};
""" % (port, TOKEN)


def main():
    import webview  # imported here so --selftest runs without pywebview

    script = find_script()
    srv = start_prompt_server()
    inject = prompt_shim(srv.server_address[1]) + script + "\n;true"

    window = webview.create_window("ZTE Helper", ROUTER + "/index.html", width=1280, height=900)

    def on_loaded():
        url = window.get_current_url() or ""
        if url.startswith(ROUTER):
            window.run_js(inject)  # run as-is (global scope), the script's onclick handlers need globals

    window.events.loaded += on_loaded
    # persistent cookies -> "Enable Automatic Login" survives restarts
    webview.start(private_mode=False, storage_path=str(Path.home() / "Library/Application Support/ZTE Helper"))


def selftest():
    """Offline check of the prompt bridge (no router, no GUI)."""
    import urllib.request

    global ask
    ask = lambda msg, default: None if msg == "cancel" else msg.upper() + "|" + default
    port = start_prompt_server().server_address[1]

    def post(path, payload, origin=ROUTER):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=json.dumps(payload).encode(),
                                     headers={"Origin": origin, "Content-Type": "text/plain"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.headers.get("Access-Control-Allow-Origin"), json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None, None

    ok = "/prompt?t=" + TOKEN
    assert post(ok, {"msg": "pci,earfcn", "def": "116,3350"}) == (200, ROUTER, {"value": "PCI,EARFCN|116,3350"})
    assert post(ok, {"msg": "cancel", "def": ""})[2] == {"value": None}
    assert post("/prompt?t=wrong", {"msg": "x"})[0] == 403
    assert post(ok, {"msg": "x"}, origin="http://evil.example")[0] == 403
    assert "window.prompt" in prompt_shim(port) and TOKEN in prompt_shim(port)
    assert "function perform_login" in find_script()
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
