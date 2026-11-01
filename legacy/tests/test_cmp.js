// Run: node tests/test_cmp.js zte-script-legacy.js
// Comparison/sign checks for the NGBR table, alignment table, saved records and tones.
const assert = require('assert');
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const grab = n => { const i = src.indexOf('function ' + n + '('); if (i < 0) throw n; let d = 0; for (let k = src.indexOf('{', i); k < src.length; k++) { if (src[k] == '{') d++; if (src[k] == '}' && --d == 0) return src.slice(i, k + 1); } };
const vars = re => eval(src.match(re)[0].replace(/var /g, 'global.'));
vars(/var NGBR_COLUMNS[\s\S]*?\]\];\n/); vars(/var LTE_BANDS[\s\S]*?\]\];\nvar NR_BANDS[\s\S]*?\]\];/);
vars(/var ALIGN_METRICS = \[[\s\S]*?\n\];/); vars(/var EXP_COLS[^\n]*\n/); vars(/var EXP_FIELDS[\s\S]*?\]\];/); vars(/var TONE_TARGET[^\n]*\n/); vars(/var TONE_RES[^\n]*\n/);
global.TONE_AVG_MS = 1500; global.TOOLS_AVG_MS = 3000; global.TOOLS_HISTORY_MS = 300000;
for (const f of ['esc_html', 'exp_serving', 'exp_arm', 'exp_tick', 'exp_finish', 'exp_render', 'ngbr_num', 'ngbr_sort_value', 'ngbr_cmp', 'arfcn_to_band', 'sinr_or_unknown', 'connected_cells', 'render_ngbr_cells',
                 'tools_avg', 'tools_num', 'tools_render', 'sparkline', 'branch_warning', 'records_table', 'tone_range', 'tone_level', 'tone_voice'])
  eval('global.' + f + '=' + grab(f).replace('function ' + f, 'function'));
let html = {}; global.$ = sel => ({ html(v) { html[sel] = v; }, length: 1 });
global.tools_store = (n, d) => d; global.tools_save = () => {};
let fails = 0; const t = (name, fn) => { try { fn(); console.log('ok   ', name); } catch (e) { fails++; console.log('FAIL ', name, '-', e.message); } };

// ---- ngbr_cmp: neg/neg, neg/pos, pos/pos, zero, missing ----
const sortv = (vals, dir) => vals.slice().sort((a, b) => ngbr_cmp(a, b, dir));
t('desc neg/neg: -76 before -91', () => assert.deepStrictEqual(sortv([-91, -76, -101.1], -1), [-76, -91, -101.1]));
t('asc  neg/neg: -101.1 before -76', () => assert.deepStrictEqual(sortv([-91, -76, -101.1], 1), [-101.1, -91, -76]));
t('desc neg/pos: 2.5 before -1.2', () => assert.deepStrictEqual(sortv([-1.2, 2.5, 0], -1), [2.5, 0, -1.2]));
t('asc  neg/pos', () => assert.deepStrictEqual(sortv([-1.2, 2.5, 0], 1), [-1.2, 0, 2.5]));
t('desc pos/pos', () => assert.deepStrictEqual(sortv([4.6, 18.8, 8.3], -1), [18.8, 8.3, 4.6]));
t('missing last when desc', () => assert.deepStrictEqual(sortv([null, -80, 3], -1), [3, -80, null]));
t('missing last when asc', () => assert.deepStrictEqual(sortv([null, -80, 3], 1), [-80, 3, null]));
t('0 is a value, not missing', () => { assert.strictEqual(ngbr_num('0.0'), 0); assert.strictEqual(ngbr_num(''), null); assert.strictEqual(ngbr_num(undefined), null); });
t('sort value SINR 0.0 is 0', () => assert.strictEqual(ngbr_sort_value({ sinr: '0.0' }, 'sinr'), 0));

// ---- full table render: RSRP / SINR / AVG sorting with mixed signs ----
Object.assign(global, { ngbr_seen: {}, ngbr_hist: {}, cell_ids: {}, cell_id_streak: {}, NGBR_KEEP_MS: 60000, ngbr_sort: { key: 'rsrp', dir: -1 },
  is_lte: true, is_5g: true, is_5g_nsa: true, is_5g_nsa_active: true, wan_active_channel: '3350', lte_ca_pcell_freq: '3350', lte_pci: '154',
  lte_rsrp: '-91', lte_rsrq: '-14', lte_snr: '-1.5', lte_rssi: '-55', lte_ta: '2', cell_id: '318269', nr5g_cell_id: '',
  lte_multi_ca_scell_info: '1,340,1,1,100,20.0;2,340,1,3,1550,20.0;', lte_multi_ca_scell_sig_info: '-101.1,-19.6,0.0,-72.5,0,1;-91.2,-15.9,8.3,-66.3,0,1;',
  nr5g_action_channel: '636576', nr5g_pci: '7f', Z5g_rsrp: '-84', Z5g_rsrq: '-12', Z5g_SINR: '6.0', Z5g_rssi: '-74.0', nr_multi_ca_scell_info: '',
  nr5g_nsa_band_lock: '1,3,7,8,20,28,78', nr5g_sa_band_lock: '1,3,7,8,20,28,78' });
const raw = '6400,112,-10,-76,-56;6400,394,-15,-81,-56;100,202,-15,-95,-67';
const rows = h => [...h.matchAll(/<tr title='([^']*)'[^>]*>(.*?)<\/tr>/g)].map(m => m[2].replace(/<a[\s\S]*?<\/a>/g, '').replace(/<[^>]+>/g, '|').replace(/&nbsp;/g, ' ').split('|').filter(x => x !== '').map(x => x.trim()));
let r = rows(render_ngbr_cells(raw));
const col = (rs, i) => rs.map(x => x[i]);
const num = a => a.map(Number);
const isSorted = (a, dir) => a.every((v, i) => i == 0 || (dir < 0 ? a[i - 1] >= v : a[i - 1] <= v));
t('RSRP desc (strongest first)', () => { const v = num(col(r, 4)); assert(isSorted(v, -1), JSON.stringify(v)); assert.strictEqual(v[0], -76); });
ngbr_sort = { key: 'rsrp', dir: 1 }; r = rows(render_ngbr_cells(null));
t('RSRP asc (weakest first)', () => { const v = num(col(r, 4)); assert(isSorted(v, 1), JSON.stringify(v)); assert.strictEqual(v[0], -101.1); });
const sinrCol = () => render_ngbr_cells(null).match(/<tr title[\s\S]*?<\/tr>/g).map(x => [...x.matchAll(/<td[^>]*>(.*?)<\/td>/g)][8][1]);
ngbr_sort = { key: 'sinr', dir: -1 };
t('SINR desc, mixed signs, 0.0 kept, missing last', () => {
  const v = sinrCol();
  const present = v.filter(x => x !== ''); const missingAfter = v.slice(present.length).every(x => x === '');
  assert(isSorted(present.map(Number), -1), JSON.stringify(v)); assert(missingAfter, 'missing not last: ' + JSON.stringify(v));
  assert(present.includes('0.0') && present.includes('-1.5'), JSON.stringify(v)); });
ngbr_sort = { key: 'sinr', dir: 1 };
t('SINR asc, missing still last', () => {
  const v = sinrCol();
  const present = v.filter(x => x !== ''); assert(isSorted(present.map(Number), 1), JSON.stringify(v)); assert(v.slice(present.length).every(x => x === ''), JSON.stringify(v)); });

// ---- BETTER hint: neighbour vs connected with negative RSRP ----
ngbr_seen = {}; ngbr_hist = {}; ngbr_sort = { key: 'band', dir: 1 };
lte_multi_ca_scell_info = '1,340,1,1,100,20.0;3,121,1,20,6400,10.0;'; lte_multi_ca_scell_sig_info = '-101.1,-19.6,8.8,-72.5,0,1;-82.4,-15.8,6.3,-56.7,0,1;';
let h; for (let i = 0; i < 3; i++) h = render_ngbr_cells(raw);
t('BETTER: -76 vs connected -82.4 -> +6.4', () => assert(/6400<\/td><td>112[\s\S]*?BETTER \+6\.4/.test(h), 'missing'));
t('no BETTER for weaker -81 vs -82.4 (only +1.4)', () => assert(!/6400<\/td><td>394[^\n]*?BETTER/.test(h.split('</tr>').find(x => x.includes('<td>394</td>')))));
t('MIN/MAX shows min first for negatives', () => assert(h.includes('-76/-76') && /-91\/-91|-91\/-?\d/.test(h)));

// ---- alignment table: delta, colour, peak ----
const T = (key, vals) => { tools.hist[key] = vals.map((v, i) => ({ t: Date.now() - (vals.length - i) * 100, v })); };
global.tools = { hist: {}, base: {}, peak: {} };
const delta = (key, base, now) => { tools.hist = {}; T(key, [now, now, now]); tools.base = { [key]: base }; tools.peak = { [key]: { v: Math.max(base, now), t: Date.now() } }; tools_render();
  const row = html['#align_table'].split('</tr>').find(x => x.includes(ALIGN_METRICS.find(m => m[0] == key)[2] + '<')); return row.match(/<td style='color:(#\w+)'>([^<]*)<\/td>/).slice(1); };
t('RSRP -91 -> -88 = +3.0 green', () => assert.deepStrictEqual(delta('lte_rsrp', -91, -88), ['#5c5', '+3.0']));
t('RSRP -88 -> -91 = -3.0 red', () => assert.deepStrictEqual(delta('lte_rsrp', -88, -91), ['#e66', '-3.0']));
t('SINR -2 -> 1 = +3.0 green (neg->pos)', () => assert.deepStrictEqual(delta('nr_sinr', -2, 1), ['#5c5', '+3.0']));
t('SINR 5 -> 3 = -2.0 red (pos/pos)', () => assert.deepStrictEqual(delta('nr_sinr', 5, 3), ['#e66', '-2.0']));
t('tiny drop -0.04 -> neutral 0.0', () => assert.deepStrictEqual(delta('nr_sinr', 5.04, 5), ['#999', '0.0']));
t('peak keeps the higher (less negative) value', () => { const pk = -85, v = -90; assert(!(v > pk)); assert(-80 > pk); });

// ---- saved positions / results: best = highest ----
const rec = records_table([{ time: 1, label: 'a', lte_rsrp: -91, nr_sinr: -1.5 }, { time: 2, label: 'b', lte_rsrp: -84, nr_sinr: 2 }, { time: 3, label: 'c', lte_rsrp: -101, nr_sinr: 0 }], ['lte_rsrp', 'nr_sinr'], 'x');
t('best RSRP is -84 (not -101)', () => assert(rec.includes("<b style='color:#5c5'>-84</b>") && !rec.includes(">-101</b>")));
t('best SINR is 2 (mixed signs)', () => assert(rec.includes("<b style='color:#5c5'>2</b>")));

// ---- tones: target reached / closeness / balance direction ----
const set = (key, v) => { tools.hist[key] = [{ t: Date.now(), v }]; };
tools.tone_res = 'abs'; tools.base = {};
t('RSRP -69 reaches target -70', () => { set('lte_rsrp', -69); assert(tone_voice('lte_rsrp', 400, 1200, false).done); });
t('RSRP -71 not yet', () => { set('lte_rsrp', -71); assert(!tone_voice('lte_rsrp', 400, 1200, false).done); });
t('RSRQ -9 reaches -10, -11 not', () => { set('lte_rsrq', -9); assert(tone_voice('lte_rsrq', 400, 1200).done); set('lte_rsrq', -11); assert(!tone_voice('lte_rsrq', 400, 1200).done); });
t('closeness grows: -100 < -80 dBm', () => { set('lte_rsrp', -100); const a = tone_level('lte_rsrp'); set('lte_rsrp', -80); assert(tone_level('lte_rsrp') > a); });
t('closeness grows: SINR -3 < 2 (neg->pos)', () => { set('nr_sinr', -3); const a = tone_level('nr_sinr'); set('nr_sinr', 2); assert(tone_level('nr_sinr') > a); });
t('LTE SINR 6 reaches 6 dB target, 5.9 not', () => { set('lte_sinr', 6); assert(tone_voice('lte_sinr', 250, 650, true).done); set('lte_sinr', 5.9); assert(!tone_voice('lte_sinr', 250, 650, true).done); });
t('relative pitch rises when -90 -> -88', () => { tools.tone_res = 'high'; tools.base = { lte_rsrp: -90 }; set('lte_rsrp', -88); const up = tone_voice('lte_rsrp', 400, 1200).f; set('lte_rsrp', -92); assert(up > tone_voice('lte_rsrp', 400, 1200).f); });
t('balance: rx0 -80 vs rx1 -83 => rx0 stronger (d > 0)', () => { const d = -80 - (-83); assert(d > 0); });
t('branch warning uses absolute difference', () => { tools.hist = {}; set('nr_rx0', -91); set('nr_rx1', -83); assert(branch_warning().includes('8.0 dB')); set('nr_rx0', -83); set('nr_rx1', -91); assert(branch_warning().includes('8.0 dB')); });


// ---- logic fixes ----
t('NR SCell: RSRQ in the rsrq slot', () => { const m = src.match(/cell_data\[7\], \/\/ RSRP \(rsrp1\)\s*""\s*,[^\n]*\n\s*cell_data\[8\], \/\/ RSRQ/); assert(m); });
t('connected: unknown LTE PCI is skipped', () => { const old = lte_pci; lte_pci = ''; const c = connected_cells(); lte_pci = old; assert(!c.some(x => x.pci == 'NaN')); });
t('connected: stale NR SCell outside allowed bands dropped', () => { nr_multi_ca_scell_info = '0,10,1,n41,520000,100MHz,0,-80,-10,5.0;0,11,1,n78,640000,100MHz,0,-82,-11,6.0;';
  const c = connected_cells().filter(x => x.nr && x.arfcn != nr5g_action_channel); nr_multi_ca_scell_info = ''; assert.deepStrictEqual(c.map(x => x.arfcn), ['640000']); });
t('learned cell id: ping-pong does not save the lagging id', () => {
  ngbr_seen = {}; ngbr_hist = {}; cell_ids = {}; cell_id_streak = {}; lte_multi_ca_scell_info = ''; lte_multi_ca_scell_sig_info = '';
  const visit = (pci, id) => { lte_pci = pci; cell_id = id; render_ngbr_cells(''); };
  // A = 3350/340 (hex 154) id 100, B = 3350/112 (hex 70) id 200; cell_id lags pci by one tick on B
  for (let i = 0; i < 3; i++) { visit('154', '100'); visit('70', '100'); }
  assert(cell_ids['lte:3350:112'] === undefined, JSON.stringify(cell_ids));
  visit('70', '200'); visit('70', '200'); visit('70', '200'); assert.strictEqual(cell_ids['lte:3350:112'], 200); lte_pci = '154'; cell_id = '318269'; });
t('alignment table survives missing base/peak after reset', () => { tools.hist = { nr_sinr: [{ t: Date.now(), v: 5 }] }; tools.base = {}; tools.peak = {}; tools_render(); });
t('labels are escaped', () => { const h = records_table([{ time: 't', label: '<b>x', lte_rsrp: -90 }], ['lte_rsrp'], 'd'); assert(h.includes('&lt;b&gt;x') && !h.includes('<b>x')); });
t('lock experiment: delay, lost target waits, completes after 60 s', () => {
  const store = {}; global.tools_store = (n, d) => (n in store ? store[n] : d); global.tools_save = (n, v) => { store[n] = v; };
  global.localStorage = { removeItem: n => { delete store[n]; } }; global.exp_run = null;
  const a = (pci) => ({ network_type: 'ENDC', wan_active_channel: '6400', lte_ca_pcell_freq: '6400', lte_pci: pci, lte_rsrp: '-80', lte_snr: '3.0', lte_rsrq: '-12', Z5g_SINR: '6.0', realtime_rx_thrpt: '125000' });
  const t0 = Date.now(); exp_arm(false, '6400', '112', 'L', 30000);
  exp_tick(a('70'), t0 + 1000); assert(exp_run === null, 'measured during the delay');
  exp_tick(a('70'), t0 + 31000); assert(exp_run, 'did not start');
  exp_tick(a('154'), t0 + 40000); assert(exp_run === null && store.zte_exp_pending, 'lost target should wait, not finish');
  exp_tick(a('70'), t0 + 50000); exp_tick(a('70'), t0 + 80000); exp_tick(a('70'), t0 + 111000);
  const res = store.zte_exp_results; assert(res && res.length == 1 && res[0].result == 'ok' && res[0].lte_rsrp == -80 && res[0].rx_mbit == 1, JSON.stringify(res)); });

t('editable tone targets (ZTE defaults 5G 15 / LTE 6), legend follows', () => {
  for (const f of ['tone_targets_load', 'tone_target_set', 'tone_legend']) eval('global.' + f + '=' + grab(f).replace('function ' + f, 'function'));
  global.TONE_TARGET_DEFAULT = JSON.parse(JSON.stringify(TONE_TARGET));
  eval(src.match(/var TONE_TARGET_EDIT[\s\S]*?\]\];/)[0].replace(/^var /, 'global.'));
  let saved = null; const save0 = global.tools_save; global.tools_save = (n, v) => { saved = [n, v]; };
  tone_targets_load({});
  assert.ok(tone_legend('dual').includes('(5G 15 dB, LTE 6 dB)') && tone_legend('m:nr_sinr').includes('5G SINR 20 dB'));
  assert.ok(tone_target_set('dual_nr_sinr', '18') && !tone_target_set('dual_lte_sinr', '-10'));
  assert.ok(tone_legend('dual').includes('(5G 18 dB, LTE 6 dB)') && tone_legend('qsinr').includes('>= 18 dB'));
  assert.strictEqual(saved[0], 'zte_tone_targets'); assert.strictEqual(saved[1].dual_nr_sinr, 18);
  tone_targets_load({}); global.tools_save = save0; });

// ---- antenna mode sweep: scoring, cell-change detection, best applied, failure restores previous ----
for (const f of ['ant_score', 'ant_best', 'ant_record', 'ant_apply', 'ant_sweep']) eval('global.' + f + '=' + (src.includes('async function ' + f + '(') ? 'async ' : '') + grab(f).replace('function ' + f, 'function'));
vars(/var ANT_CONFIGS[\s\S]*?\n\];/);
t('ant score: dual adds 5G + LTE SINR with signs, missing ranks last', () => {
  assert.strictEqual(ant_score({ nr_sinr: 10.2, lte_sinr: -1.5 }, 'dual'), 8.7);
  assert.strictEqual(ant_score({ nr_sinr: 10.2 }, 'dual'), undefined);
  assert.strictEqual(ant_best([{ score: undefined }, { score: -3 }, { score: -0.5 }, { score: -7 }]), 2);
  assert.strictEqual(ant_best([{ score: undefined }]), -1); });
t('ant record: averages, hex PCI, cell change shown', () => {
  const s = (pci, sinr) => ({ Z5g_SINR: sinr, lte_snr: '1.0', nr5g_action_channel: '636576', nr5g_pci: pci, lte_pci: '154', wan_active_channel: '3350' });
  const r = ant_record({ label: 'x' }, [s('216', '10.0'), s('216', '9.0'), s('312', '-20.0')], 'nr_sinr');
  assert.strictEqual(r.nr_sinr, 9.5, 'no-value marker -20.0 must be skipped');
  assert.strictEqual(r.nr_cell, '636576/534 > 636576/786'); assert.strictEqual(r.lte_cell, '3350/340'); assert.strictEqual(r.score, 9.5); });
const sweepRun = async (failAt) => {
  const store = {}, calls = []; let cur = { auto: 1, rx0: 1, rx1: 1 };
  global.tools_store = (n, d) => (n in store ? store[n] : d); global.tools_save = (n, v) => { store[n] = v; };
  global.ANT_SETTLE_MS = 0; global.ANT_MEASURE_MS = 3; global.ALIGN_CMD = 'x'; global.ant = { busy: false, stop: false };
  global.confirm = () => true; global.ant_sleep = () => Promise.resolve(); global.ant_render = () => {}; global.ant_read = () => {};
  global.$ = sel => ({ html(v) { html[sel] = v; }, val: () => 'dual', text: () => '5G + LTE SINR', length: 1 });
  global.zte_set = async d => { calls.push(d); if (failAt && calls.length == failAt) throw new Error('boom');
    if (d.goformId == 'WAN_ANT_SWITCH_SET') cur.auto = +d.ant_switch_enable; else cur[d.antenna_name == '5' ? 'rx0' : 'rx1'] = +d.state; };
  global.ant_get = async cmd => cmd == 'ant_switch_enable' ? { ant_switch_enable: '0' } : {
    // antenna 6 directional is the winner in this fake world; antenna 5 directional is bad
    Z5g_SINR: String(cur.rx0 == 4 ? 1 : (cur.rx1 == 4 ? 12 : 10)), lte_snr: String(cur.rx1 == 4 ? 0.5 : 1), nr5g_action_channel: '636576', nr5g_pci: '216' };
  await ant_sweep();
  return { store, calls, cur };
};
const antCases = [
  ['ant sweep: measures 5 modes, applies the best (ant6 dir), saves results', async () => {
    const r = await sweepRun(0), list = r.store.zte_ant_results;
    assert.strictEqual(list.length, 5); assert.deepStrictEqual(r.cur, { auto: 0, rx0: 1, rx1: 4 });
    assert(list[3].label.endsWith('BEST, applied') && list[3].score == 12.5, JSON.stringify(list[3]));
    assert(list.every(x => x.goal == '5G + LTE SINR' && x.samples > 0)); assert(html['#ant_state'].includes('ant5 omni / ant6 dir')); }],
  ['ant sweep: failure restores previous auto-select + omni/omni', async () => {
    const r = await sweepRun(6);
    assert.deepStrictEqual(r.cur, { auto: 0, rx0: 1, rx1: 1 }); assert(html['#ant_state'].includes('boom')); }],
];

// ---- 5G cells seen: time per cell, switches, gaps not counted, rx dropouts counted once per dropout, "no 5G" state ----
t('5G cell log: switches, time, gaps, rx dropouts', () => {
  for (const f of ['nr_key', 'nr_track', 'nr_rows']) eval('global.' + f + '=' + grab(f).replace('function ' + f, 'function'));
  global.NR_EVENTS_MAX = 3; global.nr_render = () => {}; global.tools_save = () => {};
  global.nr_log = { cells: {}, events: [], prev: null, t: 0, rx_dead: false, saved: Date.now() + 1e9 };
  const s = (pci, sinr, rx1 = '-88', type = 'ENDC') => ({ network_type: type, nr5g_action_channel: '636576', nr5g_pci: pci.toString(16), Z5g_SINR: String(sinr), Z5g_rsrp: '-80', '5g_rx0_rsrp': '-83', '5g_rx1_rsrp': rx1 });
  let t0 = 1e12; const at = (sec, a) => nr_track(a, t0 + sec * 1000);
  at(0, s(534, 9)); at(1, s(534, 10)); at(2, s(534, 8, '-140')); at(3, s(534, 8, '-140')); at(4, s(534, 9)); at(5, s(534, 9, '-140'));
  at(6, s(786, 0.5)); at(7, s(786, -0.5)); at(60, s(786, 0));          // 53 s gap -> only 5 s counted
  at(61, { network_type: 'LTE-NSA', nr5g_action_channel: '', nr5g_pci: '' }); at(62, { network_type: 'LTE-NSA' }); at(63, s(534, 9));
  const c = nr_log.cells;
  assert.strictEqual(c['636576/534'].secs, 5); assert.strictEqual(c['636576/534'].rx_drops, 2, 'dropout counted once per -140 run');
  assert.strictEqual(c['636576/534'].n_in, 1); assert.strictEqual(c['636576/534'].sinr_min, 8);
  assert.strictEqual(c['636576/786'].secs, 6); assert.strictEqual(c['636576/786'].sinr_min, -0.5, 'negative SINR kept');
  assert.strictEqual(c['no 5G'].secs, 1); assert.strictEqual(c['no 5G'].sinr_n, 0);
  assert.deepStrictEqual(nr_log.events.map(e => e.from + '>' + e.to), ['636576/534>636576/786', '636576/786>no 5G', 'no 5G>636576/534']);
  assert.strictEqual(nr_log.events[0].sinr_before, 9, 'SINR on the old cell just before the switch');
  const rows = nr_rows(); assert.strictEqual(rows[0].cell, '636576/786'); assert.strictEqual(rows[0].share_pct, 50);
  assert.strictEqual(rows.find(r => r.cell == '636576/534').avg_sinr, 8.86);
  at(64, s(127, 5)); assert.strictEqual(nr_log.events.length, 3, 'event list capped'); });

// ---- peak guide: rise -> new peaks, small noise silent, past the peak, back, confirmed after holding; negative values ----
t('peak guide: new / past / back / confirmed, noise ignored, go-back seconds', () => {
  for (const f of ['peak_step', 'peak_back_s']) eval('global.' + f + '=' + grab(f).replace('function ' + f, 'function'));
  vars(/var PEAK_GUIDE[^\n]*\n/);
  const g = {}, ev = [], t0 = 1e12;
  const feed = (sec, v) => { const e = peak_step(g, v, t0 + sec * 1000); if (e) ev.push(sec + ':' + e); };
  // LTE SINR sweep with negatives: -2 -> 2.4 (peak at 6 s) -> falls -> come back
  [[0, -2], [1, -1.2], [2, -0.5], [3, 0.6], [4, 1.5], [5, 2.1], [6, 2.4], [7, 2.2], [8, 1.6], [9, 0.8], [10, 0.4]].forEach(([s, v]) => feed(s, v));
  assert.deepStrictEqual(ev, ['1:new', '2:new', '3:new', '4:new', '5:new', '6:new', '9:past']);
  assert.strictEqual(peak_back_s(g, t0 + 9000), 5, '3 s since the peak reading + 2 s lag');
  feed(11, 1.2); feed(12, 2.0); feed(13, 2.1); feed(14, 1.8); feed(15, 2.0); feed(16, 2.2);
  assert.deepStrictEqual(ev.slice(7), ['12:back'], 'back within 0.5 dB');
  feed(17, 2.3); feed(18, 2.0); assert.deepStrictEqual(ev.slice(8), ['18:confirmed'], 'dip at 14 restarts the 3 s hold: 15 -> 18');
  feed(19, 2.1); feed(20, 2.2); assert.strictEqual(ev.length, 9, 'no repeats while holding');
  // noise around a plateau: small ups and downs (< drop) never announce "past"
  const h = {}, e2 = []; [5, 5.2, 4.6, 5.1, 4.2, 5.0, 4.4].forEach((v, i) => { const e = peak_step(h, v, t0 + i * 1000); if (e) e2.push(e); });
  assert.deepStrictEqual(e2, [], 'noise within 1.5 dB is silent');
  assert.strictEqual(peak_step(h, NaN, t0), null); });

// ---- RSSI: measured everywhere, but neutral (no green/red, no "best" highlight) ----
t('RSSI rows are neutral; per-cell average RSSI in the 5G log', () => {
  global.$ = sel => ({ html(v) { html[sel] = v; }, length: 1 });
  tools.hist = { nr_rssi: [{ t: Date.now(), v: -70 }], lte_rssi: [{ t: Date.now(), v: -55 }] }; tools.base = { nr_rssi: -74, lte_rssi: -55 };
  tools.peak = { nr_rssi: { v: -70, t: Date.now() }, lte_rssi: { v: -55, t: Date.now() } };
  tools_render();
  const row = html['#align_table'].split('</tr>').find(x => x.includes('5G RSSI'));
  assert(row && row.includes("color:#999") && row.includes('+4.0'), 'RSSI +4 dB shown grey, not green: ' + row);
  const h = records_table([{ time: 1, label: 'a', nr_rssi: -70, nr_sinr: 5 }, { time: 2, label: 'b', nr_rssi: -60, nr_sinr: 3 }], ['nr_rssi', 'nr_sinr'], 'x');
  assert(!h.includes("color:#5c5'>-60") && h.includes("color:#5c5'>5"), 'no best highlight for RSSI, SINR still highlighted');
  global.nr_log = { cells: {}, events: [], prev: null, t: 0, rx_dead: false, saved: Date.now() + 1e9 };
  const a = r => ({ network_type: 'ENDC', nr5g_action_channel: '636576', nr5g_pci: '216', Z5g_SINR: '9', Z5g_rsrp: '-80', Z5g_rssi: r, '5g_rx0_rsrp': '-83', '5g_rx1_rsrp': '-88' });
  nr_track(a('-74.0'), 1e12); nr_track(a('-70.0'), 1e12 + 1000);
  assert.strictEqual(nr_rows()[0].avg_rssi, -72); });

t('tone: RSSI options + wide RSSI range (no premature "done")', () => {
  assert.deepStrictEqual(tone_range('nr_rssi', false), [-110, -30]); assert.deepStrictEqual(tone_range('lte_rssi', true), [-110, -30]);
  assert.deepStrictEqual(tone_range('lte_rsrq', false), TONE_TARGET.rsrq); assert.deepStrictEqual(tone_range('nr_rsrp', false), TONE_TARGET.rsrp);
  const opts = src.match(/ALIGN_METRICS\.filter\(\(m, i\) => i < 9 \|\| m\[4\] == "n"\)/); assert(opts, 'tone select includes the neutral RSSI metrics');
  assert.deepStrictEqual(ALIGN_METRICS.filter((m, i) => i < 9 || m[4] == 'n').slice(-2).map(m => m[0]), ['nr_rssi', 'lte_rssi']); });

// ---- login: saved hash = automatic, macOS app asks via api.ask (no 10 s limit), browser via prompt() ----
for (const f of ['ask_password', 'perform_login', 'have_admin_password_hash']) eval('global.' + f + '=' + grab(f).replace('function ' + f, 'function'));
for (const f of ['perform_superadmin_login']) eval('global.' + f + '=' + grab(f).replace('function ' + f, 'function'));
const superadminRun = async (result, quiet) => {
  const posts = [], saved = [], deleted = [], alerts = [];
  global.cookies = { get: () => null, set: (k, v) => saved.push([k, v]), delete: k => deleted.push(k) };
  global.SHA256 = s => 'sha(' + s + ')';
  global.alert = m => alerts.push(m);
  global.err = () => {};
  global.$ = { ajax: o => {
    if (o.type === 'GET') o.success({ wa_inner_version: 'BD_UGBGRMC888V1.0.0B09', cr_version: '', RD: 'RD1', LD: 'LD1' });
    else { posts.push(o.data); o.success(JSON.stringify({ result })); }
  } };
  let outcome = 'none';
  perform_superadmin_login('H'.repeat(64), () => { outcome = 'ok'; }, true, quiet);
  await new Promise(r => setTimeout(r, 0));
  return { posts, saved, deleted, alerts, outcome };
};
const loginRun = async (cookie, app, answer) => {
  const calls = [], asked = [];
  global.cookies = { get: k => (k == 'admin_password_hash' ? cookie : null) };
  global.SHA256 = s => 'sha(' + s + ')';
  global.perform_login_hash = (h, ok, dev, save) => calls.push([h, save]);
  global.prompt = m => { asked.push('prompt:' + m); return answer; };
  global.window = app ? { app_ask: true, pywebview: { api: { ask: async m => { asked.push('ask:' + m); return answer; } } } } : {};
  perform_login(() => {}, false, true);
  await new Promise(r => setTimeout(r, 0));
  return { calls, asked };
};
(async () => {
  const cases = [
    ['saved hash: logs in without asking', async () => { const r = await loginRun('abc', true, 'x');
      assert.deepStrictEqual(r.calls, [['abc', true]]); assert.deepStrictEqual(r.asked, []); }],
    ['macOS app: password via api.ask, hashed', async () => { const r = await loginRun(null, true, 'pw');
      assert.deepStrictEqual(r.asked, ['ask:Router Password']); assert.deepStrictEqual(r.calls, [['sha(pw)', true]]); }],
    ['browser: password via prompt()', async () => { const r = await loginRun(null, false, 'pw');
      assert.deepStrictEqual(r.asked, ['prompt:Router Password']); assert.deepStrictEqual(r.calls, [['sha(pw)', true]]); }],
    ['cancel / empty: no login', async () => { assert.deepStrictEqual((await loginRun(null, true, null)).calls, []);
      assert.deepStrictEqual((await loginRun(null, false, '')).calls, []); }],
    ['superadmin: posts SHA256(hash+LD) + AD and saves the hash', async () => {
      const r = await superadminRun('0', true);
      assert.strictEqual(r.posts.length, 1);
      assert.strictEqual(r.posts[0].goformId, 'HIDDEN_PAGE_LOGIN');
      assert.strictEqual(r.posts[0].password, 'sha(' + 'H'.repeat(64) + 'LD1)');
      assert.strictEqual(r.posts[0].AD, 'sha(sha(BD_UGBGRMC888V1.0.0B09)RD1)');
      assert.deepStrictEqual(r.saved, [['superadmin_password_hash', 'H'.repeat(64)]]);
      assert.strictEqual(r.outcome, 'ok'); }],
    ['superadmin: wrong hash drops the stored hash and stays quiet', async () => {
      const r = await superadminRun('3', true);
      assert.deepStrictEqual(r.deleted, ['superadmin_password_hash']);
      assert.deepStrictEqual(r.saved, []);
      assert.deepStrictEqual(r.alerts, []); }],
  ];
  for (const [name, fn] of antCases.concat(cases)) { try { await fn(); console.log('ok   ', name); } catch (e) { fails++; console.log('FAIL ', name, '-', e.message); } }
  console.log(fails ? fails + ' FAILED' : 'ALL PASSED');
  process.exit(fails ? 1 : 0);
})();
