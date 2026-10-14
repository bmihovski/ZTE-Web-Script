#!/usr/bin/env python3
"""
Router Helper - one desktop app (macOS / Windows / Linux, same webview + cookie/localStorage store) with a window per router:
  ZTE MC889      -> zte-script-legacy.js injected on every page load
  MikroTik ATL5G -> mikrotik-script.js injected into WebFig (RouterOS REST API)

Run:   macOS / Windows:  uv run --python 3.13 --with pywebview zte_app.py [--zte | --mikrotik]
       Linux:            uv run --python 3.13 --with "pywebview[qt]" zte_app.py [--zte | --mikrotik]
       (Windows needs the WebView2 runtime - built into Windows 10/11; Linux uses Qt WebEngine from pip)
       (no flag: start window to pick ZTE or MikroTik and edit its address; a flag opens that router directly)
       (package is "pywebview", not "webview"; it is a library, so not pipx)
Build: macOS only: pip3 install py2app && python3 setup.py py2app   ->  dist/ZTE Helper.app
Data:  macOS ~/Library/Application Support/ZTE Helper, Windows %APPDATA%\\ZTE Helper, Linux ~/.config/zte-helper
Routers: <data>/routers.json  {"zte": "http://192.168.6.3", "mikrotik": "http://192.168.6.6"}
       (created with these defaults on first start; env ZTE_ROUTER / MT_ROUTER override it for one run;
        plain http: WKWebView rejects the routers' self-signed https certs)
Debug: add --debug -> every REST call logged, Web Inspector enabled (right-click > Inspect Element)
Log:   terminal + macOS ~/Library/Logs/ZTE Helper.log, elsewhere <data>/zte-helper.log  (page scripts log via window.pywebview.api.log)
Prompts: the webview's own prompt() (Windows, Linux, macOS with pywebview >= 6); old macOS pywebview without it gets a
       sync bridge (osascript, WebKit aborts it after ~10 s). Passwords on macOS: hidden-answer dialog via api.ask.
"""
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAC = sys.platform == "darwin"


def app_dirs(platform=sys.platform, env=os.environ, home=Path.home()):
    """(data dir: routers.json + webview storage, log file) per OS"""
    if platform == "darwin":
        return home / "Library/Application Support/ZTE Helper", home / "Library/Logs/ZTE Helper.log"
    if platform == "win32":
        d = Path(env.get("APPDATA") or home / "AppData/Roaming") / "ZTE Helper"
    else:
        d = Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "zte-helper"
    return d, d / "zte-helper.log"


STORAGE, LOG_FILE = app_dirs()
CONFIG_FILE = STORAGE / "routers.json"
DEFAULTS = {"zte": "http://192.168.6.3", "mikrotik": "http://192.168.6.6"}
ENV = {"zte": "ZTE_ROUTER", "mikrotik": "MT_ROUTER"}


def origin(url):
    """'192.168.6.6' / 'http://192.168.6.6/webfig/' -> 'http://192.168.6.6' (scheme://host[:port], the injection/CORS origin)"""
    url = url.strip()
    if "://" not in url:
        url = "http://" + url
    scheme, rest = url.split("://", 1)
    return scheme.lower() + "://" + rest.split("/", 1)[0]


def load_config(path=CONFIG_FILE, env=os.environ):
    """defaults < routers.json < env vars"""
    cfg = dict(DEFAULTS)
    try:
        cfg.update({k: v for k, v in json.loads(path.read_text()).items() if k in DEFAULTS and v})
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        print("ignoring unreadable %s: %s" % (path, e), file=sys.stderr)
    cfg.update({k: env[v] for k, v in ENV.items() if env.get(v)})
    return {k: origin(v) for k, v in cfg.items()}


CONFIG = load_config()
ROUTER = CONFIG["zte"]
MT_ROUTER = CONFIG["mikrotik"]
ORIGINS = {ROUTER, MT_ROUTER}  # + any address opened from the launcher
HERE = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(16)
DEBUG = "--debug" in sys.argv
log = logging.getLogger("router-helper")


def setup_logging():
    handlers = [logging.StreamHandler()]
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(LOG_FILE))
    except OSError:
        pass
    logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class JsApi:
    """Exposed to the page as window.pywebview.api (calls return Promises). One instance per window."""

    def __init__(self):
        self._dialogs = set()  # open dialog processes of this window's current page

    def log(self, msg):
        log.info("js %s", str(msg)[:2000])

    def ask(self, msg, default=""):
        # async replacement for window.prompt: WebKit aborts the sync-XHR prompt shim after ~10 s ("The operation timed out")
        return ask_logged(str(msg), str(default or ""), "js_api", self._dialogs)

    def rest(self, origin, method, path, body=None, auth=""):
        """RouterOS REST call made by Python for the injected page (macOS app). WKWebView turned out unreliable with
        explicit Authorization headers (router saw no credentials -> 401, nothing in the router log). Only /rest/ on
        router origins this app opened. Returns {status, text}; status 0 = network error."""
        if origin not in ORIGINS or not str(path).startswith("/rest/") or method not in ("GET", "POST", "PATCH", "PUT", "DELETE"):
            return {"status": 0, "text": "refused"}
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = str(auth)
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(origin + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # cell-monitor alone takes 5 s
                return {"status": r.status, "text": r.read().decode("utf-8", "replace")}
        except urllib.error.HTTPError as e:
            return {"status": e.code, "text": e.read().decode("utf-8", "replace")}
        except Exception as e:
            return {"status": 0, "text": "%s: %s" % (type(e).__name__, e)}

    def _close_dialogs(self):
        """New page in this window: dialogs the old page opened can't deliver their answer any more - close them."""
        for p in list(self._dialogs):
            p.terminate()
        return len(self._dialogs)


def probe(url):
    """Can this process reach the router at all? (timeouts here = network / macOS Local Network permission, not the script)"""
    t = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            res = r.status
    except urllib.error.HTTPError as e:
        res = e.code  # 401 from /rest is fine: reachable, needs login
    except Exception as e:
        res = "%s: %s" % (type(e).__name__, e)
    log.info("probe %s -> %s in %.0f ms", url, res, (time.monotonic() - t) * 1000)


def find_script(name="zte-script-legacy.js"):
    # app/ next to the script in the repo, or Contents/Resources inside the .app bundle
    for p in (HERE / name, HERE.parent / name):
        if p.exists():
            return p.read_text(encoding="utf-8")
    sys.exit(name + " not found next to zte_app.py or in its parent folder")


def ask_logged(msg, default, origin, dialogs=None):
    t = time.monotonic()
    value = ask(msg, default, dialogs)
    log.info("prompt %r from %s -> %s after %.1f s", msg, origin,
             "cancelled" if value is None else "answered", time.monotonic() - t)  # never log the answer (passwords)
    return value


def ask(msg, default, dialogs=None):
    """Native macOS input dialog. Returns None on Cancel (same as JS prompt) or when closed by _close_dialogs."""
    hidden = " with hidden answer" if "password" in msg.lower() else ""
    lines = [
        "on run argv",
        "activate",
        'set d to display dialog (item 1 of argv) default answer (item 2 of argv) with title "Router Helper"' + hidden,
        "return text returned of d",
        "end run",
    ]
    return run_dialog(["osascript"] + [a for line in lines for a in ("-e", line)] + [msg, default], dialogs)


def run_dialog(cmd, dialogs=None):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if dialogs is not None:
        dialogs.add(p)
    try:
        out = p.communicate()[0]
    finally:
        if dialogs is not None:
            dialogs.discard(p)
    return out.rstrip("\n") if p.returncode == 0 else None


class PromptHandler(BaseHTTPRequestHandler):
    """WKWebView (pywebview) has no window.prompt(); the injected shim calls this synchronously."""

    def do_POST(self):
        origin = self.headers.get("Origin")
        if self.path != "/prompt?t=" + TOKEN or origin not in ORIGINS:
            log.warning("prompt request rejected: path=%s origin=%s", self.path.split("?")[0], origin)
            return self.send_error(403)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        value = ask_logged(str(body.get("msg", "")), str(body.get("def", "")), origin)
        out = json.dumps({"value": value}).encode()
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", origin)
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


def injector(window, origin, inject, api=None):
    # zero-arg closure: pywebview passes extra args to handlers that declare parameters
    def on_loaded():
        url = window.get_current_url() or ""
        closed = api._close_dialogs() if api else 0  # WebFig reloads itself while its login dialog is open
        if closed:
            log.info("%s: new page - closed %d dialog(s) of the previous page", window.title, closed)
        if url.startswith(origin):
            log.info("%s loaded %s -> injecting script", window.title, url)
            window.run_js(inject)  # run as-is (global scope), the scripts' onclick handlers need globals
        else:
            log.info("%s loaded %s -> not the router origin, no injection", window.title, url)
    return on_loaded


ROUTERS = {  # kind: (title, start path, script, probe path)
    "zte": ("ZTE", "/index.html", "zte-script-legacy.js", "/"),
    "mikrotik": ("MikroTik", "/webfig/", "mikrotik-script.js", "/rest/system/resource"),
}
VALID_ORIGIN = re.compile(r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?")

LAUNCHER_HTML = """<!doctype html><meta charset=utf-8><title>Router Helper</title>
<style>
body { font: 14px -apple-system, sans-serif; margin: 22px 26px; color: #222; }
h2 { margin: 0 0 6px; } label { display: block; margin: 16px 0 5px; font-weight: 600; }
input { font-size: 14px; padding: 5px 8px; width: 230px; } button { font-size: 14px; padding: 5px 16px; }
#err { color: #c00; margin-top: 12px; min-height: 1.2em; } .note { color: #777; font-size: 12px; }
</style>
<h2>Router Helper</h2>
<div class=note>Pick the router, check its address, Open. Each router gets its own window; this one can stay open.</div>
<label for=zte>ZTE MC889</label><input id=zte placeholder="192.168.6.3"> <button onclick="go('zte')">Open</button>
<label for=mikrotik>MikroTik (WebFig)</label><input id=mikrotik placeholder="192.168.6.6"> <button onclick="go('mikrotik')">Open</button>
<div id=err></div>
<div class=note>IP or host name, optional :port or https://. Saved for next time.</div>
<script>
window.addEventListener("pywebviewready", async function () {
    var c = await pywebview.api.config();
    for (var k in c) document.getElementById(k).value = c[k].replace(/^http:\\/\\//, "");
});
async function go(k) { document.getElementById("err").textContent = (await pywebview.api.open(k, document.getElementById(k).value)) || ""; }
document.addEventListener("keydown", function (e) { if (e.key == "Enter" && e.target.id) go(e.target.id); });
</script>"""


def save_config(cfg, path=CONFIG_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n")


class Launcher(JsApi):
    """Start window API: router addresses from routers.json, Open = validate, save, open that router's window."""

    def __init__(self, opener, config_file=CONFIG_FILE):
        super().__init__()
        self._opener, self._file = opener, config_file  # underscore: not exposed to the page

    def config(self):
        return load_config(self._file)

    def open(self, kind, url):
        if kind not in ROUTERS:
            return "unknown router %r" % kind
        o = origin(str(url or ""))
        if not VALID_ORIGIN.fullmatch(o):
            return "not a router address: %r" % url
        cfg = load_config(self._file, {})  # env overrides are for one run, don't persist them
        cfg[kind] = o
        save_config(cfg, self._file)
        log.info("launcher: opening %s at %s (saved to %s)", kind, o, self._file)
        self._opener(kind, o)
        return ""


AMD_CHECK = "typeof define === 'function' && define.amd"


def patch_pywebview_amd(util):
    """
    pywebview (6.x) injects js/lib/dom_json.js into every page. It is a UMD bundle: on a page that uses RequireJS -
    the ZTE web UI does - it calls an anonymous define(), RequireJS fails with "Mismatched anonymous define() module"
    and the ZTE UI never starts (blank page: logo and an empty language box). Force its plain browser-global branch.
    """
    orig = util.load_js_files

    def load_js_files(window, platform):
        js, finish = orig(window, platform)
        if AMD_CHECK not in js:
            log.warning("pywebview JS changed - AMD patch not applied (ZTE UI may stay blank)")
        return js.replace(AMD_CHECK, "false"), finish

    util.load_js_files = load_js_files


def native_prompt():
    """True when the webview shows window.prompt itself (pywebview's cocoa delegate gained the text-input panel in 6.x)."""
    if not MAC:
        return True
    try:
        from webview.platforms import cocoa
        return hasattr(cocoa.BrowserView.BrowserDelegate,
                       "webView_runJavaScriptTextInputPanelWithPrompt_defaultText_initiatedByFrame_completionHandler_")
    except Exception:
        return False


def pywebview_version():
    try:
        from importlib.metadata import version
        return version("pywebview")
    except Exception:
        return "?"


def main():
    import webview  # imported here so --selftest runs without pywebview
    import webview.util

    patch_pywebview_amd(webview.util)  # before any window: the ZTE UI breaks on pywebview's injected UMD module

    setup_logging()
    if not CONFIG_FILE.exists():
        save_config(DEFAULTS)
        log.info("created %s", CONFIG_FILE)
    log.info("start: %s, python %s, pywebview %s, debug=%s, routers %s, log file %s", sys.platform, sys.version.split()[0],
             pywebview_version(), DEBUG, CONFIG, LOG_FILE)
    # macOS: bridge for the missing window.prompt; Windows/Linux webviews have a working prompt() - use it as is
    # window.prompt: Windows/Linux webviews and pywebview >= 6 on macOS show it natively (no time limit) - the sync-XHR
    # bridge (WebKit aborts it after ~10 s) only for an older macOS pywebview without it.
    # app_ask: hidden-answer native dialogs via api.ask (passwords) - osascript, so macOS only.
    shim = prompt_shim(start_prompt_server().server_address[1]) if MAC and not native_prompt() else ""
    shim += "window.app_ask = true;\n" if MAC else ""
    log.info("prompts: %s", "app bridge (old pywebview)" if "XMLHttpRequest" in shim else "native window.prompt")
    shim += "window.mt_debug = true;\n" if DEBUG else ""

    def open_router(kind, o):
        title, path, script, probe_path = ROUTERS[kind]
        ORIGINS.add(o)  # the prompt bridge accepts this router's page
        threading.Thread(target=probe, args=(o + probe_path,), daemon=True).start()
        api = JsApi()
        window = webview.create_window("%s Helper - %s" % (title, o.split("://", 1)[1]), o + path, width=1280, height=900, js_api=api)
        window.events.loaded += injector(window, o, shim + find_script(script) + "\n;true", api)

    picked = [k for k in ROUTERS if "--" + k in sys.argv]
    for k in picked:  # --zte / --mikrotik: straight to that router (address from routers.json / env)
        open_router(k, CONFIG[k])
    if not picked:
        webview.create_window("Router Helper", html=LAUNCHER_HTML, width=480, height=340, js_api=Launcher(open_router))
    webview.settings["ALLOW_DOWNLOADS"] = True  # CSV export from the alignment tools
    # persistent cookies -> "Enable Automatic Login" survives restarts
    webview.start(debug=DEBUG, private_mode=False, storage_path=str(STORAGE))


def selftest():
    """Offline check of the prompt bridge (no router, no GUI)."""
    import urllib.request

    global ask
    ask = lambda msg, default, dialogs=None: None if msg == "cancel" else msg.upper() + "|" + default
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
    assert post(ok, {"msg": "mt", "def": "admin"}, origin=MT_ROUTER) == (200, MT_ROUTER, {"value": "MT|admin"})
    assert "window.prompt" in prompt_shim(port) and TOKEN in prompt_shim(port)
    assert "function perform_login" in find_script()
    assert "function mt_init" in find_script("mikrotik-script.js")
    JsApi().log("selftest")
    assert origin("192.168.1.1") == "http://192.168.1.1" and origin("HTTP://10.0.0.1:8080/webfig/") == "http://10.0.0.1:8080"
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "routers.json"
        assert load_config(f, {}) == DEFAULTS                                   # no file -> defaults
        f.write_text('{"mikrotik": "10.0.0.2/", "junk": "x"}')
        assert load_config(f, {}) == {"zte": DEFAULTS["zte"], "mikrotik": "http://10.0.0.2"}
        assert load_config(f, {"MT_ROUTER": "10.0.0.3"})["mikrotik"] == "http://10.0.0.3"   # env wins
        f.write_text("{broken")
        assert load_config(f, {}) == DEFAULTS                                   # unreadable -> defaults
    assert JsApi().ask("pci", "1") == "PCI|1" and JsApi().ask("cancel") is None
    api, res = JsApi(), []
    th = threading.Thread(target=lambda: res.append(run_dialog([sys.executable, "-c", "import time; time.sleep(30)"], api._dialogs)))
    th.start()
    while not api._dialogs:
        time.sleep(0.01)
    assert api._close_dialogs() == 1
    th.join(5)
    assert res == [None] and not api._dialogs and not th.is_alive()        # stale dialog closed, answers "cancelled"
    assert run_dialog([sys.executable, "-c", "print('admin')"]) == "admin"
    class FakeRouter(BaseHTTPRequestHandler):  # RouterOS REST: basic auth admin:pw, echoes method + body
        def _go(self):
            ok = self.headers.get("Authorization") == "Basic YWRtaW46cHc="
            n = int(self.headers.get("Content-Length") or 0)
            out = json.dumps({"m": self.command, "body": self.rfile.read(n).decode()} if ok else {"error": 401}).encode()
            self.send_response(200 if ok else 401)
            self.end_headers()
            self.wfile.write(out)
        do_GET = do_POST = do_PATCH = _go

        def log_message(self, *a):
            pass

    fr = ThreadingHTTPServer(("127.0.0.1", 0), FakeRouter)
    threading.Thread(target=fr.serve_forever, daemon=True).start()
    fo = "http://127.0.0.1:%d" % fr.server_address[1]
    ORIGINS.add(fo)
    r = JsApi().rest(fo, "POST", "/rest/interface/lte/monitor", {"numbers": "lte1"}, "Basic YWRtaW46cHc=")
    assert r["status"] == 200 and json.loads(r["text"]) == {"m": "POST", "body": '{"numbers": "lte1"}'}
    assert JsApi().rest(fo, "PATCH", "/rest/interface/lte/*3", {"band": ""}, "Basic YWRtaW46cHc=")["status"] == 200
    assert JsApi().rest(fo, "GET", "/rest/interface/lte", None, "Basic YWRtaW46d3Jvbmc=")["status"] == 401
    assert JsApi().rest(fo, "GET", "/rest/interface/lte")["status"] == 401                      # no login yet
    assert JsApi().rest(fo, "GET", "/webfig/", None, "x")["text"] == "refused"                 # REST only
    assert JsApi().rest("http://evil.example", "GET", "/rest/x")["text"] == "refused"           # router origins only
    assert JsApi().rest(fo, "HEAD", "/rest/x")["text"] == "refused"
    fr.shutdown()
    ORIGINS.discard(fo)
    with tempfile.TemporaryDirectory() as d:
        f, opened = Path(d) / "routers.json", []
        la = Launcher(lambda k, o: opened.append((k, o)), f)
        assert la.config() == load_config(f) and la.open("mikrotik", " 10.0.0.9 ") == "" and opened == [("mikrotik", "http://10.0.0.9")]
        assert json.loads(f.read_text()) == {"zte": DEFAULTS["zte"], "mikrotik": "http://10.0.0.9"}   # saved for next time
        assert la.open("zte", "https://router.lan:8443/x") == "" and opened[-1] == ("zte", "https://router.lan:8443")
        for bad in ("", "http://", "10.0.0.1 ; rm", "javascript:alert(1)", "http://a b"):
            assert la.open("mikrotik", bad).startswith("not a router address"), bad
        assert la.open("cisco", "10.0.0.1").startswith("unknown") and len(opened) == 2
    class FakeUtil:
        @staticmethod
        def load_js_files(window, platform):
            return "x;if (" + AMD_CHECK + ") { define(f) } else { g() }", "finish"

    patch_pywebview_amd(FakeUtil)
    js, fin = FakeUtil.load_js_files(None, "cocoa")
    assert AMD_CHECK not in js and "if (false)" in js and fin == "finish"
    h = Path("/h")
    assert app_dirs("darwin", {}, h)[0] == h / "Library/Application Support/ZTE Helper"
    assert app_dirs("win32", {"APPDATA": "/h/Roaming"}, h) == (Path("/h/Roaming/ZTE Helper"), Path("/h/Roaming/ZTE Helper/zte-helper.log"))
    assert app_dirs("linux", {}, h)[0] == h / ".config/zte-helper" and app_dirs("linux", {"XDG_CONFIG_HOME": "/x"}, h)[0] == Path("/x/zte-helper")
    assert "pywebviewready" in LAUNCHER_HTML and "open(k" in LAUNCHER_HTML
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
