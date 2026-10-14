// ==UserScript==
// @name         MikroTik LTE Helper
// @match        http://*/webfig/*
// @match        https://*/webfig/*
// @grant        none
// @inject-into  page
// @run-at       document-idle
// ==/UserScript==
/*
 * MikroTik ATL 5G (ATLGM, Quectel RG520F modem) helper for RouterOS v7 WebFig.
 * Talks to the RouterOS REST API (/rest, HTTP basic auth) of the same origin - no extra service needed.
 *
 * Use: Tampermonkey/Violentmonkey (header above), paste into the DevTools console on the WebFig page,
 *      or let app/zte_app.py inject it into the MikroTik window.
 *
 * Changes apply live, no reboot:
 *   - network mode / LTE bands / NR bands:  /interface lte set   (modem re-registers, link drops for a few seconds)
 *   - cell lock: AT+QNWLOCK via /interface lte at-chat  (Quectel; NOT persistent - modem forgets it on reboot/reset)
 *   - NR cell lock ("common/5g",pci,arfcn,scs,band) works in SA and NSA (verified on the RG520F in 5G NSA).
 *
 * Antenna alignment (same as the ZTE script): live table with change since start / peak / 5 min sparkline,
 * per-rx-path RSRP, branch warning, alignment tones (computer speakers) and saved positions with CSV export.
 * Cell evaluations: a single-cell lock from the panel (or "Measure current cell") records a 60 s average on that cell.
 */

console.log("Loading MikroTik Script v" + "2026-10-14-#16");

var MT_IF = "lte1";
var mt = { ready: false, id: null, auth: null, stopped: false, busy: false, nb_busy: false, cap: {}, serving: [], nb: [] };

function $id(id) { return document.getElementById(id); }
function mt_num(v) { var n = parseFloat(v); return isNaN(n) ? null : n; }
function mt_esc(v) { return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); }
function mt_msg(text, bad) { var e = $id("mt_msg"); if (e) { e.textContent = text; e.style.color = bad ? "#c00" : "#080"; } if (bad) mt_log("ERROR shown:", text); }

// Logs to the console and, inside the Mac app, to its terminal/log file (window.pywebview.api.log).
// Lines written before pywebview is ready are queued. window.mt_debug = true (app --debug) logs every REST call.
var mt_logq = [];
function mt_log()
{
    var msg = [].map.call(arguments, function (a) { return typeof a == "string" ? a : JSON.stringify(a); }).join(" ");
    console.log("mt:", msg);
    mt_logq.push(new Date().toISOString().slice(11, 23) + " " + msg);
    if (mt_logq.length > 200) mt_logq.shift();   // plain browser: no pywebview, keep the queue small
    mt_log_flush();
}
function mt_log_flush()
{
    var api = window.pywebview && window.pywebview.api;
    if (!api || !api.log) return;
    while (mt_logq.length) api.log("mt: " + mt_logq.shift());
}
if (typeof window !== "undefined" && window.addEventListener) {
    window.addEventListener("pywebviewready", mt_log_flush);
    window.addEventListener("error", function (e) { mt_log("window error:", e.message, (e.filename || "") + ":" + e.lineno); });
    window.addEventListener("unhandledrejection", function (e) { mt_log("unhandled rejection:", String(e.reason && (e.reason.stack || e.reason))); });
}

// ---------- REST ----------

function mt_auth_get() { try { return localStorage.getItem("mt_auth"); } catch (e) { return null; } }
function mt_auth_set(v) { try { v ? localStorage.setItem("mt_auth", v) : localStorage.removeItem("mt_auth"); } catch (e) {} }

// macOS app: native dialog via api.ask (password hidden). Browser / app on Windows+Linux: plain prompt().
async function mt_prompt(msg, def)
{
    var api = window.pywebview && window.pywebview.api;
    return api && api.ask && window.app_ask ? await api.ask(msg, def) : prompt(msg, def);   // app_ask: macOS app (hidden password)
}

// one login dialog at a time: parallel 401s (poll + lock status) share it
function mt_login()
{
    if (!mt.login_p) mt.login_p = mt_login_ask().finally(function () { mt.login_p = null; });
    return mt.login_p;
}

async function mt_login_ask()
{
    mt_log("REST login prompt");
    var u, p;
    try {
        u = await mt_prompt("RouterOS user for the REST API", "admin");
        p = u == null ? null : await mt_prompt("RouterOS password for " + u, "");
    } catch (e) { mt_log("prompt failed:", e.name, e.message); throw e; }
    // never the password itself - only hints for "router says 401": hidden-answer dialogs make a wrong keyboard layout invisible
    mt_log("REST login prompt", p == null ? "cancelled" : "answered for user " + u + mt_cred_hints(u, p));
    if (p == null) { mt.stopped = true; mt_msg("not logged in - click 'login'", true); mt_auth_ui(); return false; }
    mt.auth = "Basic " + btoa(unescape(encodeURIComponent(u + ":" + p)));
    mt.auth_fresh = true;   // saved only after the router accepts it (mt_api)
    mt.stopped = false;
    mt_auth_ui();
    return true;
}

function mt_cred_hints(u, p)
{
    var h = [];
    if (/[^\x20-\x7e]/.test(u + p)) h.push("non-ASCII characters (keyboard layout?)");
    if (/^\s|\s$/.test(u) || /^\s|\s$/.test(p)) h.push("leading/trailing space");
    if (!p) h.push("empty password");
    return h.length ? " - note: " + h.join(", ") : "";
}

async function mt_relogin() { mt.stopped = false; if (await mt_login()) { mt_msg("logged in"); if (!mt.ready) mt_start(); } mt_auth_ui(); }
function mt_logout() { mt.auth = null; mt_auth_set(null); mt.stopped = true; mt_msg("logged out - updates stopped"); mt_auth_ui(); }

function mt_user() { try { return mt.auth ? decodeURIComponent(escape(atob(mt.auth.slice(6)))).split(":")[0] : null; } catch (e) { return null; } }

// header: "admin · logout" while a login is stored, "login" otherwise
function mt_auth_ui()
{
    var e = $id("mt_who");
    if (!e) return;
    var u = mt_user();
    e.innerHTML = u && !mt.stopped
        ? "<span style='color:#080'>&#9679;</span> " + mt_esc(u) + " &middot; <a onclick='mt_logout()'>logout</a>"
        : "<span style='color:#c00'>&#9679;</span> <a onclick='mt_relogin()'>login</a>";
}

async function mt_api(path, body, method)
{
    if (mt.stopped) throw new Error("not logged in");
    var sent = mt.auth, h = { "Content-Type": "application/json" }, m = method || (body ? "POST" : "GET"), t0 = Date.now(), r;
    // Mac app: the request is made by Python (api.rest) - WKWebView dropped the Authorization header (router: 401, no
    // login attempt in its log). Browser: plain same-origin fetch.
    var api = window.pywebview && window.pywebview.api, via_app = !!(api && api.rest);
    var what = m + " /rest" + path + (body ? " " + JSON.stringify(body) : "") + " auth=" + (sent ? "header" : "none") + (via_app ? " via app" : "");
    if (sent) h.Authorization = sent;
    try {
        if (via_app) {
            var x = await api.rest(location.origin, m, "/rest" + path, body || null, sent || "");
            if (!x || !x.status) throw new TypeError((x && x.text) || "no answer from the app");
            r = { ok: x.status >= 200 && x.status < 300, status: x.status, text: async function () { return x.text; } };
        }
        else r = await fetch("/rest" + path, { method: m, headers: h, body: body ? JSON.stringify(body) : undefined });
    } catch (e) {
        mt_log(what, "-> FETCH FAILED after", Date.now() - t0, "ms:", e.name, e.message);
        throw e;
    }
    var ms = Date.now() - t0;
    if (window.mt_debug || !r.ok || ms > 3000) mt_log(what, "->", r.status, ms + " ms");
    if (r.ok && sent && sent === mt.auth && mt.auth_fresh) {   // first success with a typed login: keep it
        mt.auth_fresh = false; mt_auth_set(mt.auth); mt_log("REST login accepted for", mt_user()); mt_auth_ui();
    }
    if (r.status == 401) {
        if (mt.auth !== sent) return mt_api(path, body, method);   // another request logged in meanwhile - retry
        if (sent && mt.auth_fresh) {
            // the login just typed was rejected: stop here instead of asking again and again
            var who = mt_user();
            mt.auth = null; mt.auth_fresh = false; mt_auth_set(null); mt.stopped = true; mt_auth_ui();
            mt_msg("router rejected the login for '" + who + "' - check user / password (keyboard layout), then click login", true);
            throw new Error("login rejected (HTTP 401)");
        }
        if (await mt_login()) return mt_api(path, body, method);   // no login yet, or the stored one stopped working
    }
    var text = await r.text().catch(function (e) { mt_log(what, "-> body read failed:", e.name, e.message); return ""; });
    var j = null;
    try { j = text ? JSON.parse(text) : null; } catch (e) { mt_log(what, "-> non-JSON body:", text.slice(0, 300)); }
    if (!r.ok) { mt_log(what, "-> error body:", text.slice(0, 300)); throw new Error((j && (j.detail || j.message)) || "HTTP " + r.status); }
    return j;
}

// lenient: return the answer even without OK (chained commands stop at the first ERROR, earlier parts are still useful)
async function mt_at(cmd, lenient)
{
    var r = await mt_api("/interface/lte/at-chat", { ".id": MT_IF, input: cmd });
    var out = (r && r[0] && r[0].output) || "";
    if (!lenient && !/\bOK\b/.test(out)) throw new Error(cmd + " -> " + (out || "no answer"));
    return out;
}

// '+QENG: "servingcell","SEARCH"' -> "SEARCH" (NOCONN / CONNECT = camped; SEARCH / LIMSRV = no usable cell)
function al_modem_state(out) { var m = String(out || "").match(/"servingcell","([A-Z]+)"/); return m ? m[1] : ""; }

// ---------- parsers (pure, covered by tests/test_mikrotik.js) ----------

// "n78@100Mhz earfcn: 649920 phy-cellid: 432,B3@20Mhz earfcn: 1800 phy-cellid: 341"
function mt_parse_cells(s)
{
    return String(s || "").split(",").map(function (x) {
        var m = x.match(/\b([BbNn]\d+)(?:@([\d.]+)Mhz)?(?: earfcn: (\d+))?(?: phy-cellid: (\d+))?/);
        return m && { band: m[1], bw: m[2] || "", earfcn: m[3] || "", pci: m[4] || "", nr: /^n/i.test(m[1]) };
    }).filter(Boolean);
}

// '+QNWLOCK: "common/4g",1,3150,341\r\nOK'  |  '+QNWLOCK: "common/5g",432,649920,30,78'  |  '...",0'
function mt_parse_lock(out)
{
    var m = String(out).match(/\+QNWLOCK: "common\/(4g|5g)",([^\r\n]*)/);
    if (!m) return null;
    var v = m[2].split(",").map(Number);
    if (v.length == 1 && v[0] === 0) return { rat: m[1], on: false, cells: [] };
    if (m[1] == "5g") return { rat: "5g", on: true, cells: [{ pci: v[0], arfcn: v[1], scs: v[2], band: v[3] }] };
    var cells = [];
    for (var i = 1; i + 1 < v.length; i += 2) cells.push({ earfcn: v[i], pci: v[i + 1] });
    return { rat: "4g", on: true, cells: cells };
}

// "3150,341; 1800,308" -> [{earfcn, pci}], null when anything is not a whole number
function mt_parse_pairs(s)
{
    var cells = String(s || "").split(/[;\s]+/).filter(Boolean).map(function (p) {
        var a = p.split(",");
        return { earfcn: Number(a[0]), pci: Number(a[1]), ok: a.length == 2 && a[0] !== "" && a[1] !== "" };
    });
    if (!cells.length || cells.some(function (c) { return !c.ok || !Number.isInteger(c.earfcn) || !Number.isInteger(c.pci) || c.earfcn < 0 || c.pci < 0 || c.pci > 1007; }))
        return null;
    return cells.map(function (c) { return { earfcn: c.earfcn, pci: c.pci }; });
}

function mt_lock4_cmd(cells)
{
    return cells && cells.length
        ? 'AT+QNWLOCK="common/4g",' + cells.length + "," + cells.map(function (c) { return c.earfcn + "," + c.pci; }).join(",")
        : 'AT+QNWLOCK="common/4g",0';
}

function mt_lock5_cmd(c) { return c ? 'AT+QNWLOCK="common/5g",' + [c.pci, c.arfcn, c.scs, c.band].join(",") : 'AT+QNWLOCK="common/5g",0'; }

// subcarrier spacing (kHz) for the 5G lock: TDD mid-band 30, FDD/SDL 15
function mt_scs(band) { return [38, 40, 41, 77, 78, 79].indexOf(parseInt(String(band).replace(/^n/i, ""))) >= 0 ? 30 : 15; }

// cell-monitor returns several ".section" snapshots: keep the newest entry per cell, strongest first
function mt_neighbours(list, serving)
{
    var by = {};
    (list || []).forEach(function (x) { by[x.band + ":" + x.earfcn + ":" + x["phy-cellid"]] = x; });
    return Object.keys(by).map(function (k) {
        var x = by[k];
        return { band: x.band, earfcn: x.earfcn, pci: x["phy-cellid"], rsrp: x.rsrp, rsrq: x.rsrq, sinr: x.sinr, nr: /^n/i.test(x.band),
                 serving: (serving || []).some(function (s) { return s.earfcn == x.earfcn && s.pci == x["phy-cellid"]; }) };
    }).sort(function (a, b) {
        var va = mt_num(a.rsrp), vb = mt_num(b.rsrp);
        if (va == null || vb == null) return (va == null) - (vb == null);   // missing last
        return vb - va || mt_num(b.rsrq) - mt_num(a.rsrq) || 0;
    });
}

function mt_col(v, good, bad) { v = mt_num(v); return v == null ? "#999" : v >= good ? "#080" : v <= bad ? "#c00" : "#c80"; }
function mt_val(v, good, bad, unit) { return v == null || v === "" ? "<span style='color:#999'>-</span>" : "<b style='color:" + mt_col(v, good, bad) + "'>" + mt_esc(v) + "</b>" + (unit || ""); }

// ---------- signal ----------

function mt_sig_html(m)
{
    var cells = mt_parse_cells(m["primary-band"]).map(function (c) { c.role = "P"; return c; })
        .concat(mt_parse_cells(m["ca-band"]).map(function (c) { c.role = "S"; return c; }));
    var rows = cells.map(function (c) {
        return "<tr><td>" + c.role + "</td><td>" + mt_esc(c.band) + "</td><td>" + mt_esc(c.bw) + "</td><td>" + mt_esc(c.earfcn) + "</td><td>" + mt_esc(c.pci) + "</td></tr>";
    }).join("");
    return "<div>" + mt_esc(m["current-operator"]) + " &middot; <b>" + mt_esc(m["data-class"] || m.status) + "</b>" + (m.roaming == "true" ? " &middot; roaming" : "") +
        " &middot; eNB " + mt_esc(m["enb-id"]) + "/" + mt_esc(m["sector-id"]) + " &middot; up " + mt_esc(m["session-uptime"]) + "</div>" +
        "<table class=mt_t><tr><th>LTE</th><td>RSRP " + mt_val(m.rsrp, -80, -105) + "</td><td>RSRQ " + mt_val(m.rsrq, -10, -15) + "</td><td>SINR " + mt_val(m.sinr, 13, 0) +
        "</td><td>RSSI " + mt_val(m.rssi, -65, -85) + "</td><td>CQI " + mt_esc(m.cqi || "-") + " MCS " + mt_esc(m.mcs || "-") + " RI " + mt_esc(m.ri || "-") + "</td></tr>" +
        "<tr><th>NR</th><td>RSRP " + mt_val(m["nr-rsrp"], -80, -105) + "</td><td>RSRQ " + mt_val(m["nr-rsrq"], -10, -15) + "</td><td>SINR " + mt_val(m["nr-sinr"], 13, 0) +
        "</td><td colspan=2>" + mt_esc(m["nr-dl-modulation"] || "") + "</td></tr></table>" +
        "<table class=mt_t><tr><th></th><th>band</th><th>MHz</th><th>(E)ARFCN</th><th>PCI</th></tr>" + rows + "</table>";
}

async function mt_poll()
{
    if (mt.busy || mt.stopped || mt.nb_busy) return;   // cell-monitor scan holds the modem for 5 s
    mt.busy = true;
    try {
        var m = mt.mon = (await mt_api("/interface/lte/monitor", { numbers: MT_IF, once: "" }))[0] || {};
        mt.serving = mt_parse_cells(m["primary-band"]).concat(mt_parse_cells(m["ca-band"]));
        $id("mt_sig").innerHTML = mt_sig_html(m);
        if (mt_store("mt_exp_pending", null)) {   // cell evaluation running: add lte1 throughput
            var tr = (await mt_api("/interface/monitor-traffic", { "interface": MT_IF, once: "" }))[0] || {};
            exp_tick(Object.assign({}, m, tr), Date.now());
        }
    } catch (e) { mt_msg("monitor: " + e.message, true); }
    mt.busy = false;
}

// ---------- bands ----------

function mt_checks(name, all, current)
{
    var on = String(current || "").split(",");
    return String(all || "").split(",").filter(Boolean).map(function (b) {
        return "<label><input type=checkbox name=" + name + " value='" + mt_esc(b) + "'" + (on.indexOf(b) >= 0 ? " checked" : "") + ">" + mt_esc(b) + "</label>";
    }).join(" ");
}

function mt_checked(name) { return [].slice.call(document.querySelectorAll("#mt_panel input[name=" + name + "]:checked")).map(function (e) { return e.value; }).join(","); }

function mt_render_bands(l)
{
    $id("mt_bands").innerHTML =
        "<div>Mode: " + mt_checks("mt_mode", mt.cap["rat-modes"] || "3g,lte,5g", l["network-mode"]) + "</div>" +
        "<div>LTE: " + mt_checks("mt_band", mt.cap["lte-bands"], l.band) + "</div>" +
        "<div>NR: " + mt_checks("mt_nrband", mt.cap["nr-bands"], l["nr-band"]) + "</div>" +
        "<div class=mt_note>none ticked = all bands. Now: LTE [" + mt_esc(l.band || "all") + "] NR [" + mt_esc(l["nr-band"] || "all") + "]</div>";
    if ($id("mt_op_box")) $id("mt_op_box").innerHTML = mt_operator_html(l.operator || "");
}

// ---------- operator (manual network selection; the SIM roams locally) ----------
// [MCC+MNC, label] - Bulgaria: A1 (ex M-Tel/Mobiltel) 284 01, Vivacom (BTC) 284 03, Yettel (ex Telenor/Globul) 284 05
var MT_OPERATORS = [["", "Automatic"], ["28401", "A1 (284 01)"], ["28403", "Vivacom (284 03)"], ["28405", "Yettel (284 05)"]];

function mt_operator_valid(v) { return v === "" || /^\d{5,6}$/.test(v); }

function mt_operator_html(cur)
{
    var list = MT_OPERATORS.concat(MT_OPERATORS.some(function (o) { return o[0] == cur; }) ? [] : [[cur, cur + " (set on router)"]]);
    return "<select id=mt_op>" + list.map(function (o) {
        return "<option value='" + mt_esc(o[0]) + "'" + (o[0] == cur ? " selected" : "") + ">" + mt_esc(o[1]) + "</option>";
    }).join("") + "<option value='?'>other MCC+MNC ...</option></select> <button onclick='mt_apply_operator()'>Apply</button>" +
    " <span class=mt_note>setting: " + mt_esc(cur || "automatic") + "</span>";
}

async function mt_apply_operator()
{
    var v = $id("mt_op").value;
    if (v == "?") {
        v = await mt_prompt("Operator MCC+MNC, 5-6 digits (e.g. 28403 = Vivacom)", "");
        if (v == null) return;
        v = v.replace(/\s/g, "");
    }
    if (!mt_operator_valid(v)) return mt_msg("operator: 5-6 digits MCC+MNC, e.g. 28403", true);
    try {
        if (v) await mt_api("/interface/lte/" + encodeURIComponent(mt.id), { operator: v }, "PATCH");
        else {
            try { await mt_api("/interface/lte/unset", { numbers: mt.id, "value-name": "operator" }); }
            catch (e) { mt_log("unset operator failed (" + e.message + "), trying operator=\"\""); await mt_api("/interface/lte/" + encodeURIComponent(mt.id), { operator: "" }, "PATCH"); }
        }
        await mt_load_bands();
        mt_msg("operator " + (v || "automatic") + " applied (no reboot) - modem re-registers, watch the operator line");
    } catch (e) { mt_msg("operator: " + e.message, true); }
}

async function mt_load_bands() { mt_render_bands((await mt_api("/interface/lte/" + encodeURIComponent(mt.id)))); }

async function mt_apply_bands(all)
{
    var body = all ? { band: "", "nr-band": "" } : { band: mt_checked("mt_band"), "nr-band": mt_checked("mt_nrband"), "network-mode": mt_checked("mt_mode") };
    if (!all && !body["network-mode"]) return mt_msg("tick at least one network mode", true);
    try {
        await mt_api("/interface/lte/" + encodeURIComponent(mt.id), body, "PATCH");
        await mt_load_bands();
        mt_msg("bands applied (no reboot) - modem re-registers");
    } catch (e) { mt_msg("bands: " + e.message, true); }
}

// ---------- cell lock ----------

async function mt_lock_status()
{
    if (mt.nb_busy) return;
    try {
        var l4 = mt_parse_lock(await mt_at('AT+QNWLOCK="common/4g"')), l5 = mt_parse_lock(await mt_at('AT+QNWLOCK="common/5g"'));
        $id("mt_lock").innerHTML =
            "LTE: " + (l4 && l4.on ? "<b style='color:#c60'>" + l4.cells.map(function (c) { return c.earfcn + "/" + c.pci; }).join(" ") + "</b>" : "off") +
            " &middot; NR: " + (l5 && l5.on ? "<b style='color:#c60'>" + [l5.cells[0].pci, l5.cells[0].arfcn, l5.cells[0].scs, "n" + l5.cells[0].band].join("/") + "</b>" : "off");
    } catch (e) { mt_msg("lock status: " + e.message, true); }
}

async function mt_send_lock(cmd, what, on_ok)
{
    try { await mt_at(cmd); mt_msg(what + " (no reboot, lost on modem reset)"); if (on_ok) on_ok(); }
    catch (e) { mt_msg(e.message, true); }
    mt_lock_status();
}

function mt_lock4(off)
{
    var cells = off ? [] : mt_parse_pairs($id("mt_l4").value);
    if (!cells) return mt_msg("LTE lock: use earfcn,pci[;earfcn,pci]", true);
    // single cell: measure it for 60 s once the modem is on it (multi-cell: the modem picks, nothing to evaluate)
    var c = cells.length == 1 ? cells[0] : null;
    mt_send_lock(mt_lock4_cmd(cells), off ? "LTE lock removed" : "LTE locked",
                 c && function () { exp_arm(false, c.earfcn, c.pci, "LTE lock PCI " + c.pci + " @ " + c.earfcn, EXP_SETTLE_MS); });
}

function mt_lock5(off)
{
    var a = $id("mt_l5").value.split(",").map(function (x) { return Number(x.trim().replace(/^n/i, "")); });
    if (!off && (a.length != 4 || !a.every(Number.isInteger))) return mt_msg("NR lock: use pci,arfcn,scs,band", true);
    mt_send_lock(mt_lock5_cmd(off ? null : { pci: a[0], arfcn: a[1], scs: a[2], band: a[3] }), off ? "NR lock removed" : "NR locked",
                 !off && function () { exp_arm(true, a[1], a[0], "5G lock PCI " + a[0] + " @ " + a[1] + " n" + a[3], EXP_SETTLE_MS); });
}

function mt_pick(nr, earfcn, pci, band, add)
{
    if (nr) {
        var b = parseInt(String(band).replace(/^n/i, ""));
        $id("mt_l5").value = [pci, earfcn, mt_scs(b), b].join(",");
        return mt_lock5();
    }
    var cur = add ? $id("mt_l4").value.trim() : "";
    $id("mt_l4").value = (cur ? cur + ";" : "") + earfcn + "," + pci;
    if (!add) mt_lock4();
}

// ---------- neighbours ----------

async function mt_scan()
{
    if (mt.nb_busy || mt.stopped) return;
    mt.nb_busy = true;
    $id("mt_nb_state").textContent = "scanning 5 s...";
    try {
        mt.nb = mt_neighbours(await mt_api("/interface/lte/cell-monitor", { ".id": MT_IF, duration: "5s" }), mt.serving);
        $id("mt_nb").innerHTML = "<table class=mt_t><tr><th>band</th><th>(E)ARFCN</th><th>PCI</th><th>RSRP</th><th>RSRQ</th><th>SINR</th><th></th></tr>" +
            mt.nb.map(function (c) {
                var args = [c.nr, Number(c.earfcn), Number(c.pci), "'" + mt_esc(c.band.split("/")[0]) + "'"].join(",");
                return "<tr" + (c.serving ? " class=mt_srv title='serving'" : "") + "><td>" + mt_esc(c.band) + "</td><td>" + mt_esc(c.earfcn) + "</td><td>" + mt_esc(c.pci) +
                    "</td><td>" + mt_val(c.rsrp, -80, -105) + "</td><td>" + mt_val(c.rsrq, -10, -15) + "</td><td>" + mt_val(c.sinr, 13, 0) + "</td><td>" +
                    (c.nr ? "<a onclick=\"mt_pick(" + args + ")\">lock</a>"
                          : "<a onclick=\"mt_pick(" + args + ")\">lock</a> <a title='add to LTE lock list' onclick=\"mt_pick(" + args + ",true)\">+</a>") + "</td></tr>";
            }).join("") + "</table>";
        $id("mt_nb_state").textContent = new Date().toLocaleTimeString();
    } catch (e) { $id("mt_nb_state").textContent = ""; mt_msg("scan: " + e.message, true); }
    mt.nb_busy = false;
}

// ---------- antenna alignment (port of the ZTE script's "Antenna alignment": table, tones, saved positions) ----------
// Source: one at-chat call 'AT+QENG="servingcell";+QRSRP' (~15 ms) - serving LTE/5G RSRP/RSRQ/SINR plus RSRP per rx path.
// Polls only while the Antenna alignment box is open or the tone is on; pauses during a neighbour scan.

var AL_HISTORY_MS = 5 * 60 * 1000;   // sparkline window
var AL_AVG_MS = 3000;                // "now" = mean of the last 3 s (modem values jump)
var TONE_AVG_MS = 1500;              // tone reacts faster (use Fast update)
var AL_CMD = 'AT+QENG="servingcell";+QRSRP';
// [key, label, unit] - every metric is "higher = better"
var AL_METRICS = [
    ["nr_sinr", "5G SINR", "dB"], ["nr_rsrp", "5G RSRP", "dBm"], ["nr_rsrq", "5G RSRQ", "dB"],
    ["nr_rx0", "5G RSRP rx0", "dBm"], ["nr_rx1", "5G RSRP rx1", "dBm"], ["nr_rx2", "5G RSRP rx2", "dBm"], ["nr_rx3", "5G RSRP rx3", "dBm"],
    ["lte_sinr", "LTE SINR", "dB"], ["lte_rsrp", "LTE RSRP", "dBm"], ["lte_rsrq", "LTE RSRQ", "dB"],
    ["lte_p1", "LTE RSRP port 1", "dBm"], ["lte_p2", "LTE RSRP port 2", "dBm"], ["lte_p3", "LTE RSRP port 3", "dBm"], ["lte_p4", "LTE RSRP port 4", "dBm"]
];
var al = { hist: {}, base: {}, peak: {}, fast: false, busy: false, tone: null, tone_mode: "dual", tone_res: "high" };

// Quectel answers -> { metric: number }. -140 (RSRP) and -32768 = "no value" (unused rx path / no 5G leg).
//  NSA: +QENG: "LTE","FDD",mcc,mnc,cellid,pci,earfcn,band,ulbw,dlbw,tac,RSRP,RSRQ,RSSI,SINR,...  +QENG: "NR5G-NSA",mcc,mnc,pci,RSRP,SINR,RSRQ,...
//  LTE only: same LTE fields on the "servingcell" line.   SA: +QENG: "servingcell",st,"NR5G-SA",dup,mcc,mnc,cellid,pci,tac,arfcn,band,bw,RSRP,RSRQ,SINR
//  +QRSRP: rx0,rx1,rx2,rx3,LTE|NR5G
function al_parse(out)
{
    var o = String(out || ""), r = {}, m, f;
    var num = function (v) { var n = parseFloat(v); return isNaN(n) || n <= -140 || n == -32768 ? undefined : n; };
    if ((m = o.match(/"LTE","(?:FDD|TDD)",([^\r\n]*)/))) { f = m[1].split(","); r.lte_rsrp = num(f[9]); r.lte_rsrq = num(f[10]); r.lte_sinr = num(f[12]); }
    if ((m = o.match(/"NR5G-NSA",([^\r\n]*)/))) { f = m[1].split(","); r.nr_rsrp = num(f[3]); r.nr_sinr = num(f[4]); r.nr_rsrq = num(f[5]); }
    if ((m = o.match(/"NR5G-SA","(?:FDD|TDD)",([^\r\n]*)/))) { f = m[1].split(","); r.nr_rsrp = num(f[8]); r.nr_rsrq = num(f[9]); r.nr_sinr = num(f[10]); }
    if ((m = o.match(/\+QRSRP: ([^\r\n]*),LTE/))) m[1].split(",").slice(0, 4).forEach(function (v, i) { r["lte_p" + (i + 1)] = num(v); });
    if ((m = o.match(/\+QRSRP: ([^\r\n]*),NR5G/))) m[1].split(",").slice(0, 4).forEach(function (v, i) { r["nr_rx" + i] = num(v); });
    Object.keys(r).forEach(function (k) { if (r[k] === undefined) delete r[k]; });
    return r;
}

function al_avg(key, ms, now)
{
    var h = al.hist[key] || [], s = 0, n = 0;
    now = now || Date.now();
    for (var i = h.length - 1; i >= 0 && now - h[i].t <= (ms || AL_AVG_MS); i--) { s += h[i].v; n++; }
    return n ? s / n : NaN;
}

function al_feed(v, now)
{
    now = now || Date.now();
    AL_METRICS.forEach(function (m) {
        if (v[m[0]] === undefined) return;
        var h = al.hist[m[0]] = al.hist[m[0]] || [];
        h.push({ t: now, v: v[m[0]] });
        while (h.length && now - h[0].t > AL_HISTORY_MS) h.shift();
        var avg = al_avg(m[0], AL_AVG_MS, now);
        if (al.base[m[0]] === undefined) al.base[m[0]] = avg;
        if (!al.peak[m[0]] || avg > al.peak[m[0]].v) al.peak[m[0]] = { v: avg, t: now };
    });
}

function al_reset() { al.base = {}; al.peak = {}; if (al.tone) al.tone.peaks = {}; mt_msg("alignment start/peak reset"); }

function al_sparkline(key, now)
{
    var h = al.hist[key] || [];
    if (h.length < 2) return "";
    now = now || Date.now();
    var w = 140, ht = 22, t0 = Math.max(h[0].t, now - AL_HISTORY_MS), tspan = (now - t0) || 1;
    var vs = h.map(function (p) { return p.v; }), lo = Math.min.apply(null, vs), hi = Math.max.apply(null, vs), span = (hi - lo) || 1;
    var pts = h.map(function (p) {
        return ((p.t - t0) / tspan * w).toFixed(1) + "," + (hi == lo ? ht / 2 : ht - 2 - (p.v - lo) / span * (ht - 4)).toFixed(1);
    }).join(" ");
    return "<svg width='" + w + "' height='" + ht + "'><title>" + lo + " .. " + hi + "</title><polyline fill='none' stroke='#40adf5' stroke-width='1.5' points='" + pts + "'/></svg>";
}

// rx paths of one antenna system far apart = antenna turned / shadowed on one side
function al_branch_warning(now)
{
    var w = [];
    [["5G rx", ["nr_rx0", "nr_rx1", "nr_rx2", "nr_rx3"]], ["LTE ports", ["lte_p1", "lte_p2", "lte_p3", "lte_p4"]]].forEach(function (g) {
        var p = g[1].map(function (k) { return al_avg(k, AL_AVG_MS, now); }).filter(function (v) { return !isNaN(v); });
        var d = p.length > 1 ? Math.max.apply(null, p) - Math.min.apply(null, p) : 0;
        if (d > 6) w.push(g[0] + " differ by " + d.toFixed(1) + " dB");
    });
    return w.length ? "<b style='color:#f90'>" + w.join("; ") + "</b> - try rotating / repositioning" : "balanced (&le; 6 dB)";
}

function al_table_html(now)
{
    var html = "<tr><th>metric</th><th>now (3 s)</th><th>&Delta; start</th><th>peak</th><th>last 5 min</th></tr>";
    AL_METRICS.forEach(function (m) {
        var v = al_avg(m[0], AL_AVG_MS, now), pk = al.peak[m[0]];
        if (isNaN(v) || al.base[m[0]] === undefined || !pk) return;
        var d = Math.round((v - al.base[m[0]]) * 10) / 10;
        html += "<tr><td>" + m[1] + "</td><td><b>" + v.toFixed(1) + "</b>&nbsp;" + m[2] + "</td>" +
                "<td style='color:" + (d > 0 ? "#3a3" : d < 0 ? "#d44" : "#999") + "'>" + (d > 0 ? "+" : "") + (d == 0 ? "0.0" : d.toFixed(1)) + "</td>" +
                "<td title='" + new Date(pk.t).toLocaleTimeString() + "'>" + pk.v.toFixed(1) + "</td><td>" + al_sparkline(m[0], now) + "</td></tr>";
    });
    return html;
}

function al_render()
{
    if (!$id("al_table")) return;
    $id("al_branch").innerHTML = al_branch_warning();
    $id("al_table").innerHTML = al_table_html();
}

async function al_poll()
{
    if (al.busy || mt.stopped || mt.nb_busy || !mt.ready) return;
    if (!($id("al_box") && $id("al_box").open) && !al.tone) return;
    al.busy = true;
    try {
        // while the modem searches (after a band/cell lock, lost coverage) +QRSRP answers ERROR: show it, no error
        var out = await mt_at(AL_CMD, true), st = al_modem_state(out), v = al_parse(out), none = !Object.keys(v).length;
        var text = none ? "modem " + (st || "no answer") + " - no serving cell, waiting ..." : "";
        if (text != al.state_text) { mt_log("alignment state:", text || "values again (" + st + ")"); al.state_text = text; }
        $id("al_state").textContent = text;
        if (!none) { al_feed(v); al_render(); tone_update(); }
    }
    catch (e) { mt_msg("alignment: " + e.message, true); }
    al.busy = false;
}

function al_loop() { al_poll(); setTimeout(al_loop, al.fast ? 500 : 1000); }

/*
 * Alignment sounds (off by default; the Tone checkbox click also unlocks audio). Modes (al.tone_mode):
 *  "dual"     5G + LTE SINR: pair of beeps - high note = 5G, low note = LTE, pitch follows each SINR;
 *             beeps speed up as the weaker one gets closer to its target; steady tone = both reached.
 *             chirp up = 5G new best, chirp down = LTE new best, 3-note chime = both at their best.
 *  "balance"  5G rx0 vs rx1: steady tone when within the window; low beeps = rx0 stronger, high = rx1 stronger.
 *  "m:<key>"  one metric: faster + higher beeps for better values, double chirp on a new best.
 */
// legend text from the current targets (they are editable)
function tone_legend(mode)
{
    var T = function (k) { return TONE_TARGET[k][1]; };
    return {
        dual: "pair of beeps: high = 5G SINR, low = LTE SINR; faster = the one further from its target (5G " + T("dual_nr_sinr") + " dB, LTE " + T("dual_lte_sinr") +
              " dB) gets closer; steady tone = both reached. Chirp up = 5G best, chirp down = LTE best, 3-note chime = both at best.",
        balance: "target rx0 - rx1 = 0: steady tone = balanced (within 2 / 1 / 0.5 dB by resolution). Low beeps = rx0 stronger, high beeps = rx1 stronger; faster = closer to balanced.",
        metric: "faster = closer to target (5G SINR " + T("nr_sinr") + " dB, LTE SINR " + T("lte_sinr") + " dB, RSRQ " + T("rsrq") + " dB, RSRP " + T("rsrp") +
                " dBm); steady tone = target reached; double chirp = new best."
    }[mode.indexOf("m:") == 0 ? "metric" : mode];
}
// k: semitones per dB (0 = absolute scale), thr: balance window (dB), best: dB needed for a "new best" chirp
var TONE_RES = { abs: { k: 0, thr: 2, best: 0.5 }, high: { k: 2, thr: 1, best: 0.3 }, vhigh: { k: 4, thr: 0.5, best: 0.2 } };
// [floor, target] - the tone says "done" (steady) at the target; beep speed = closeness floor..target.
// Defaults higher than the ZTE's (outdoor ATL antenna): starting points, editable in the panel.
var TONE_TARGET = { nr_sinr: [-10, 25], lte_sinr: [-10, 20], rsrq: [-20, -10], rsrp: [-120, -70], dual_nr_sinr: [-10, 20], dual_lte_sinr: [-10, 15] };

// Editable targets (alignment box, saved in localStorage "mt_tone_targets"): [key, label]. Floors stay fixed.
var TONE_TARGET_DEFAULT = JSON.parse(JSON.stringify(TONE_TARGET));
var TONE_TARGET_EDIT = [["dual_nr_sinr", "5G+LTE mode: 5G SINR"], ["dual_lte_sinr", "LTE SINR"],
                        ["nr_sinr", "single metric: 5G SINR"], ["lte_sinr", "LTE SINR"], ["rsrq", "RSRQ"], ["rsrp", "RSRP"]];

// saved targets over the defaults; anything missing / not a number / not above the floor -> default
function tone_targets_load(saved)
{
    TONE_TARGET_EDIT.forEach(function (e) {
        var k = e[0], s = saved ? saved[k] : undefined, v = Number(s);
        var ok = s !== undefined && s !== null && String(s).trim() !== "" && isFinite(v) && v > TONE_TARGET_DEFAULT[k][0];
        TONE_TARGET[k] = [TONE_TARGET_DEFAULT[k][0], ok ? v : TONE_TARGET_DEFAULT[k][1]];
    });
}

// false = rejected (empty, not a number, not above the floor) - the input then shows the current value again
function tone_target_set(k, v)
{
    var n = Number(v);
    if (!TONE_TARGET[k] || String(v).trim() === "" || !isFinite(n) || n <= TONE_TARGET[k][0]) return false;
    TONE_TARGET[k][1] = n;
    var s = {};
    TONE_TARGET_EDIT.forEach(function (e) { s[e[0]] = TONE_TARGET[e[0]][1]; });
    mt_save("mt_tone_targets", s);
    return true;
}

function tone_targets_html()
{
    return TONE_TARGET_EDIT.map(function (e) {
        return "<label>" + e[1] + " <input type=number step=1 style='width:4.5em' value='" + TONE_TARGET[e[0]][1] +
               "' title='above " + TONE_TARGET[e[0]][0] + "' onchange='tone_target_input(\"" + e[0] + "\", this)'></label>";
    }).join(" ") + " <a onclick='tone_targets_reset()'>defaults</a>";
}

function tone_target_input(k, el)
{
    if (!tone_target_set(k, el.value)) el.value = TONE_TARGET[k][1];
    tone_targets_render(false);
}

function tone_targets_reset() { mt_save("mt_tone_targets", {}); tone_targets_load({}); tone_targets_render(true); }

tone_targets_load(mt_store("mt_tone_targets", {}));

function tone_targets_render(inputs)
{
    if (inputs && $id("tone_targets")) $id("tone_targets").innerHTML = tone_targets_html();
    if ($id("tone_legend")) $id("tone_legend").innerHTML = tone_legend(al.tone_mode);
}

function tone_toggle(on)
{
    if (al.tone) { clearTimeout(al.tone.timer); al.tone.ctx.close(); al.tone = null; }
    if (!on) return;
    al.tone = { ctx: new (window.AudioContext || window.webkitAudioContext)(), timer: null, peaks: {}, hold: null, chime_at: 0, chirp_at: 0 };
    tone_loop();
}

function tone_set_mode(mode)
{
    al.tone_mode = mode;
    if (al.tone) { al.tone.peaks = {}; tone_hold(0); }
    $id("tone_legend").innerHTML = tone_legend(mode);
}

function tone_set_res(res) { al.tone_res = res; if (al.tone) al.tone.peaks = {}; }

function tone_beep(freq, start, len)
{
    var ctx = al.tone.ctx, osc = ctx.createOscillator(), g = ctx.createGain();
    osc.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, start);
    g.gain.exponentialRampToValueAtTime(0.08, start + 0.005);   // short ramps: no clicks
    g.gain.exponentialRampToValueAtTime(0.0001, start + len);
    osc.connect(g); g.connect(ctx.destination);
    osc.start(start); osc.stop(start + len + 0.02);
}

// sustained tone = "target reached"; freq 0 = off
function tone_hold(freq)
{
    var t = al.tone, now = t.ctx.currentTime;
    if (!freq) { if (t.hold) { t.hold.g.gain.setTargetAtTime(0.0001, now, 0.03); t.hold.osc.stop(now + 0.2); t.hold = null; } return; }
    if (t.hold) return;
    var osc = t.ctx.createOscillator(), g = t.ctx.createGain();
    osc.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, now);
    g.gain.setTargetAtTime(0.05, now, 0.03);
    osc.connect(g); g.connect(t.ctx.destination);
    osc.start(now);
    t.hold = { osc: osc, g: g };
}

function tone_chord(freqs) { var t = al.tone.ctx.currentTime; freqs.forEach(function (f, i) { tone_beep(f, t + i * 0.08, 0.06); }); }

function tone_range(key, dual)
{
    if (key.indexOf("sinr") >= 0) return TONE_TARGET[(dual ? "dual_" : "") + key];
    return key.indexOf("rsrq") >= 0 ? TONE_TARGET.rsrq : TONE_TARGET.rsrp;
}

// closeness to target: 0 at the floor, 1 at (or above) the target
function tone_level(key, dual, now)
{
    var v = al_avg(key, TONE_AVG_MS, now), r = tone_range(key, dual);
    return isNaN(v) ? NaN : Math.min(1, Math.max(0, (v - r[0]) / (r[1] - r[0])));
}

// one voice: pitch lo..hi Hz, x = closeness (beep speed), done = target reached.
// abs: pitch = closeness; high/vhigh: pitch = change from the reference (Reset start/peak), 2 / 4 semitones per dB
function tone_voice(key, lo, hi, dual, now)
{
    var v = al_avg(key, TONE_AVG_MS, now), r = TONE_RES[al.tone_res];
    if (isNaN(v)) return null;
    var x = tone_level(key, dual, now), f;
    if (!r.k) f = lo + x * (hi - lo);
    else {
        var ref = al.base[key] === undefined ? v : al.base[key];
        f = Math.sqrt(lo * hi) * Math.pow(2, Math.max(-18, Math.min(18, (v - ref) * r.k)) / 12);   // +-1.5 octaves
    }
    return { f: f, x: x, done: v >= tone_range(key, dual)[1] };
}

function tone_loop()
{
    var t = al.tone;
    if (!t) return;
    var now = t.ctx.currentTime, next = 500, mode = al.tone_mode, r = TONE_RES[al.tone_res];
    if (mode == "balance") {
        var d = al_avg("nr_rx0", TONE_AVG_MS) - al_avg("nr_rx1", TONE_AVG_MS);
        if (isNaN(d)) tone_hold(0);
        else if (Math.abs(d) <= r.thr) { tone_hold(660); next = 200; }
        else {
            tone_hold(0);
            tone_beep(d > 0 ? 440 : 990, now, 0.07);
            next = Math.min(1200, 120 + (Math.abs(d) - r.thr) * (r.k ? 400 / r.k : 100));
        }
    } else if (mode == "dual") {
        var v5 = tone_voice("nr_sinr", 700, 1600, true), v4 = tone_voice("lte_sinr", 250, 650, true);
        if (v5 && v4 && v5.done && v4.done) { tone_hold(880); next = 200; }
        else {
            tone_hold(0);
            if (v5) tone_beep(v5.f, now, 0.06);            // 5G: high voice
            if (v4) tone_beep(v4.f, now + 0.09, 0.06);     // LTE: low voice
            if (v5 || v4) next = Math.max(250, 1000 - Math.min(v5 ? v5.x : 1, v4 ? v4.x : 1) * 750);
        }
    } else {
        var v = tone_voice(mode.substr(2), 400, 1200, false);
        if (v && v.done) { tone_hold(880); next = 200; }
        else { tone_hold(0); if (v) { tone_beep(v.f, now, 0.06); next = 1000 - v.x * 880; } }
    }
    t.timer = setTimeout(tone_loop, next);
}

// on every reading: track bests, play "new best" sounds (max one per second)
function tone_update()
{
    var t = al.tone;
    if (!t || al.tone_mode == "balance") return;
    var keys = al.tone_mode == "dual" ? ["nr_sinr", "lte_sinr"] : [al.tone_mode.substr(2)], improved = {}, best = TONE_RES[al.tone_res].best;
    keys.forEach(function (k) {
        var v = al_avg(k, TONE_AVG_MS);
        if (isNaN(v)) return;
        if (t.peaks[k] !== undefined && v >= t.peaks[k] + best) improved[k] = true;
        if (t.peaks[k] === undefined || v > t.peaks[k]) t.peaks[k] = v;
    });
    if (!Object.keys(improved).length || Date.now() - t.chirp_at < 1000) return;
    t.chirp_at = Date.now();
    if (al.tone_mode != "dual") return tone_chord([1800, 2400]);
    var at_best = function (k) { var v = al_avg(k, TONE_AVG_MS); return !isNaN(v) && t.peaks[k] !== undefined && v >= t.peaks[k] - best; };
    if (at_best("nr_sinr") && at_best("lte_sinr") && Date.now() - t.chime_at > 3000) { t.chime_at = Date.now(); tone_chord([1200, 1600, 2000]); }
    else if (improved.nr_sinr) tone_chord([1800, 2400]);   // 5G new best: rising
    else tone_chord([1600, 1200]);                         // LTE new best: falling
}

// ---------- saved positions (localStorage "mt_positions", CSV export) ----------

var POSITION_COLS = ["nr_sinr", "nr_rsrp", "nr_rsrq", "nr_rx0", "nr_rx1", "nr_rx2", "nr_rx3", "lte_sinr", "lte_rsrp", "lte_rsrq"];

function mt_store(name, def) { try { var v = JSON.parse(localStorage.getItem(name)); return v === null ? def : v; } catch (e) { return def; } }
function mt_save(name, v) { try { localStorage.setItem(name, JSON.stringify(v)); } catch (e) { mt_log("localStorage unavailable", e.message); } }

async function position_save()
{
    var list = mt_store("mt_positions", []);
    var label = await mt_prompt("Label for this antenna position (e.g. balcony left, +30 deg)", "#" + (list.length + 1));
    if (label == null) return;
    var rec = { time: new Date().toLocaleString(), label: label };
    AL_METRICS.forEach(function (m) { var v = al_avg(m[0], 5000); if (!isNaN(v)) rec[m[0]] = +v.toFixed(1); });
    list.push(rec);
    mt_save("mt_positions", list);
    positions_render();
}

// best value per column highlighted (only when the column differs)
function records_table(list, cols, del_fn)
{
    var best = {};
    cols.forEach(function (c) {
        var vs = list.map(function (r) { return r[c]; }).filter(function (v) { return typeof v == "number"; });
        if (vs.length > 1 && Math.max.apply(null, vs) != Math.min.apply(null, vs)) best[c] = Math.max.apply(null, vs);
    });
    var html = "<tr><th>time</th><th>label</th>" + cols.map(function (c) {
        var m = AL_METRICS.filter(function (x) { return x[0] == c; })[0];
        return "<th>" + (m ? m[1].replace("RSRP ", "") : mt_esc(c)) + "</th>";
    }).join("") + "<th></th></tr>";
    list.forEach(function (r, i) {
        html += "<tr><td>" + mt_esc(r.time) + "</td><td>" + mt_esc(r.label) + "</td>" + cols.map(function (c) {
            var v = r[c] === undefined ? "" : r[c];
            return "<td>" + (v !== "" && v === best[c] ? "<b style='color:#3a3'>" + v + "</b>" : mt_esc(v)) + "</td>";
        }).join("") + "<td><a onclick='" + del_fn + "(" + i + ")'>x</a></td></tr>";
    });
    return html;
}

function positions_render() { if ($id("positions_table")) $id("positions_table").innerHTML = records_table(mt_store("mt_positions", []), POSITION_COLS, "position_delete"); }
function position_delete(i) { var l = mt_store("mt_positions", []); l.splice(i, 1); mt_save("mt_positions", l); positions_render(); }
function positions_clear() { if (confirm("Delete all saved positions?")) { mt_save("mt_positions", []); positions_render(); } }

function csv_text(list)
{
    var cols = [];
    list.forEach(function (r) { Object.keys(r).forEach(function (k) { if (cols.indexOf(k) < 0) cols.push(k); }); });
    return [cols.join(",")].concat(list.map(function (r) {
        return cols.map(function (c) { var v = r[c] === undefined ? "" : String(r[c]); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }).join(",");
    })).join("\n");
}

function csv_export(name, filename)
{
    var list = mt_store(name, []);
    if (!list.length) return mt_msg("nothing to export", true);
    var a = document.createElement("a");
    a.href = "data:text/csv;charset=utf-8," + encodeURIComponent(csv_text(list));
    a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
}

// ---------- cell evaluations / lock experiments (port of the ZTE script) ----------
// After a single-cell lock from this panel (or "Measure current cell"): wait until the modem serves the target cell,
// then average 60 s of monitor readings + lte1 traffic. No reboot on the MikroTik - the lock applies live, so only a
// short settle delay; if the target is lost the partial minute is dropped and it waits again until the deadline.
var EXP_COLS = ["lte_rsrp", "lte_sinr", "lte_rsrq", "nr_rsrp", "nr_sinr", "nr_rsrq", "rx_mbit", "tx_mbit"];
// [result column, monitor / monitor-traffic field]
var EXP_FIELDS = [["lte_rsrp", "rsrp"], ["lte_sinr", "sinr"], ["lte_rsrq", "rsrq"], ["nr_rsrp", "nr-rsrp"], ["nr_sinr", "nr-sinr"], ["nr_rsrq", "nr-rsrq"],
                  ["rx_mbit", "rx-bits-per-second"], ["tx_mbit", "tx-bits-per-second"]];
var EXP_SECS = 60, EXP_WAIT_MS = 3 * 60 * 1000, EXP_SETTLE_MS = 5000;
var exp_run = null;

// serving cell "earfcn:pci": LTE = primary (first LTE cell), NR = the n-band cell (ca-band in NSA, primary in SA)
function exp_serving(m, nr)
{
    var c = mt_parse_cells(m["primary-band"]).concat(mt_parse_cells(m["ca-band"])).filter(function (c) { return c.nr == !!nr && c.earfcn && c.pci; })[0];
    return c ? c.earfcn + ":" + c.pci : "";
}

function exp_state(html) { if ($id("exp_state")) $id("exp_state").innerHTML = html; }

function exp_arm(nr, earfcn, pci, label, delay_ms, now)
{
    now = now || Date.now();
    mt_save("mt_exp_pending", { nr: !!nr, target: earfcn + ":" + pci, label: label, armed: now, not_before: now + (delay_ms || 0) });
    exp_run = null;
    exp_state("armed: " + mt_esc(label));
}

async function exp_measure_now(nr)
{
    var t = exp_serving(mt.mon || {}, nr);
    if (!t) return mt_msg("no " + (nr ? "5G" : "LTE") + " serving cell reported right now", true);
    var a = t.split(":");
    var label = await mt_prompt("Label for this measurement", (nr ? "5G" : "LTE") + " PCI " + a[1] + " @ " + a[0]);
    if (label == null) return;
    exp_arm(nr, a[0], a[1], label);
}

function exp_cancel() { try { localStorage.removeItem("mt_exp_pending"); } catch (e) {} exp_run = null; exp_state("cancelled"); }

// called with every monitor reading (merged with monitor-traffic while a measurement is pending)
function exp_tick(m, now)
{
    var p = mt_store("mt_exp_pending", null);
    if (!p) return;
    if (now < p.not_before) return exp_state("settling after the lock ...");
    var cur = exp_serving(m, p.nr);
    if (!exp_run) {
        if (cur == p.target) exp_run = { start: now, sums: {}, ns: {} };
        else if (now - p.armed > EXP_WAIT_MS) return exp_finish(p, { result: "not on target within " + EXP_WAIT_MS / 60000 + " min" });
        else return exp_state("waiting for " + mt_esc(p.target) + " (now " + mt_esc(cur || "-") + ") ...");
    }
    if (cur != p.target) { exp_run = null; return exp_state("target lost - waiting again ..."); }
    EXP_FIELDS.forEach(function (f) {
        var v = mt_num(m[f[1]]);
        if (v == null) return;
        if (f[0].indexOf("mbit") > 0) v /= 1e6;
        exp_run.sums[f[0]] = (exp_run.sums[f[0]] || 0) + v;
        exp_run.ns[f[0]] = (exp_run.ns[f[0]] || 0) + 1;
    });
    var secs = Math.round((now - exp_run.start) / 1000);
    exp_state("measuring " + mt_esc(p.label) + ": " + secs + "/" + EXP_SECS + " s");
    if (secs >= EXP_SECS) {
        var rec = { result: "ok" };
        EXP_FIELDS.forEach(function (f) { if (exp_run.ns[f[0]]) rec[f[0]] = +(exp_run.sums[f[0]] / exp_run.ns[f[0]]).toFixed(f[0].indexOf("mbit") > 0 ? 2 : 1); });
        exp_finish(p, rec);
    }
}

function exp_finish(p, rec)
{
    rec.time = new Date().toLocaleString();
    rec.label = p.label + " (" + p.target + ")";
    var list = mt_store("mt_exp_results", []);
    list.push(rec);
    mt_save("mt_exp_results", list);
    try { localStorage.removeItem("mt_exp_pending"); } catch (e) {}
    exp_run = null;
    exp_state("last: " + mt_esc(rec.label) + " - " + mt_esc(rec.result));
    exp_render();
}

function exp_render() { if ($id("exp_table")) $id("exp_table").innerHTML = records_table(mt_store("mt_exp_results", []), EXP_COLS.concat(["result"]), "exp_delete"); }
function exp_delete(i) { var l = mt_store("mt_exp_results", []); l.splice(i, 1); mt_save("mt_exp_results", l); exp_render(); }
function exp_clear() { if (confirm("Delete all cell evaluations?")) { mt_save("mt_exp_results", []); exp_render(); } }

// ---------- UI ----------

// panel size: everything scales with the font size (A- / A+), minimize keeps only the header; both remembered
var MT_FS = [10, 22], MT_FS_DEFAULT = 14;
function mt_ui() { return mt_store("mt_ui", { fs: MT_FS_DEFAULT, min: false }); }
function mt_ui_apply(u)
{
    var d = $id("mt_panel");
    if (!d) return;
    d.style.fontSize = u.fs + "px";
    d.classList.toggle("mt_min", !!u.min);
    $id("mt_body").style.display = u.min ? "none" : "";
    $id("mt_min_link").textContent = u.min ? "expand" : "minimize";
}
function mt_ui_set(change) { var u = Object.assign(mt_ui(), change); mt_save("mt_ui", u); mt_ui_apply(u); }
function mt_toggle() { mt_ui_set({ min: !mt_ui().min }); }
function mt_zoom(step) { mt_ui_set({ fs: Math.min(MT_FS[1], Math.max(MT_FS[0], mt_ui().fs + step)), min: false }); }

function mt_inject()
{
    var d = document.createElement("div");
    d.id = "mt_panel";
    d.innerHTML = `
<style>
#mt_panel { position: fixed; top: 8px; right: 8px; z-index: 99999; width: 52em; max-width: calc(100vw - 16px); max-height: calc(100vh - 16px);
  overflow: auto; box-sizing: border-box; background: #fff; color: #222; border: 1px solid #889; border-radius: 6px; box-shadow: 0 2px 10px #0004;
  font: 14px/1.6 -apple-system, Segoe UI, sans-serif; padding: 0.4em 0.8em; }
#mt_panel.mt_min { width: auto; }
#mt_panel .mt_head { display: flex; gap: 0.6em; align-items: baseline; flex-wrap: wrap; } #mt_panel .mt_head .mt_right { margin-left: auto; white-space: nowrap; }
#mt_panel a { color: #06c; cursor: pointer; text-decoration: none; }
#mt_panel fieldset { border: 1px solid #ccd; border-radius: 4px; margin: 6px 0; padding: 4px 8px; }
#mt_panel label { white-space: nowrap; margin-right: 4px; }
#mt_panel input { all: revert; margin: 0 2px 0 0; vertical-align: middle; }   /* WebFig restyles inputs (hidden unchecked boxes) */
#mt_panel input, #mt_panel button, #mt_panel select { font-size: 0.9em; }
#mt_panel input[type=text] { width: 14em; }
#mt_panel details { margin: 6px 0; } #mt_panel summary { cursor: pointer; }
#mt_panel select { all: revert; font-size: 0.9em; }
.mt_t { border-collapse: collapse; margin: 3px 0; } .mt_t td, .mt_t th { padding: 0 6px; text-align: left; }
.mt_srv { background: #eef6ff; } .mt_note { color: #777; }
</style>
<div class=mt_head><b>MikroTik LTE Helper</b> <span id=mt_msg></span>
  <span class=mt_right><span id=mt_who></span> &middot; <a onclick="mt_zoom(-1)" title="smaller">A&minus;</a> <a onclick="mt_zoom(1)" title="bigger">A+</a>
  &middot; <a id=mt_min_link onclick="mt_toggle()">minimize</a></span></div>
<div id=mt_body>
  <div id=mt_sig>loading...</div>
  <fieldset><legend>Network mode / bands</legend><div id=mt_bands></div>
    <button onclick="mt_apply_bands()">Apply</button> <button onclick="mt_apply_bands(true)">All bands</button></fieldset>
  <fieldset><legend>Operator</legend><span id=mt_op_box>loading...</span>
    <div class=mt_note>Network the SIM registers on (local roaming). "Automatic" = the modem chooses; the current operator is shown at the top.</div></fieldset>
  <fieldset><legend>Cell lock &middot; <span id=mt_lock>?</span></legend>
    LTE earfcn,pci[;earfcn,pci] <input type=text id=mt_l4 placeholder="3150,341;1800,308"> <button onclick="mt_lock4()">Lock</button> <button onclick="mt_lock4(true)">Unlock</button><br>
    NR pci,arfcn,scs,band <input type=text id=mt_l5 placeholder="747,636576,30,78"> <button onclick="mt_lock5()">Lock</button> <button onclick="mt_lock5(true)">Unlock</button></fieldset>
  <fieldset><legend>Neighbour cells <button onclick="mt_scan()">Scan</button> <label><input type=checkbox id=mt_auto> auto 15 s</label> <span id=mt_nb_state class=mt_note></span></legend>
    <div id=mt_nb class=mt_note>LTE + NR cells the modem sees (cell-monitor)</div></fieldset>
  <details id=al_box><summary><b>Antenna alignment</b></summary>
    <label><input type=checkbox onchange="al.fast = this.checked"> Fast update (0.5 s)</label>
    <label><input type=checkbox onchange="tone_toggle(this.checked)"> Tone</label>
    <select onchange="tone_set_mode(this.value)">
      <option value="dual" selected>5G + LTE SINR</option>
      <option value="balance">5G rx0/rx1 balance</option>
      ${AL_METRICS.slice(0, 10).map(m => "<option value='m:" + m[0] + "'>" + m[1] + "</option>").join("")}
    </select>
    <select onchange="tone_set_res(this.value)" title="High / very high: pitch follows the change from the reference (Reset start/peak) - 2 or 4 semitones per dB">
      <option value="abs">Absolute</option><option value="high" selected>High res (2 st/dB)</option><option value="vhigh">Very high (4 st/dB)</option>
    </select><br>
    <a onclick="al_reset()">Reset start/peak</a> &middot; <a onclick="position_save()">Save position</a>
    <div class=mt_note>Tone: <span id=tone_legend>${tone_legend("dual")}</span>
      High / very high resolution: pitch is relative to the reference set by Reset start/peak (1 dB = 2 / 4 semitones).</div>
    <div class=mt_note>Tone targets (dB / dBm): <span id=tone_targets>${tone_targets_html()}</span></div>
    <div id=al_state style="color:#c60"></div>
    Antenna branches: <span id=al_branch></span>
    <table class=mt_t id=al_table></table>
  </details>
  <details><summary><b>Saved positions</b></summary>
    <a onclick="csv_export('mt_positions', 'mikrotik-positions.csv')">Export CSV</a> &middot; <a onclick="positions_clear()">Clear</a>
    <table class=mt_t id=positions_table></table>
  </details>
  <details><summary><b>Cell evaluations / lock experiments</b></summary>
    <a onclick="exp_measure_now(false)">Measure LTE cell (60 s)</a> &middot; <a onclick="exp_measure_now(true)">Measure 5G cell (60 s)</a> &middot;
    <a onclick="exp_cancel()">Cancel</a> &middot; <a onclick="csv_export('mt_exp_results', 'mikrotik-cell-tests.csv')">Export CSV</a> &middot; <a onclick="exp_clear()">Clear</a>
    <div><span id=exp_state class=mt_note></span></div>
    <div class=mt_note>A single-cell lock from this panel starts a 60 s measurement automatically once the modem is on the locked cell
      (no reboot - ~5 s settle; gives up after 3 min). rx/tx = traffic flowing at the time, not link capacity - run a speed test during the window to compare.</div>
    <table class=mt_t id=exp_table></table>
  </details>
</div>`;
    document.body.appendChild(d);
    mt_ui_apply(mt_ui());
}

async function mt_start()
{
    try {
        var l = (await mt_api("/interface/lte"))[0];
        if (!l) throw new Error("no LTE interface");
        mt.id = l[".id"]; MT_IF = l.name;
        mt.cap = (await mt_api("/interface/lte/show-capabilities", { ".id": MT_IF }))[0] || {};
        mt_render_bands(l);
        mt_log("ready: interface", MT_IF, mt.id, "modem bands LTE", mt.cap["lte-bands"], "NR", mt.cap["nr-bands"]);
    } catch (e) { return mt_msg("start: " + e.message, true); }
    mt.ready = true;
    mt_poll(); setInterval(mt_poll, 1000);
    mt_lock_status(); setInterval(mt_lock_status, 10000);
    setInterval(function () { if ($id("mt_auto").checked) mt_scan(); }, 15000);
}

function mt_init()
{
    if (window.mt_loaded || !document.body) return;
    window.mt_loaded = true;
    mt.auth = mt_auth_get();
    mt_log("init v2026-10-14-#16 at", location.href, "stored login:", !!mt.auth, "debug:", !!window.mt_debug, navigator.userAgent);
    mt_inject();
    mt_auth_ui();
    positions_render();
    exp_render();
    al_loop();
    mt_start();
}

if (typeof window !== "undefined" && typeof document !== "undefined") mt_init();
