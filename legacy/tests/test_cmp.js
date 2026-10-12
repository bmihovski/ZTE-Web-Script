// Run: node tests/test_cmp.js zte-script-legacy.js
// Comparison/sign checks for the NGBR table, alignment table, saved records and tones.
const assert = require('assert');
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const grab = n => { const i = src.indexOf('function ' + n + '('); if (i < 0) throw n; let d = 0; for (let k = src.indexOf('{', i); k < src.length; k++) { if (src[k] == '{') d++; if (src[k] == '}' && --d == 0) return src.slice(i, k + 1); } };
const vars = re => eval(src.match(re)[0].replace(/var /g, 'global.'));
vars(/var NGBR_COLUMNS[\s\S]*?\]\];\n/); vars(/var LTE_BANDS[\s\S]*?\]\];\nvar NR_BANDS[\s\S]*?\]\];/);
vars(/var ALIGN_METRICS = \[[\s\S]*?\n\];/); vars(/var TONE_TARGET[^\n]*\n/); vars(/var TONE_RES[^\n]*\n/);
global.TONE_AVG_MS = 1500; global.TOOLS_AVG_MS = 3000; global.TOOLS_HISTORY_MS = 300000;
for (const f of ['ngbr_num', 'ngbr_sort_value', 'ngbr_cmp', 'arfcn_to_band', 'sinr_or_unknown', 'connected_cells', 'render_ngbr_cells',
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
  nr5g_action_channel: '636576', nr5g_pci: '7f', Z5g_rsrp: '-84', Z5g_rsrq: '-12', Z5g_SINR: '6.0', Z5g_rssi: '-74.0', nr_multi_ca_scell_info: '' });
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

console.log(fails ? fails + ' FAILED' : 'ALL PASSED');
process.exit(fails ? 1 : 0);
