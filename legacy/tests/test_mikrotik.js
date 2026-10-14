// Run: node tests/test_mikrotik.js mikrotik-script.js
// Parser / command-builder checks for the MikroTik helper. Inputs are real RouterOS 7.24 / RG520F answers.
const assert = require('assert');
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const grab = n => { const i = src.indexOf('function ' + n + '('); if (i < 0) throw n; let d = 0; for (let k = src.indexOf('{', i); k < src.length; k++) { if (src[k] == '{') d++; if (src[k] == '}' && --d == 0) return src.slice(i, k + 1); } };
for (const f of ['mt_num', 'mt_esc', 'mt_parse_cells', 'mt_parse_lock', 'mt_parse_pairs', 'mt_lock4_cmd', 'mt_lock5_cmd', 'mt_scs', 'mt_neighbours', 'mt_col', 'mt_val', 'mt_sig_html'])
  eval('global.' + f + '=' + grab(f).replace('function ' + f, 'function'));
let fails = 0; const t = (name, fn) => { try { fn(); console.log('ok   ', name); } catch (e) { fails++; console.log('FAIL ', name, '-', e.message); } };

const mon = { 'ca-band': 'n78@100Mhz earfcn: 649920 phy-cellid: 432,B3@20Mhz earfcn: 1800 phy-cellid: 341,B1@20Mhz earfcn: 500 phy-cellid: 341',
  'primary-band': 'B7@20Mhz earfcn: 3150 phy-cellid: 341', 'current-operator': 'Vivacom BG', 'data-class': '5G NSA', rsrp: '-85', rsrq: '-15', sinr: '0',
  'nr-rsrp': '-86', 'nr-rsrq': '-11', 'nr-sinr': '6', roaming: 'true' };

t('ca-band: NR + 2 LTE scells', () => assert.deepStrictEqual(mt_parse_cells(mon['ca-band']).map(c => [c.band, c.bw, c.earfcn, c.pci, c.nr]),
  [['n78', '100', '649920', '432', true], ['B3', '20', '1800', '341', false], ['B1', '20', '500', '341', false]]));
t('primary-band', () => assert.deepStrictEqual(mt_parse_cells(mon['primary-band'])[0], { band: 'B7', bw: '20', earfcn: '3150', pci: '341', nr: false }));
t('band without earfcn/pci, empty, undefined', () => {
  assert.deepStrictEqual(mt_parse_cells('B20@10Mhz'), [{ band: 'B20', bw: '10', earfcn: '', pci: '', nr: false }]);
  assert.deepStrictEqual(mt_parse_cells(''), []); assert.deepStrictEqual(mt_parse_cells(undefined), []);
});

t('lock 4g off', () => assert.deepStrictEqual(mt_parse_lock('+QNWLOCK: "common/4g",0\r\nOK'), { rat: '4g', on: false, cells: [] }));
t('lock 4g two cells', () => assert.deepStrictEqual(mt_parse_lock('+QNWLOCK: "common/4g",2,3150,341,1800,308\r\nOK').cells, [{ earfcn: 3150, pci: 341 }, { earfcn: 1800, pci: 308 }]));
t('lock 5g on, PCI 0 is a value', () => assert.deepStrictEqual(mt_parse_lock('+QNWLOCK: "common/5g",0,649920,30,78\r\nOK'),
  { rat: '5g', on: true, cells: [{ pci: 0, arfcn: 649920, scs: 30, band: 78 }] }));
t('lock 5g off', () => assert.strictEqual(mt_parse_lock('+QNWLOCK: "common/5g",0\r\nOK').on, false));
t('lock garbage -> null', () => assert.strictEqual(mt_parse_lock('ERROR'), null));

t('pairs ok, ; and space separated', () => assert.deepStrictEqual(mt_parse_pairs('3150,341; 1800,0'), [{ earfcn: 3150, pci: 341 }, { earfcn: 1800, pci: 0 }]));
t('pairs reject bad input', () => { for (const s of ['', '3150', '3150,', ',341', '3150,341,1', 'a,b', '3150,1008', '-1,5', '3150.5,341']) assert.strictEqual(mt_parse_pairs(s), null, s); });

t('lock4 cmd', () => { assert.strictEqual(mt_lock4_cmd([{ earfcn: 3150, pci: 341 }, { earfcn: 1800, pci: 308 }]), 'AT+QNWLOCK="common/4g",2,3150,341,1800,308');
  assert.strictEqual(mt_lock4_cmd([]), 'AT+QNWLOCK="common/4g",0'); });
t('lock5 cmd', () => { assert.strictEqual(mt_lock5_cmd({ pci: 432, arfcn: 649920, scs: 30, band: 78 }), 'AT+QNWLOCK="common/5g",432,649920,30,78');
  assert.strictEqual(mt_lock5_cmd(null), 'AT+QNWLOCK="common/5g",0'); });
t('lock5 cmd = known-good NSA terminal command', () => assert.strictEqual(mt_lock5_cmd({ pci: 747, arfcn: 636576, scs: mt_scs('n78'), band: 78 }), 'AT+QNWLOCK="common/5g",747,636576,30,78'));
t('scs', () => { assert.strictEqual(mt_scs('n78'), 30); assert.strictEqual(mt_scs(78), 30); assert.strictEqual(mt_scs('n3'), 15); assert.strictEqual(mt_scs('n28'), 15); });

const cm = [
  { '.section': '1', band: 'B3', earfcn: '1800', 'phy-cellid': '308', rsrp: '-90', rsrq: '-16' },
  { '.section': '3', band: 'B3', earfcn: '1800', 'phy-cellid': '308', rsrp: '-83', rsrq: '-16' },
  { '.section': '3', band: 'B7', earfcn: '3150', 'phy-cellid': '341', rsrp: '-85', rsrq: '-11' },
  { '.section': '3', band: 'n78/n77', earfcn: '649920', 'phy-cellid': '432', rsrp: '-85', rsrq: '-12' },
  { '.section': '3', band: 'B1', earfcn: '500', 'phy-cellid': '458', rsrp: '', rsrq: '-14' },
  { '.section': '3', band: 'B20', earfcn: '6300', 'phy-cellid': '7', rsrp: '-101', rsrq: '-9' }];
const nb = mt_neighbours(cm, mt_parse_cells(mon['primary-band'] + ',' + mon['ca-band']));
t('neighbours: newest section wins, strongest first, tie by RSRQ, missing last',
  () => assert.deepStrictEqual(nb.map(c => c.pci + ':' + c.rsrp), ['308:-83', '341:-85', '432:-85', '7:-101', '458:']));
t('neighbours: serving + NR flags', () => { assert.deepStrictEqual(nb.filter(c => c.serving).map(c => c.pci), ['341', '432']); assert.strictEqual(nb[2].nr, true); });
t('neighbours: empty', () => assert.deepStrictEqual(mt_neighbours([], []), []));

t('colours: neg/neg, zero, missing', () => { assert.strictEqual(mt_col('-75', -80, -105), '#080'); assert.strictEqual(mt_col('-110', -80, -105), '#c00');
  assert.strictEqual(mt_col('-90', -80, -105), '#c80'); assert.strictEqual(mt_col('0', 13, 0), '#c00'); assert.strictEqual(mt_col('', 13, 0), '#999'); });
t('signal html escapes and shows all cells', () => { const h = mt_sig_html(Object.assign({}, mon, { 'current-operator': '<x>' }));
  assert.ok(h.includes('&#60;x&#62;') && !h.includes('<x>')); assert.strictEqual((h.match(/<tr><td>[PS]<\/td>/g) || []).length, 4); assert.ok(h.includes('roaming')); });

// ---- antenna alignment ----
for (const f of ['al_parse', 'al_avg', 'al_feed', 'al_sparkline', 'al_branch_warning', 'al_table_html', 'records_table', 'csv_text', 'tone_range', 'tone_level', 'tone_voice'])
  eval('global.' + f + '=' + grab(f).replace('function ' + f, 'function'));
const gvar = re => eval(src.match(re)[0].replace(/^var /, 'global.'));
gvar(/var AL_METRICS = \[[\s\S]*?\n\];/); gvar(/var TONE_RES[^\n]*/); gvar(/var TONE_TARGET[^\n]*/);
Object.assign(global, { AL_HISTORY_MS: 300000, AL_AVG_MS: 3000, TONE_AVG_MS: 1500 });
const fresh = () => { global.al = { hist: {}, base: {}, peak: {}, tone_res: 'high' }; };

// real RG520F answers (NSA + LTE only); SA built from the Quectel QENG field order
const NSA = '+QENG: "servingcell","NOCONN"\r\n+QENG: "LTE","FDD",284,03,4B545,341,3150,7,5,5,44C,-80,-14,-46,14,13,-230,-\r\n' +
            '+QENG: "NR5G-NSA",284,03,432,-82,6,-11,649920,78,12,1\r\n+QRSRP: -79,-82,-140,-140,LTE\r\n+QRSRP: -98,-82,-91,-87,NR5G\r\nOK';
const LTE_ONLY = '+QENG: "servingcell","NOCONN","LTE","FDD",284,03,4B545,341,3150,7,5,5,44C,-79,-11,-48,13,12,-60,-\r\n+QRSRP: -79,-82,-140,-140,LTE\r\nOK';
const SA = '+QENG: "servingcell","NOCONN","NR5G-SA","TDD",284,03,1F2E3D,432,44C,649920,78,12,-85,-11,9,1,-\r\n+QRSRP: -85,-88,-32768,-32768,NR5G\r\nOK';
t('al_parse NSA: LTE + NR + 2 LTE ports + 4 NR rx', () => assert.deepStrictEqual(al_parse(NSA), { lte_rsrp: -80, lte_rsrq: -14, lte_sinr: 14, nr_rsrp: -82, nr_sinr: 6, nr_rsrq: -11,
  lte_p1: -79, lte_p2: -82, nr_rx0: -98, nr_rx1: -82, nr_rx2: -91, nr_rx3: -87 }));
t('al_parse LTE only (fields on the servingcell line), unused ports dropped', () => assert.deepStrictEqual(al_parse(LTE_ONLY), { lte_rsrp: -79, lte_rsrq: -11, lte_sinr: 13, lte_p1: -79, lte_p2: -82 }));
t('al_parse SA, -32768 dropped', () => assert.deepStrictEqual(al_parse(SA), { nr_rsrp: -85, nr_rsrq: -11, nr_sinr: 9, nr_rx0: -85, nr_rx1: -88 }));
t('al_parse SINR 0 kept, garbage -> {}', () => { assert.strictEqual(al_parse(NSA.replace('432,-82,6,', '432,-82,0,')).nr_sinr, 0);
  assert.deepStrictEqual(al_parse('ERROR'), {}); assert.deepStrictEqual(al_parse(undefined), {}); });

t('al_feed: 3 s average, start = first avg, peak only rises', () => { fresh(); const T = 1e6;
  al_feed({ nr_sinr: -2 }, T); al_feed({ nr_sinr: 4 }, T + 1000); al_feed({ nr_sinr: 1 }, T + 5000);
  assert.strictEqual(al.base.nr_sinr, -2); assert.strictEqual(al.peak.nr_sinr.v, 1); assert.strictEqual(al_avg('nr_sinr', 3000, T + 5000), 1);
  assert.ok(isNaN(al_avg('lte_sinr', 3000, T))); });
t('al_feed: history keeps 5 min only', () => { fresh(); al_feed({ nr_rsrp: -90 }, 0 + 1); al_feed({ nr_rsrp: -80 }, 300002); assert.strictEqual(al.hist.nr_rsrp.length, 1); });
t('table: delta sign/colour, no red -0.0, rows only for metrics with data', () => { fresh(); const T = 1e6;
  al_feed({ nr_rsrp: -90, lte_sinr: 3 }, T); al_feed({ nr_rsrp: -85, lte_sinr: 3 }, T + 4000);
  const rows = al_table_html(T + 4000).split('<tr>').slice(2);
  assert.strictEqual(rows.length, 2);
  assert.ok(rows[0].includes('5G RSRP') && rows[0].includes('#3a3') && rows[0].includes('+5.0'), rows[0]);
  assert.ok(rows[1].includes('LTE SINR') && rows[1].includes('#999') && rows[1].includes('>0.0<') && !rows[1].includes('#d44'), rows[1]); });
t('branch warning: 5G rx spread > 6 dB, balanced otherwise', () => { fresh(); const T = 1e6;
  al_feed(al_parse(NSA), T); const w = al_branch_warning(T);
  assert.ok(/5G rx differ by 16.0 dB/.test(w) && !/LTE ports/.test(w), w);
  fresh(); al_feed({ lte_p1: -80, lte_p2: -84 }, T); assert.ok(/balanced/.test(al_branch_warning(T))); });
t('sparkline needs 2 points, has the range in its title', () => { fresh(); al_feed({ nr_sinr: 2 }, 1000); assert.strictEqual(al_sparkline('nr_sinr', 1000), '');
  al_feed({ nr_sinr: 7 }, 2000); assert.ok(al_sparkline('nr_sinr', 2000).includes('<title>2 .. 7</title>')); });
t('tone targets and closeness (neg/pos, clamp)', () => { fresh(); const T = 1e6;
  assert.deepStrictEqual(tone_range('nr_sinr', true), [-10, 20]);   // MikroTik defaults (outdoor antenna) assert.deepStrictEqual(tone_range('nr_rx0'), [-120, -70]); assert.deepStrictEqual(tone_range('lte_rsrq'), [-20, -10]);
  al_feed({ nr_sinr: 5, lte_sinr: 30, nr_rsrp: -130 }, T);
  assert.strictEqual(tone_level('nr_sinr', true, T), 0.5); assert.strictEqual(tone_level('lte_sinr', true, T), 1); assert.strictEqual(tone_level('nr_rsrp', false, T), 0);
  const v = tone_voice('lte_sinr', 250, 650, true, T);
  assert.ok(v.done && Math.abs(v.f - Math.sqrt(250 * 650)) < 1e-9);   // high res: at the reference = middle pitch
  assert.strictEqual(tone_voice('nr_rx3', 400, 1200, false, T), null); });
t('saved positions: best per column, escaping; CSV quoting', () => {
  const list = [{ time: 't1', label: '<b>left</b>', nr_sinr: 3, lte_sinr: -1 }, { time: 't2', label: 'a,"b"', nr_sinr: 8, lte_sinr: -1 }];
  const h = records_table(list, ['nr_sinr', 'lte_sinr'], 'position_delete');
  assert.ok(h.includes("<b style='color:#3a3'>8</b>") && !h.includes("color:#3a3'>-1") && h.includes('&#60;b&#62;left') && !h.includes('<b>left'));
  assert.strictEqual(csv_text(list).split('\n')[2], 't2,"a,""b""",8,-1'); });

t('modem SEARCH (real answer, +QRSRP ERROR): state read, no values', () => {
  eval('global.al_modem_state=' + grab('al_modem_state').replace('function al_modem_state', 'function'));
  const out = '+QENG: "servingcell","SEARCH"\r\nERROR';
  assert.strictEqual(al_modem_state(out), 'SEARCH'); assert.deepStrictEqual(al_parse(out), {});
  assert.strictEqual(al_modem_state(NSA), 'NOCONN'); assert.strictEqual(al_modem_state(LTE_ONLY), 'NOCONN'); assert.strictEqual(al_modem_state(''), ''); });

// ---- cell evaluations / lock experiments ----
for (const f of ['exp_serving', 'exp_state', 'exp_arm', 'exp_tick', 'exp_finish', 'exp_render', 'mt_store', 'mt_save'])
  eval('global.' + f + '=' + grab(f).replace('function ' + f, 'function'));
gvar(/var EXP_COLS[^\n]*/); gvar(/var EXP_FIELDS[\s\S]*?\]\];/);
Object.assign(global, { EXP_SECS: 60, EXP_WAIT_MS: 180000, EXP_SETTLE_MS: 5000, exp_run: null });
let store = {}, dom = {};
global.localStorage = { getItem: k => k in store ? store[k] : null, setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
global.$id = id => (dom[id] = dom[id] || { innerHTML: '' });
const emon = (ca, extra) => Object.assign({ 'primary-band': 'B7@20Mhz earfcn: 3150 phy-cellid: 341', 'ca-band': ca, rsrp: '-85', sinr: '0', rsrq: '-12',
  'nr-rsrp': '-84', 'nr-sinr': '-3', 'nr-rsrq': '-11', 'rx-bits-per-second': '40000000', 'tx-bits-per-second': '0' }, extra || {});
const NSA_CA = 'n78@100Mhz earfcn: 649920 phy-cellid: 432,B3@20Mhz earfcn: 1800 phy-cellid: 341';
const results = () => JSON.parse(store.mt_exp_results || '[]');

t('exp_serving: LTE primary, NR from ca-band, SA primary, none', () => {
  assert.strictEqual(exp_serving(emon(NSA_CA), false), '3150:341'); assert.strictEqual(exp_serving(emon(NSA_CA), true), '649920:432');
  assert.strictEqual(exp_serving({ 'primary-band': 'n78@100Mhz earfcn: 636576 phy-cellid: 747' }, true), '636576:747');
  assert.strictEqual(exp_serving(emon(''), true), ''); assert.strictEqual(exp_serving({}, false), ''); });
t('exp: settle, wait for target, 60 s average incl. SINR 0 / negatives / Mbit, then done', () => {
  store = {}; dom = {}; global.exp_run = null; const T = 1e6;
  exp_arm(true, 649920, 432, '5G lock', 5000, T);
  exp_tick(emon('n78@100Mhz earfcn: 649920 phy-cellid: 21'), T + 1000); assert.ok(/settling/.test(dom.exp_state.innerHTML));
  exp_tick(emon('n78@100Mhz earfcn: 649920 phy-cellid: 21'), T + 6000); assert.ok(/waiting for 649920:432 \(now 649920:21\)/.test(dom.exp_state.innerHTML));
  for (let s = 0; s <= 60; s++) exp_tick(emon(NSA_CA, { sinr: s % 2 ? '1' : '-1', 'nr-sinr': '0' }), T + 10000 + s * 1000);
  const r = results(); assert.strictEqual(r.length, 1);
  assert.strictEqual(r[0].result, 'ok'); assert.strictEqual(r[0].label, '5G lock (649920:432)');
  assert.strictEqual(r[0].nr_sinr, 0); assert.strictEqual(r[0].lte_rsrp, -85); assert.strictEqual(r[0].rx_mbit, 40); assert.strictEqual(r[0].tx_mbit, 0);
  assert.ok(Math.abs(r[0].lte_sinr) <= 0.1); assert.strictEqual(store.mt_exp_pending, undefined); });
t('exp: target lost restarts the minute; gives up after the deadline', () => {
  store = {}; dom = {}; global.exp_run = null; const T = 1e6;
  exp_arm(false, 3150, 341, 'LTE lock', 0, T);
  for (let s = 0; s < 30; s++) exp_tick(emon(NSA_CA), T + s * 1000);
  exp_tick(emon(NSA_CA, { 'primary-band': 'B3@20Mhz earfcn: 1800 phy-cellid: 308' }), T + 31000);
  assert.ok(/target lost/.test(dom.exp_state.innerHTML) && results().length == 0);
  for (let s = 32; s < 92; s++) exp_tick(emon(NSA_CA), T + s * 1000);      // restarted at 32 s: 59 s so far
  assert.strictEqual(results().length, 0); exp_tick(emon(NSA_CA), T + 92000); assert.strictEqual(results().length, 1);
  store = {}; global.exp_run = null; exp_arm(false, 1, 2, 'never', 0, T);
  exp_tick(emon(NSA_CA), T + 180001); assert.strictEqual(results()[0].result, 'not on target within 3 min'); });

t('editable tone targets: load / set / reject, closeness follows, saved', () => {
  for (const f of ['tone_targets_load', 'tone_target_set', 'tone_targets_html']) eval('global.' + f + '=' + grab(f).replace('function ' + f, 'function'));
  gvar(/var TONE_TARGET = [^\n]*/); global.TONE_TARGET_DEFAULT = JSON.parse(JSON.stringify(TONE_TARGET)); gvar(/var TONE_TARGET_EDIT[\s\S]*?\]\];/);
  store = {};
  tone_targets_load({ dual_nr_sinr: 18, lte_sinr: 'abc', rsrq: -25, rsrp: '', nr_sinr: 0 });
  assert.deepStrictEqual(TONE_TARGET.dual_nr_sinr, [-10, 18]); assert.deepStrictEqual(TONE_TARGET.nr_sinr, [-10, 0]);         // 0 is a value
  assert.deepStrictEqual([TONE_TARGET.lte_sinr[1], TONE_TARGET.rsrq[1], TONE_TARGET.rsrp[1]], [20, -10, -70]);                // bad -> default
  for (const bad of ['', ' ', 'x', '-10', '-11']) assert.strictEqual(tone_target_set('dual_lte_sinr', bad), false, bad);       // floor -10
  assert.strictEqual(tone_target_set('nope', 5), false);
  assert.ok(tone_target_set('dual_lte_sinr', '-9') && tone_target_set('rsrp', -75));
  assert.deepStrictEqual(JSON.parse(store.mt_tone_targets), { dual_nr_sinr: 18, dual_lte_sinr: -9, nr_sinr: 0, lte_sinr: 20, rsrq: -10, rsrp: -75 });
  fresh(); al_feed({ lte_sinr: -8.5 }, 1e6); assert.strictEqual(tone_level('lte_sinr', true, 1e6), 1); assert.strictEqual(tone_voice('lte_sinr', 250, 650, true, 1e6).done, true);   // target -9 reached
  assert.ok(tone_targets_html().includes("value='-75'") && tone_targets_html().includes('defaults'));
  tone_targets_load({}); assert.deepStrictEqual(TONE_TARGET.dual_lte_sinr, [-10, 15]); });

t('operator: Bulgarian codes, validation, current selection, custom code kept, escaping', () => {
  for (const f of ['mt_operator_valid', 'mt_operator_html']) eval('global.' + f + '=' + grab(f).replace('function ' + f, 'function'));
  gvar(/var MT_OPERATORS[^\n]*/);
  assert.deepStrictEqual(MT_OPERATORS.map(o => o[0]), ['', '28401', '28403', '28405']);
  for (const ok of ['', '28403', '310260']) assert.ok(mt_operator_valid(ok), ok);
  for (const bad of ['2840', '2840333', '284-03', 'abc', ' 28403']) assert.ok(!mt_operator_valid(bad), bad);
  assert.ok(mt_operator_html('').includes("<option value='' selected>Automatic"));
  assert.ok(mt_operator_html('28405').includes("<option value='28405' selected>Yettel"));
  const h = mt_operator_html('20801'); assert.ok(h.includes("<option value='20801' selected>20801 (set on router)") && h.includes('setting: 20801'));
  assert.ok(!mt_operator_html("<x>").includes('<x>')); });

t('panel size: A-/A+ clamp 10..22 px, minimize/expand, remembered', () => {
  for (const f of ['mt_ui', 'mt_ui_apply', 'mt_ui_set', 'mt_toggle', 'mt_zoom']) eval('global.' + f + '=' + grab(f).replace('function ' + f, 'function'));
  gvar(/var MT_FS[^\n]*/); store = {};
  const els = { mt_panel: { style: {}, classList: { on: false, toggle(c, v) { this.on = v; } } }, mt_body: { style: {} }, mt_min_link: {} };
  global.$id = id => els[id];
  mt_ui_apply(mt_ui()); assert.strictEqual(els.mt_panel.style.fontSize, '14px');
  for (let i = 0; i < 20; i++) mt_zoom(1); assert.strictEqual(els.mt_panel.style.fontSize, '22px');
  for (let i = 0; i < 20; i++) mt_zoom(-1); assert.strictEqual(els.mt_panel.style.fontSize, '10px');
  mt_toggle(); assert.ok(els.mt_panel.classList.on && els.mt_body.style.display == 'none' && els.mt_min_link.textContent == 'expand');
  assert.deepStrictEqual(JSON.parse(store.mt_ui), { fs: 10, min: true });
  mt_zoom(1); assert.ok(!els.mt_panel.classList.on && els.mt_body.style.display == '' && els.mt_min_link.textContent == 'minimize'); });

// ---- REST login: a rejected login must not loop; a good one is saved only after the router accepts it ----
// async -> runs after the sync cases; the whole script is loaded in a vm sandbox with fake fetch / storage / dialogs
async function loginCase(name, routerPassword, typed, viaApp) {
  const vm = require('vm'), store = {}, asked = [];
  const el = () => ({ innerHTML: '', textContent: '', style: {}, checked: false });
  const sb = { console: { log() {} }, setTimeout() {}, setInterval() {}, clearTimeout() {}, clearInterval() {}, btoa: s => Buffer.from(s, 'binary').toString('base64'),
    atob: s => Buffer.from(s, 'base64').toString('binary'), escape, unescape, encodeURIComponent, decodeURIComponent,
    navigator: { userAgent: '' }, location: { href: 'http://r/webfig/' }, Date, JSON, Promise, Object, String, Number, Math, Array, Error, isNaN, parseFloat, parseInt,
    localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } },
    document: { body: null, getElementById: () => el(), querySelectorAll: () => [] } };
  sb.window = sb; sb.window.addEventListener = () => {};
  sb.window.pywebview = { api: { log() {}, ask: async m => { asked.push(m); return typed.shift() ?? null; } } };
  sb.window.app_ask = true;   // macOS app: dialogs via api.ask
  const good = 'Basic ' + Buffer.from('admin:' + routerPassword).toString('base64');
  sb.fetch = async (url, o) => (o.headers.Authorization === good
    ? { ok: true, status: 200, text: async () => '[{"name":"lte1"}]' } : { ok: false, status: 401, text: async () => '{"error":401,"message":"Unauthorized"}' });
  if (viaApp) {   // Mac app: Python makes the request (api.rest); fetch must not be used
    const f = sb.fetch; sb.fetch = () => { throw new Error('fetch used in app mode'); };
    sb.window.pywebview.api.rest = async (origin, m, path, body, auth) => { const r = await f(path, { headers: { Authorization: auth } }); return { status: r.status, text: await r.text() }; };
  }
  sb.location.origin = 'http://r';
  vm.createContext(sb); vm.runInContext(src, sb);
  let err = null; await vm.runInContext('mt_api("/interface/lte")', sb).catch(e => { err = e.message; });
  return { asked, store, err, stopped: vm.runInContext('mt.stopped', sb) };
}
const asyncCases = [
  ['wrong password: one user+password prompt, then stop - no loop, nothing saved', async () => {
    const r = await loginCase('wrong', 'right', ['admin', 'wrong', 'admin', 'wrong', 'admin', 'wrong']);
    assert.strictEqual(r.asked.length, 2, JSON.stringify(r.asked)); assert.ok(r.stopped && /rejected/.test(r.err)); assert.strictEqual(r.store.mt_auth, undefined); }],
  ['app mode (api.rest): wrong password stops after one prompt pair, right one is saved', async () => {
    const w = await loginCase('wrong', 'right', ['admin', 'wrong', 'admin', 'wrong'], true);
    assert.strictEqual(w.asked.length, 2); assert.ok(w.stopped && /rejected/.test(w.err));
    const r = await loginCase('ok', 'right', ['admin', 'right'], true);
    assert.strictEqual(r.err, null); assert.strictEqual(r.store.mt_auth, 'Basic ' + Buffer.from('admin:right').toString('base64')); }],
  ['right password: accepted and saved after the router said OK', async () => {
    const r = await loginCase('ok', 'right', ['admin', 'right']);
    assert.strictEqual(r.asked.length, 2); assert.strictEqual(r.err, null);
    assert.strictEqual(r.store.mt_auth, 'Basic ' + Buffer.from('admin:right').toString('base64')); }],
];
(async () => {
  for (const [name, fn] of asyncCases) { try { await fn(); console.log('ok   ', name); } catch (e) { fails++; console.log('FAIL ', name, '-', e.message); } }
  console.log(fails ? fails + ' FAILED' : 'ALL PASSED');
  process.exit(fails ? 1 : 0);
})();
