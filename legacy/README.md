# Router helpers (legacy)

Helper scripts that extend the web UI of two 5G routers, plus a small desktop app that opens them with the scripts
already loaded.

| File | What it is |
|------|------------|
| `zte-script-legacy.js` | ZTE MC889 helper: signal details, band / cell lock, neighbour cells, antenna alignment, cell evaluations |
| `mikrotik-script.js` | MikroTik ATL 5G (RouterOS v7, Quectel RG520F modem) helper: same tools, as a panel on top of WebFig |
| `app/zte_app.py` | "Router Helper" desktop app (macOS, Windows, Linux) - start window, one window per router, scripts injected |
| `app/setup.py` | macOS `.app` build (py2app) |
| `tests/` | offline checks for both scripts and the app - see [`tests/README.md`](tests/README.md) |

## Desktop app

### Start window

Run the app without options and the **Router Helper** start window opens:

```
Router Helper
ZTE MC889          [192.168.6.3    ] [Open]
MikroTik (WebFig)  [192.168.6.6    ] [Open]
```

Check or edit the address and click **Open** (or press Enter). The router opens in its own window with its script
loaded; the start window stays open, so you can open the other router too. Accepted: IP or host name, optional `:port`,
optional `https://`. The addresses are saved to `routers.json` for next time.

Options (terminal):

| Option | Effect |
|--------|--------|
| `--zte` / `--mikrotik` | skip the start window, open that router directly (saved address) |
| `--debug` | log every router request, enable the web inspector (right-click > Inspect) |
| `--selftest` | offline self-test, no window, no router |

Env `ZTE_ROUTER` / `MT_ROUTER` override the saved address for one run (e.g. `MT_ROUTER=10.0.0.2`).

### Where things are stored

| | Settings + browser storage (`routers.json`, logins, saved positions, cell results) | Log |
|--|--|--|
| macOS | `~/Library/Application Support/ZTE Helper/` | `~/Library/Logs/ZTE Helper.log` |
| Windows | `%APPDATA%\ZTE Helper\` | `%APPDATA%\ZTE Helper\zte-helper.log` |
| Linux | `~/.config/zte-helper/` (or `$XDG_CONFIG_HOME/zte-helper/`) | `~/.config/zte-helper/zte-helper.log` |

`routers.json` is created on first start: `{"zte": "http://192.168.6.3", "mikrotik": "http://192.168.6.6"}`.
Use plain `http://` where possible - the embedded webviews reject the routers' self-signed https certificates.

The app needs the [uv](https://docs.astral.sh/uv/) tool. Install it once:

- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows (PowerShell): `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`

The Python package is **`pywebview`** (not `webview`, which is an unrelated package that fails to build). It is a
library, so `pipx install` does not work - use `uv run` as below. Python 3.13 is pinned because newer versions may
lack ready-made builds of the macOS dependencies.

### macOS

Quick run from the terminal:

```bash
cd legacy/app
uv run --python 3.13 --with pywebview zte_app.py
```

Recommended for daily use - build a real app once (link mode: the app uses the files in this folder, so script edits
need only a quit and reopen, no rebuild):

```bash
cd legacy/app
brew install python@3.13
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/pip install pywebview py2app
.venv/bin/python setup.py py2app -A
cp -R "dist/ZTE Helper.app" /Applications/
```

- Start it from Launchpad / Spotlight / Dock. On first launch allow **Local Network** access when macOS asks
  (System Settings > Privacy & Security > Local Network) - without it the routers are unreachable.
- Rebuild only if you move the repo folder or the `.venv`.
- Standalone copy (e.g. for another Mac): `.venv/bin/python setup.py py2app` without `-A`; it bundles the scripts, so
  rebuild after every edit.

Handy terminal alias for development (`~/.zshrc`):

```bash
alias routers='uv run --python 3.13 --with pywebview ~/Privatespace/ZTE-Web-Script/legacy/app/zte_app.py'
routers --mikrotik --debug
```

### Windows

```powershell
cd legacy\app
uv run --python 3.13 --with pywebview zte_app.py
```

- Uses the Edge WebView2 runtime, built into Windows 10/11.
- If Windows Firewall asks about Python, allowing **private networks** is enough.
- For a shortcut, put the line above in a `.bat` file. There is no packaged `.exe` (py2app is macOS only).

### Linux

```bash
cd legacy/app
uv run --python 3.13 --with "pywebview[qt]" zte_app.py
```

- `[qt]` installs Qt WebEngine from pip - no GTK development packages needed.
- On minimal systems Qt may report a missing system library (often `libxcb-cursor0` or `libegl1`) - install the one
  it names with your package manager.

### Prompts (PCI / EARFCN input, logins)

- All platforms with pywebview 6+: the webview's own `prompt()` dialog, no time limit.
- macOS: router passwords (ZTE login, MikroTik REST login) use a separate dialog that hides what you type.
- Only an old macOS pywebview without native `prompt()` falls back to a bridge that WebKit aborts after about
  10 seconds (the log says `prompts: app bridge (old pywebview)`).

### ZTE automatic login

The ZTE script logs in by itself once the password is saved:

1. Open the ZTE router (app or browser) and log in once as usual.
2. In the helper links at the bottom click **Enable Automatic Login**, confirm, enter the router password.
   "Successfully saved password as hash!" = done.
3. From now on the script logs in automatically when the page opens. It stores only an SHA-256 hash of the password,
   in a cookie of that browser / of the app (the app keeps it across restarts). A wrong password removes the hash;
   to change it click **Enable Automatic Login** again.

## In a browser instead of the app (any OS)

### MikroTik script - Firefox / Chrome

Recommended: a userscript extension, the script then loads automatically on every WebFig page.

1. Install **Tampermonkey** or **Violentmonkey**.
2. Dashboard > new script > replace the template with the whole `mikrotik-script.js` (keep the `// ==UserScript==`
   header) > save.
3. Open `http://<router>/` (redirects to `/webfig/`), log in to WebFig - the panel appears top-right.
4. After editing the file, paste the new version into the extension again.

The header matches any WebFig page (`http://*/webfig/*`, `https://*/webfig/*`), so a new router address needs no change.

Quick test without an extension: open WebFig, open the console (Firefox `Cmd/Ctrl+Option/Shift+K`, Chrome `F12`),
in Firefox type `allow pasting` once, then paste the whole script and press Enter. Repeat after every reload.

Notes for the browser:

- Login: the browser may show its own username/password box on the first REST request - enter the RouterOS login
  there; otherwise the script prompts for it.
- Saved login, positions and cell results live in that browser only (export CSV to move them).
- The alignment tone starts only after clicking the Tone checkbox; keep the tab in front and the laptop awake -
  background tabs slow down timers.
- Logs go to the browser console (lines starting with `mt:`).

### ZTE script

Run it in the browser as before (console or userscript extension), or use the app.

## MikroTik panel - what it does

Talks only to the RouterOS REST API (`/rest`, HTTP basic auth, same origin as WebFig; in the macOS app the requests are made by
the app itself because WKWebView dropped the login header); nothing to enable on the router
(REST over plain http works on RouterOS 7.9+ with the `www` service). Changes apply live - **no reboot**.

- **Header:** login state (green dot + user + logout / red dot + login), **A− / A+** (panel size 10-22 px, default 14;
  width scales with it), **minimize / expand** (header only). Size and minimized state are remembered.
- **Signal:** LTE and 5G RSRP/RSRQ/SINR, primary and carrier-aggregation cells (band, width, EARFCN, PCI), every 1 s.
- **Network mode / bands:** tick boxes from the modem's supported list; none ticked = all bands. Applied with
  `/interface lte set` - the modem re-registers for a few seconds.
- **Operator:** manual network selection for local roaming - Automatic, A1 (284 01), Vivacom (284 03), Yettel (284 05)
  or any other MCC+MNC. Sets `/interface lte operator=` (Automatic = unset); the modem re-registers, no reboot. The
  list is `MT_OPERATORS` in the script.
- **Cell lock:** LTE `earfcn,pci[;earfcn,pci]` and 5G `pci,arfcn,scs,band` via `AT+QNWLOCK` (5G lock works in NSA and
  SA). Locks are **not persistent** - the modem forgets them on reboot / modem reset. Lock status refreshes every 10 s.
- **Neighbour cells:** 5 s `cell-monitor` scan, serving cells highlighted, `lock` / `+` links; auto-scan every 15 s.
  While a scan runs, the other readings pause (the router holds the modem for the 5 s).
- **Antenna alignment:** live table (3 s average, change since start, peak, 5 min sparkline) incl. RSRP per 5G rx path
  and LTE port, branch warning (> 6 dB spread), Fast update (0.5 s), tones on the computer speakers
  (5G + LTE SINR, rx0/rx1 balance, single metric; absolute / 2 / 4 semitones per dB), Reset start/peak. Data from
  `AT+QENG="servingcell";+QRSRP`. **Tone targets** are editable under the legend ("Tone targets"): the value at which
  the tone goes steady and the top of the beep-speed scale. MikroTik defaults (outdoor antenna): 5G+LTE mode 5G SINR 20 /
  LTE SINR 15 dB; single metric 5G SINR 25, LTE SINR 20, RSRQ -10 dB, RSRP -70 dBm. ZTE defaults: 15 / 6 and 20, 6, -10,
  -70. Saved per router script in that browser / the app; **defaults** link resets. While the modem searches for a cell (after a lock / band change) an orange
  "modem SEARCH" line is shown instead of values. The ZTE "quality 0-4" modes do not exist here.
- **Saved positions:** label + all values, best per column highlighted, CSV export.
- **Cell evaluations / lock experiments:** a single-cell lock from the panel (or "Measure LTE / 5G cell") waits ~5 s,
  waits for the modem to be on the target cell, then averages 60 s of signal + lte1 throughput. Target lost = the
  partial minute is dropped; gives up after 3 min. Rx/tx Mbit/s is the traffic flowing at the time - run a speed test
  during the minute to compare cells.

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `pipx install webview` fails to compile | wrong package - use `uv run --with pywebview` (see above) |
| App: ZTE window blank (logo + empty language box only) | pywebview 6 injects a UMD module that breaks the ZTE UI's RequireJS; the app patches it at start - if the log says "AMD patch not applied", pywebview changed: pin `--with "pywebview==6.2.1"` |
| App: "Load failed" once right after start (log) | WebFig redirects `/webfig/` -> `/` -> `/webfig/`; the script is loaded again, harmless |
| App can't reach the router (probe line in the log times out) | macOS Local Network permission, firewall, or wrong address in `routers.json` |
| ZTE prompt: "The operation timed out" (macOS app) | only with an old pywebview (log: `prompts: app bridge`) - use pywebview 6+ (`uv run --with pywebview` / `pip install -U pywebview`) |
| MikroTik panel: "not logged in" | click **login** in the panel header, enter the RouterOS user / password |
| Several login dialogs stacked up (macOS app) | fixed: WebFig reloads itself on start, each reload used to leave its dialog open; a new page now closes the old page's dialogs |
| MikroTik panel: "router rejected the login" | the router answered 401 to the user / password just typed (not saved). Check them in the WebFig login; the password box hides what you type, so check the keyboard layout (e.g. Cyrillic). The log notes "non-ASCII characters" / "leading/trailing space" when present |
| Slow requests (~5 s) in the log | a neighbour scan was running - expected |

Investigate with `--debug`: every REST call with status and time, page errors and prompt timings are written to the
terminal and the log file (passwords are never logged).

## Tests

```bash
cd legacy
node --check zte-script-legacy.js && node tests/test_cmp.js zte-script-legacy.js
node --check mikrotik-script.js && node tests/test_mikrotik.js mikrotik-script.js
python3 app/zte_app.py --selftest
```

Details in [`tests/README.md`](tests/README.md).
