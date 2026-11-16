// Lantern Control (Player A, the Flight Director). You can see everything,
// and all of it is old.
import { $, $$, el, dur, num, Link, toast, sceneCard, guide, tabs, briefing, drawer, provenanceDrawer, modelCardDrawer,
  scienceDrawer, science, linkState, ageClass, etIso , roomUrl, roomChip, ROOM, realityToast } from '/shared/ll.js';
import { LaunchFX } from '/shared/launch.js';
import { drawOrrery, drawRibbon, drawSpacetime } from '/shared/viz.js';
import { DesignUI } from '/shared/design.js';
import { Score } from '/shared/audio.js';
import { Recorder, playAudio } from '/shared/voice.js';
import { showDebrief } from '/shared/debrief.js';
import { ReviewOverlay } from '/shared/review.js';
import { traceItem } from '/shared/trace.js';
import { createRoom } from '/shared/room.js';

let S = null, recvAt = 0, traj = null, meta = null, design = null, lastPhase = null, lastFido = null, kind = 'text', map = 'orrery';
const history = new Map();   // bundle id -> flight, for the spacetime diagram
const score = new Score(), rec = new Recorder(), board = new ReviewOverlay();
const PRE = ['design', 'review', 'scrubbed'];       // the phases that show the design bay
let recording = false;
const showTab = tabs($('#tabs'), (id) => $(`[data-tab="${id}"] .dot`)?.remove());
const unread = (id) => { const b = $(`[data-tab="${id}"]`); if (b && !b.classList.contains('on') && !b.querySelector('.dot')) b.append(el('span', { class: 'dot' })); };

// Two Earth seats share this console: the Flight Director (default) and the
// Mission Scientist (?seat=scientist). Both read what Lantern knows.
const SEAT = new URLSearchParams(location.search).get('seat') === 'scientist' ? 'scientist' : 'control';
document.body.dataset.seat = SEAT;
$('#seat-name').textContent = SEAT === 'scientist' ? 'Mission Scientist' : 'Flight Director';
for (const [seat, id] of [['control', 'flight-station'], ['scientist', 'science-station']]) {
  $('#' + id).classList.toggle('is-active', seat === SEAT);
  $(`[data-seat-state="${seat}"]`).textContent = seat === SEAT ? 'Your station' : 'Partner station';
}
if (SEAT === 'scientist') document.title = document.title.replace(/Lantern Control|LIGHT-LAG/, (m) => m + ' / Mission Scientist');
document.querySelector('.topbar .spacer')?.after(roomChip('/'));
const room = createRoom({ side: 'earth', seat: SEAT, action: (action) => link.send('ship.action', { action }) });
const launch = new LaunchFX({ side: 'earth', room });         // Phase 6: TMI as Houston sees it, one light time late
const link = new Link(SEAT, {
  hello: (h) => { meta = h.meta; if (room) room.meta = h.meta; $('#fido-mode').textContent = /fallback/.test(h.agents.control.mode) ? 'Running without a local language model: answers come from rules and tools.' : `Running ${h.agents.control.model} locally.`; },
  state: onState, event: onEvent, ai: (m) => fidoReply(m),
  reply: (m) => { if (m.data?.error) toast(m.data.error, 'error'); if (m.data?.note) toast(m.data.note, 'info', 3500); },
  close: () => toast('Lost the connection to the game server. Reconnecting.', 'error', 2500),
});

// ------------------------------------------------------------------ state
let lastObjective = null, signedAt = null;
function onState(s) {
  S = s; recvAt = performance.now();
  room?.update(s);
  launch.update(s);
  document.body.classList.toggle('design-phase', PRE.includes(s.phase));
  if (s.phase !== lastPhase) {
    // Back to the bay after a flight (fly it again, or a reset): start the page clean.
    if (lastPhase && !PRE.includes(lastPhase) && s.phase === 'design') return location.reload();
    lastPhase = s.phase;
    $('#design').classList.toggle('hidden', !PRE.includes(s.phase) || (room && !room.dashboardMode && room.focus !== 'design'));
    if (PRE.includes(s.phase) && !design) design = new DesignUI($('#design'), link, SEAT);
    if (!PRE.includes(s.phase)) { fetch(roomUrl('/api/trajectory')).then((r) => r.json()).then((t) => { traj = t; if (room) room.trajectory = t; }).catch(() => {}); if (!room || room.dashboardMode) briefing(SEAT); }
    if (s.phase === 'debrief') showAAR();
  }
  board.update(s);
  if (PRE.includes(s.phase)) { design?.update(s); return; }
  if (!s.geometry) return;
  score.update(s);
  const g = s.geometry, c = s.control, [lb, lcls, why] = linkState(s.comms.link);
  $('#t-day').textContent = `T+ ${num(g.mission_day, 1)} d`;
  $('#t-utc').textContent = s.utc.replace('T', ' ').replace('Z', '');
  $('#t-home').textContent = s.home_local.replace(/^\w+ /, '');
  $('#t-link').innerHTML = ''; $('#t-link').append(el('span', { class: 'badge ' + lcls, title: why }, lb));
  $('#t-rate').textContent = s.rate === 0 ? 'Mission clock stopped' : `${num(s.rate, 0)}× real time`;
  $('#lt-big').textContent = dur(g.lt_up_s, { precise: true });
  $('#lt-sub').textContent = s.rate === 0 ? 'Mission clock stopped' : s.rate > 1 ? `You wait ${dur(g.lt_up_s / s.rate, { precise: true })} at this speed` : 'True light time · 1:1 clock';
  $('#round-trip').textContent = `Round trip ${dur(g.lt_up_s + g.lt_down_s)}`;
  renderWall(s);
  sceneCard(s.director);
  const open = (s.director.objectives || []).find((o) => !o.done && (o.side === 'control' || o.side === 'both'));
  const waitSign = open?.id === 'policy_signed' && signedAt === 'policy_signed';
  guide($('#coach'), 'control', s, waitSign ? { what: 'TERN’s rules are signed and on their way.', how: `Meridian gets them ${dur(g.lt_up_s)} after they leave; Imani countersigns aboard.`, target: null } : null);
  $('#coach').classList.toggle('urgent', open?.id === 'storm_warning');
  if (open && open.id !== lastObjective) { lastObjective = open.id; if (open.id === 'policy_signed') showTab('rules'); if (open.id === 'storm_warning') setKind('alert'); }
  renderTelemetry(c, g); renderShipState(c); renderSW(c); renderComms(s); renderInbox(c, s.callouts); renderFamily(c);
  renderRequests(c); renderPolicy(c.policy); renderTernLog(c); renderDSNNow(c.dsn_now);
  renderScience(s.science, g); renderCrewEarth(s.crew, g);
  if (!sciOpened) { sciOpened = true; if (SEAT === 'scientist') showTab('science'); }
  for (const f of s.comms.in_flight) history.set(f.id, f);
  for (const [id, f] of history) if (s.et - f.arrive_et > g.lt_up_s * 3 + 60) history.delete(id);
  drawMap();
}
function drawMap(now = performance.now()) {
  if (typeof now !== 'number') now = performance.now();
  if (!S?.geometry) return;
  if (map === 'orrery') drawOrrery($('#orrery'), S, traj, 'control', now, recvAt); else drawSpacetime($('#spacetime'), S, [...history.values()]);
}
let lastMapFrame = 0;
(function frame(now = performance.now()) {
  if (S?.geometry && now - lastMapFrame >= 50) { drawRibbon($('#ribbon'), S, now, recvAt); drawMap(now); lastMapFrame = now; }
  requestAnimationFrame(frame);
})();

// Local operations are current; remote readings keep their own sampling age.
function renderWall(s) {
  const L = s.comms.link || {}, ps = s.comms.passes || [], g = s.geometry;
  const current = ps.find((p) => p.start <= s.et && p.end > s.et);
  const next = ps.find((p) => p.start > s.et);
  $('#dsn-complex').textContent = L.pass_now || 'No scheduled contact';
  $('#dsn-state').textContent = L.blackout ? 'Solar conjunction · link blocked' : L.reason || 'Awaiting antenna';
  $('#dsn-aos').textContent = L.blackout ? 'Held by conjunction' : next ? `${next.complex} · ${dur(next.start - s.et)}` : 'No pass in plan';
  $('#dsn-los').textContent = L.blackout ? 'No usable pass' : current ? dur(current.end - s.et) : next ? dur(next.end - s.et) : 'Unavailable';
  $('#dsn-rate').textContent = L.data_rate_bps == null ? 'Unavailable' : `${num(L.data_rate_bps / 1000, 1)} kbit/s`;
  $('#dsn-command').textContent = L.blackout ? 'Blackout' : L.moratorium ? 'Commands held' : 'Available';
  const bt = s.windows?.blackout;
  $('#dsn-conjunction').textContent = !bt ? 'Unavailable' : s.et < bt.start_et ? `${num((bt.start_et - s.et) / 86400, 1)} d` : s.et < bt.end_et ? `${num((bt.end_et - s.et) / 86400, 1)} d remaining` : 'Ended';
  $('#dsn-age').textContent = `Plan computed at ${etIso(s.et)} UTC`;
  $('#geometry-age').textContent = `Geometry computed at ${etIso(s.et)} UTC`;
  $('#map-source').textContent = s.ephemeris?.backend === 'spice' ? 'REAL ephemeris / MODEL transfer' : 'MODEL · analytic ephemeris';
  $('#radio-age').textContent = `Local queue · ${s.comms.queue_counts?.control ?? '–'} bundles · current`;
  $('#policy-age').textContent = `Policy register at ${etIso(s.et)} UTC`;
  $('#fido-age').textContent = s.control.age_s == null ? 'Ground assistant · no spacecraft telemetry received' : `Ground assistant · ship knowledge ${dur(s.control.age_s)} old`;
  const flights = (s.comms.in_flight || []).filter((f) => !['telemetry', 'status'].includes(f.kind));
  $('#flight-bundles').replaceChildren(...(flights.length ? flights.slice(0, 6).map((f) => el('div', { class: 'qitem' },
    el('span', { class: 'grow' }, `${f.direction === 'up' ? '↑' : '↓'} ${f.label || f.kind}`),
    el('span', { class: 'num small' }, `ETA ${dur(Math.max(0, f.arrive_et - s.et))}`))) : [el('div', { class: 'empty' }, 'No messages in flight')]));
}

// ------------------------------------------------------------------ the ship, as of minutes ago
const GROUPS = [['Power', ['batt_soc', 'solar_kw', 'bus_v', 'bus_a']], ['Coolant', ['loop_a_c', 'loop_b_c', 'cabin_c']],
  ['Air', ['ppco2', 'scrub_dp']], ['Radio and radiation', ['link_db', 'dose_rate']]];
const SHORT = { batt_soc: 'Battery', solar_kw: 'Solar', bus_v: 'Bus voltage', bus_a: 'Bus current', loop_a_c: 'Loop A', loop_b_c: 'Loop B', cabin_c: 'Cabin',
  ppco2: 'CO₂', scrub_dp: 'Scrubber Δp', link_db: 'Link margin', dose_rate: 'Dose rate' };
const UNIT = (u) => ({ C: '°C', 'uSv/h': 'µSv/h' }[u] || u);
function level(ch, v) {
  const lim = meta?.limits?.[ch]; if (!lim || v == null) return '';
  const low = ch === 'batt_soc' || ch === 'link_db';
  for (const lv of ['emergency', 'warning', 'caution']) if (low ? v <= lim[lv] : v >= lim[lv]) return 'lvl-' + lv;
  return '';
}
function renderTelemetry(c, g) {
  const box = $('#tlm'), pill = $('#tlm-age');
  for (const cls of ['age-0', 'age-1', 'age-2', 'age-3']) $('#ship-col').classList.toggle(cls, cls === ageClass(c.age_s, g.lt_down_s));
  pill.textContent = c.age_s == null ? 'No telemetry received' : `as of ${dur(c.age_s)} ago`;
  if (!c.telemetry || !meta) { box.innerHTML = '<div class="empty">Nothing has reached Lantern yet.</div>'; return; }
  pill.textContent = `as of ${dur(c.age_s)} ago`; pill.className = 'age-pill' + (c.stale ? ' stale' : '');
  $('#tlm-floor').textContent = c.stale ? 'Older than the light time: the ship has gone quiet.' : `Light alone takes ${dur(g.lt_down_s)}.`;
  if (!box.dataset.built) {
    box.dataset.built = 1;
    for (const [name, chs] of GROUPS) box.append(el('div', { class: 'tgroup' }, el('h3', {}, name),
      el('div', { class: 'tgrid' }, chs.map((ch) => el('div', { class: 'readout', 'data-ch': ch }, el('span', { class: 'k' }, SHORT[ch]), el('span', { class: 'v' }))))));
  }
  for (const node of $$('[data-ch]', box)) {
    const ch = node.dataset.ch, v = c.telemetry[ch];
    const d = ch === 'bus_a' || ch === 'batt_soc' ? 0 : ch === 'ppco2' ? 2 : 1;
    node.lastChild.innerHTML = `${num(v, d)}<span class="u">${UNIT(meta.units[ch])}</span>`;
    node.lastChild.className = 'v ' + level(ch, v);
  }
}
function renderShipState(c) {
  const d = c.dose, m = c.modes || {}, a = c.anomaly;
  const key = JSON.stringify([d?.total_msv, d?.sep_msv, c.shelter, c.attitude, m, a?.alert, a && Math.round(a.score * 100)]);
  if ($('#shipstate').dataset.k === key) return; $('#shipstate').dataset.k = key;
  $('#shipstate').innerHTML = !d ? '' : `
    <tr><td>Imani's dose this mission</td><td class="v">${num(d.total_msv, 1)}<span class="u">mSv</span></td></tr>
    <tr><td>of that, from solar storms</td><td class="v">${num(d.sep_msv, 1)}<span class="u">mSv</span></td></tr>
    <tr><td>Storm shelter</td><td class="v txt ${c.shelter ? 'lantern' : ''}">${c.shelter ? 'Inside' : 'Not in it'}</td></tr>
    <tr><td>Pointing</td><td class="v txt ${c.attitude ? 'lantern' : ''}">${c.attitude ? 'Tanks to the Sun' : 'Antenna on Earth'}</td></tr>
    <tr><td>Coolant loop running</td><td class="v">${m.active_loop ?? '–'}</td></tr>
    <tr><td>Antenna pointing error</td><td class="v">${num(m.hga_err_deg, 2)}<span class="u">°</span></td></tr>`;
  if (a) {
    const f = Math.min(1, a.score / (a.threshold * 1.6));
    $('#anomaly').innerHTML = '';
    $('#anomaly').append(el('div', { class: 'meter' + (a.alert ? ' hot' : '') }, el('div', { class: 'row' }, el('span', { class: 'grow small haze' }, 'Anomaly detector'),
      el('span', { class: 'small ' + (a.alert ? 'lantern' : 'dim') }, a.alert ? 'Something looks wrong' : 'Looks normal')),
      el('div', { class: 'track' }, el('i', { style: `width:${f * 100}%` }), el('b', { style: `left:${(1 / 1.6) * 100}%` }))));
  }
}

// ------------------------------------------------------------------ space weather, passes, DSN
function renderSW(c) {
  const sw = c.space_weather || [], key = JSON.stringify(sw) + (c.storm_label || '');
  const latest = sw.at(-1);
  const stamp = latest ? `Last observation ${dur(Math.max(0, S.et - latest.et))} ago` : 'No event received';
  $('#weather-age').textContent = stamp;
  $('#weather-log-age').textContent = stamp;
  if ($('#sw').dataset.k === key) return; $('#sw').dataset.k = key;
  $('#sw').innerHTML = '';
  if (sw.length && $('#sw').dataset.seen !== String(sw.length)) { unread('weather'); $('#sw').dataset.seen = sw.length; }
  if (!sw.length) $('#sw').append(el('div', { class: 'empty' }, 'No space-weather events received. Earth observations appear here as the mission learns about them.'));
  for (const i of [...sw].reverse()) $('#sw').append(el('div', { class: 'msg alert' }, el('div', { class: 'meta' }, (i.iso || '').replace('T', ' ').slice(0, 16)), i.text));
  if (c.storm_label) $('#sw').append(el('div', { class: 'small dim' }, c.storm_label));
  $('#weather-log').replaceChildren(...[...$('#sw').children].map((n) => n.cloneNode(true)));
}
const CPLX = [['Goldstone', 'California'], ['Madrid', 'Spain'], ['Canberra', 'Australia'], ['surge', 'Emergency']];
function renderPasses(s) {
  const ps = s.comms.passes, t0 = s.et, span = 48 * 3600, box = $('#passes');
  const key = ps.map((p) => p.complex + Math.round(p.start / 600)).join() + Math.round(t0 / 600);
  if (box.dataset.k === key) return; box.dataset.k = key;
  box.innerHTML = '';
  const chart = el('div', { class: 'passes' });
  for (const [cx, where] of CPLX) {
    const mine = ps.filter((p) => p.complex === cx); if (cx === 'surge' && !mine.length) continue;
    chart.append(el('div', { class: 'prow' }, el('span', { class: 'pname' }, cx === 'surge' ? 'Emergency' : cx, el('span', { class: 'dim small' }, cx === 'surge' ? '' : ' ' + where)),
      el('div', { class: 'ptrack' }, mine.map((p) => el('i', { class: cx === 'surge' ? 'surge' : '', style: `left:${Math.max(0, (p.start - t0) / span * 100)}%;width:${Math.max(0.6, (Math.min(p.end, t0 + span) - Math.max(p.start, t0)) / span * 100)}%` })))));
  }
  chart.append(el('div', { class: 'prow axis' }, el('span'), el('div', { class: 'ptrack' }, [0, 12, 24, 36, 48].map((h) => el('span', { style: `left:${h / 48 * 100}%` }, h ? `+${h} h` : 'now')))));
  box.append(chart, el('p', { class: 'small dim', style: 'margin:6px 0 0' }, 'Bars are when a dish can hear Meridian. In the gaps, everything you send waits here in Houston.'));
}
function renderDSNNow(d) {
  if (!d) return;
  const key = JSON.stringify(d).length; if ($('#dsnnow').dataset.k == key) return; $('#dsnnow').dataset.k = key;
  $('#dsnnow').innerHTML = '';
  if (!d.stations) { $('#dsnnow').append(el('div', { class: 'empty' }, d.note || 'Not available offline.')); return; }
  for (const st of d.stations) $('#dsnnow').append(el('div', { class: 'msg' }, el('b', {}, st.friendly),
    el('div', { class: 'row', style: 'gap:6px;margin-top:4px' }, st.dishes.map((x) => el('span', { class: 'badge plain' }, `${x.name}: ${x.targets.join(', ') || 'idle'}`)))));
  $('#dsnnow').append(el('div', { class: 'small dim' }, `${d.attribution || ''} ${d.age_s != null ? `Fetched ${dur(d.age_s)} ago.` : ''}`));
}

// ------------------------------------------------------------------ the radio
function renderComms(s) {
  const L = s.comms.link, [lb, lcls] = linkState(L);
  $('#linkst').innerHTML = ''; $('#linkst').append(el('span', { class: 'badge ' + lcls }, lb));
  const waiting = L && !L.uplink_open;
  $('#reason').textContent = L?.blackout ? 'The Sun is between you. Nothing gets through until it moves.' : L?.moratorium && kind === 'command' ? 'Commands are on hold near the Sun. Messages still go.'
    : waiting ? 'No dish can hear the ship right now. What you send will wait for the next pass.' : '';
  $('#eta').textContent = `Arrives ${dur(s.geometry.lt_up_s)} after it leaves.`;
  const q = s.comms.queue || [];
  $('#queue-wrap').classList.toggle('hidden', !q.length);
  const key = q.map((x) => x.id).join();
  if ($('#queue').dataset.k !== key) { $('#queue').dataset.k = key; $('#queue').innerHTML = '';
    for (const x of q) { const name = QNAME[x.kind] || x.kind, extra = x.label && x.label.toLowerCase() !== x.kind && !['MSG', 'ALERT', 'VOICE'].includes(x.label) ? `: ${x.label}` : '';
      $('#queue').append(el('div', { class: 'qitem ' + x.kind }, el('span', { class: 'grow' }, name + extra), el('span', { class: 'small dim' }, `written ${etIso(x.created_et)}`))); } }
  $('#queue-wrap h3').textContent = L?.blackout ? 'Held until the Sun moves out of the way' : L?.moratorium ? 'Held: no commands this close to the Sun' : 'Waiting for an antenna';
  renderPasses(s);
  if (document.activeElement !== $('#planSel') && $('#planSel').value !== s.comms.plan) $('#planSel').value = s.comms.plan;
}
function renderInbox(c, callouts = []) {
  // Design traces (callouts) sit among the messages, in the order Lantern received them.
  const items = [...(c.inbox || []).filter((m) => !m.family), ...callouts.map((x) => ({ ...x, kind: 'trace', delivered_et: x.arrived_et }))]
    .sort((a, b) => (a.delivered_et ?? 0) - (b.delivered_et ?? 0)), box = $('#inbox');
  const key = items.map((m) => m.id).join(); if (box.dataset.k === key) return; box.dataset.k = key;
  box.innerHTML = '';
  if (!items.length) { box.append(el('div', { class: 'empty' }, 'Nothing from Meridian yet.')); return; }
  for (const m of items) if (m.kind === 'trace') box.append(traceItem(m));
  else if (m.kind === 'letter') box.append(el('div', { class: 'relay' }, `Imani sent a personal message home, ${etIso(m.created_et)}. Lantern relays it unread.`));
  else box.append(el('div', { class: 'msg ' + (m.kind === 'alert' ? 'alert' : '') },
    el('div', { class: 'meta' }, el('span', {}, m.from), el('span', {}, `written ${etIso(m.created_et)}`), el('span', {}, `${dur(m.lt_s)} in transit`)),
    el('div', {}, m.text), m.audio_id ? el('button', { class: 'ghost small', onclick: () => playAudio(m.audio_id) }, 'Play voice') : null));
  box.scrollTop = box.scrollHeight;
}
const famSent = [];
const QNAME = { letter: 'Letter for Imani', text: 'Message', alert: 'Alert', command: 'Command', voice: 'Voice note', policy: 'TERN’s new rules', policy_answer: 'Your answer to TERN', ping: 'A wave' };
function renderFamily(c) {
  const items = [...(c.inbox || []).filter((m) => m.family).map((m) => ({ who: m.from, text: m.text, t: m.created_et })), ...famSent].sort((a, b) => a.t - b.t);
  const box = $('#famlog'), key = items.length;
  if (box.dataset.k == key) return; box.dataset.k = key; box.innerHTML = '';
  for (const m of items.slice(-4)) box.append(el('div', { class: 'famline' + (m.mine ? ' mine' : '') }, el('span', { class: 'who' }, m.mine ? 'You' : m.who), ' ', m.text));
}

// ------------------------------------------------------------------ TERN: requests, rules, log
function renderRequests(c) {
  const rq = c.requests || [], box = $('#requests');
  const key = rq.map((r) => r.rule).join(); if (box.dataset.k === key) return; box.dataset.k = key;
  box.innerHTML = '';
  if (rq.length) unread('rules');
  for (const r of rq) box.append(el('div', { class: 'request' }, el('div', { class: 'row' }, el('b', { class: 'grow' }, 'TERN is asking permission'), el('span', { class: 'small dim' }, r.trigger)),
    el('div', {}, r.rationale),
    el('div', { class: 'row' }, el('button', { class: 'primary', onclick: () => link.send('request.answer', { rule: r.rule, approve: true }) }, 'Allow it'),
      el('button', { class: 'warn', onclick: () => link.send('request.answer', { rule: r.rule, approve: false }) }, 'Refuse'),
      el('span', { class: 'small dim grow' }, 'Your answer crosses space too.'))));
}
const PERMS = [['auto', 'Acts alone'], ['ask', 'Asks first'], ['forbid', 'Never']];
let draft = null, draftKey = null;
function renderPolicy(p) {
  if (!p) return;
  const key = JSON.stringify([p.version, p.signed_by, p.rules.map((r) => r.permission)]);
  if (key === draftKey) return; draftKey = key;
  draft = p.rules.map((r) => ({ id: r.id, permission: r.permission, label: r.label }));
  const box = $('#policy-ed'); box.innerHTML = '';
  const rows = el('div', { class: 'rules' });
  const paint = () => { rows.innerHTML = ''; for (const r of draft) rows.append(el('div', { class: 'rule' }, el('span', { class: 'grow' }, r.label),
    el('div', { class: 'seg' }, PERMS.map(([k, t]) => el('button', { class: r.permission === k ? 'on' : '', onclick: () => { r.permission = k; paint(); } }, t))))); };
  paint();
  box.append(el('div', { class: 'row' }, el('span', { class: 'grow small haze' }, `Aboard now: version ${p.version}, signed by ${p.signed_by.map((x) => (x === 'ship' ? 'Imani' : 'Lantern')).join(' and ') || 'nobody yet'}.`)),
    rows, el('div', { class: 'row' }, el('button', { class: 'primary', id: 'sign', onclick: () => { link.send('policy.set', { rules: draft.map(({ id, permission }) => ({ id, permission })) }); link.send('policy.sign'); signedAt = lastObjective; toast('Signed. TERN’s rules are queued to go up.', 'info'); } }, 'Sign and send'),
      el('span', { class: 'small dim grow' }, 'New rules are a command, so they stop going up when the Sun gets close.')));
}
function renderTernLog(c) {
  const log = c.tern_log || [], box = $('#ternlog');
  const key = log.length; if (box.dataset.k == key) return; box.dataset.k = key;
  if (!log.length) return;
  box.innerHTML = '';
  for (const d of [...log].reverse().slice(0, 12)) box.append(el('div', { class: 'msg ' + (d.outcome === 'broke_policy' ? 'alert' : 'tern') },
    el('div', { class: 'meta' }, el('span', {}, d.rule?.replace(/_/g, ' ')), el('span', {}, d.outcome === 'broke_policy' ? 'broke its rules' : d.policy_status), el('span', {}, d.iso?.slice(0, 16).replace('T', ' '))), d.rationale));
}

// ------------------------------------------------------------------ science (the Mission Scientist's panel)
let sciOpened = false;
const gb = (v) => num(v, 2);
function renderScience(sc, g) {
  if (!sc) return;
  const as = sc.as_of || {}, sm = as.summary || {}, e = sc.earth || {}, o = sc.objective || {};
  const pill = $('#sci-age');
  pill.textContent = as.age_s != null ? `aboard, as of ${dur(as.age_s)} ago` : 'nothing from the ship yet';
  pill.className = 'age-pill ' + ageClass(as.age_s, g.lt_down_s) + (as.age_s != null && as.age_s > g.lt_down_s + 3600 ? ' stale' : '');
  $('#sci-obj-name').textContent = `Objective: ${o.name || '–'}`;
  $('#science-earth-age').textContent = `Earth receipts at ${etIso(S.et)} UTC · weighted science value ${gb(o.value_gbit)} Gbit · MODEL`;
  const deliveredKey = JSON.stringify(e.delivered_by_instrument);
  if ($('#science-instruments').dataset.k !== deliveredKey) {
    $('#science-instruments').dataset.k = deliveredKey;
    $('#science-instruments').replaceChildren(...(sc.payload || []).map((i) => el('tr', {},
      el('td', {}, i.name), el('td', { class: 'number' }, gb(e.delivered_by_instrument?.[i.id] ?? 0)))));
  }
  const key = JSON.stringify([sm, e.delivered_gbit, o.relevant_gbit]);
  if ($('#sci-kv').dataset.k !== key) {
    $('#sci-kv').dataset.k = key;
    $('#sci-kv').innerHTML = `
      <tr><td>Recorded aboard</td><td class="v">${gb(sm.gen)}<span class="u">Gbit</span></td></tr>
      <tr><td>On the recorder</td><td class="v">${gb(sm.rec)}<span class="u">Gbit</span></td></tr>
      <tr><td>Packed or crossing space</td><td class="v">${gb(sm.fly)}<span class="u">Gbit</span></td></tr>
      <tr><td>Lost: overwritten or expired</td><td class="v ${sm.lost > 0 ? 'lantern' : ''}">${gb(sm.lost)}<span class="u">Gbit</span></td></tr>
      <tr><td>Delivered to Houston, now</td><td class="v">${gb(e.delivered_gbit)}<span class="u">Gbit</span></td></tr>
      <tr><td>Payload powered</td><td class="v txt ${sm.on === false ? 'lantern' : ''}">${sm.on == null ? '–' : sm.on ? 'Yes' : 'No: shed to save power'}</td></tr>`;
  }
  const rf = sm.cap ? Math.min(1, (sm.rec || 0) / sm.cap) : 0;
  $('#sci-rec').style.width = `${rf * 100}%`; $('#sci-rec-t').textContent = `${num(rf * 100, 0)}% of ${gb(sm.cap)} Gbit`;
  $('#sci-obj').style.width = `${(o.progress || 0) * 100}%`;
  $('#sci-obj').style.background = o.met ? 'var(--go)' : 'var(--haze)';
  $('#sci-obj-t').textContent = `${gb(o.relevant_gbit)} of ${gb(o.min_data_gbit)} Gbit${o.met ? ', met' : ''}`;
  $('#sci-obj-label').textContent = o.covered === false ? 'The payload cannot serve this objective' : 'Data the objective needs, delivered';
  const L = e.lever, aboard = sm.prio;
  $$('#sci-prio button').forEach((b) => { b.classList.toggle('on', b.dataset.v === (L?.value || aboard || 'crew_first')); b.disabled = !e.may_set;
    b.title = e.may_set ? '' : 'The Mission Scientist sets this.'; });
  const NAME = { science_first: 'Science first', crew_first: 'Crew first' };
  let t = aboard ? `Aboard, as of the last telemetry: ${NAME[aboard]}.` : '';
  if (L) {
    const st = L.state === 'queued' ? `${L.label}: sent, ${L.why}.` : L.state === 'in_flight' ? `${L.label}: sent, arrives aboard in ${dur(L.arrives_in_s)}.`
      : L.state === 'arrived_unconfirmed' ? `${L.label}: should be aboard now. The receipt is on its way back.` : L.state === 'confirmed' ? `${L.label}: in effect aboard (receipt received).`
      : `${L.label}: ${L.state}.`;
    t = st + (aboard && L.state !== 'confirmed' ? ` ${t}` : '');
  }
  $('#sci-lever').textContent = t;
}
$$('#sci-prio button').forEach((b) => (b.onclick = () => { if (!b.disabled) link.send('science.priority', { value: b.dataset.v }); }));

// ------------------------------------------------------------------ the crew, as of the last telemetry
const WORDS = { f: ['rested', 'a bit tired', 'tired', 'exhausted'], s: ['calm', 'alert', 'strained', 'overwhelmed'],
  t: ['barely', 'somewhat', 'mostly', 'fully'], m: ['low', 'flat', 'good', 'high'] };
const LABEL = { f: 'Tiredness', s: 'Stress', t: 'Trusts Houston', m: 'Morale' };
const w4 = (v, k) => WORDS[k][Math.max(0, Math.min(3, Math.floor((v ?? 0) * 4)))];
function renderCrewEarth(cr, g) {
  if (!cr) return;
  const as = cr.as_of || {}, sm = as.summary || {}, names = cr.names || {};
  const pill = $('#crew-age');
  pill.textContent = as.age_s != null ? `as of ${dur(as.age_s)} ago` : 'nothing from the ship yet';
  pill.className = 'age-pill ' + ageClass(as.age_s, g.lt_down_s);
  for (const cls of ['age-0', 'age-1', 'age-2', 'age-3']) $('#crew-pane').classList.toggle(cls, cls === ageClass(as.age_s, g.lt_down_s));
  const key = JSON.stringify(sm) + Object.keys(names).join();
  if ($('#crew-rows').dataset.k !== key) {
    $('#crew-rows').dataset.k = key; $('#crew-rows').innerHTML = '';
    for (const [id, n] of Object.entries(names)) {
      const m = sm[id];
      $('#crew-rows').append(el('div', { class: 'crew-line' },
        el('div', { class: 'row' }, el('b', { class: 'grow' }, n.name, el('span', { class: 'dim small' }, ` ${n.role}`)), el('span', { class: 'badge plain' }, m ? (m.ai ? 'AI' : 'Person') : '–')),
        el('div', { class: 'small haze' }, m ? m.task : 'No word yet.'),
        m ? el('div', { class: 'cstats' }, ['f', 's', 't', 'm'].map((k) => el('div', { class: 'cstat' + ((k === 'f' || k === 's') ? (m[k] >= 0.75 ? ' hi' : '') : (m[k] < 0.25 ? ' hi' : '')) },
          el('span', { class: 'k' }, LABEL[k]), el('span', { class: 'cbar' }, el('i', { style: `width:${Math.round(m[k] * 100)}%` })), el('span', { class: 'w' }, w4(m[k], k))))) : null));
    }
  }
  const log = cr.log || [], box = $('#crew-log');
  if (box.dataset.k != log.length) {
    box.dataset.k = log.length;
    if (log.length) { box.innerHTML = ''; for (const x of [...log].reverse().slice(0, 30)) box.append(el('div', { class: 'msg ' + (x.kind.startsWith('order_') ? 'alert' : 'crew') },
      el('div', { class: 'meta' }, el('span', {}, x.name), el('span', {}, x.kind === 'said' ? 'said' : x.kind.replace(/_/g, ' ')), el('span', {}, `aboard ${(x.iso || '').slice(0, 16).replace('T', ' ')}`)), x.text)); }
  }
}

// ------------------------------------------------------------------ events
function onEvent(e) {
  if (e.kind === 'crew_log') { unread('crew'); const held = (e.entries || []).find((x) => x.kind === 'order_held' || x.kind === 'order_query');
    if (held) toast(`The crew held your order: ${held.text}`, 'warning', 9000); }
  if (e.kind === 'science_rx') unread('science');
  if (e.kind === 'alert') toast(`${e.title}: ${e.text}`, e.level || 'warning', 9000);
  if (e.kind === 'message') toast(`From ${e.message.from}: ${e.message.text.slice(0, 120)}`, 'info');
  if (e.kind === 'policy_request') { unread('rules'); toast('TERN is asking permission. See TERN’s rules.', 'warning', 9000); }
  if (e.kind === 'tern_log') { unread('rules'); toast(e.decision.outcome === 'broke_policy' ? `TERN broke its rules: ${e.decision.rule.replace(/_/g, ' ')}` : `TERN acted: ${e.decision.rule.replace(/_/g, ' ')}`, e.decision.outcome === 'broke_policy' ? 'warning' : 'info', 8000); }
  if (e.kind === 'objective') toast('Done: ' + e.text, 'info', 4000);
  if (e.kind === 'design_nogo') toast('Not go: ' + e.reason, 'warning');
  if (e.kind === 'callout') toast('Your design: ' + e.callout.text, 'info', 9000);
  if (e.kind === 'note') toast(e.text, 'info');
  if (e.kind === 'reality') realityToast(e.reality);          // Phase 6, never scored
  if (e.kind === 'debrief') { lastPhase = 'debrief'; showAAR(e.report); }
}
function fidoReply(m) {
  lastFido = m;
  const log = $('#fido-log'); log.querySelector('.empty')?.remove();
  log.append(el('div', { class: 'msg tern' }, el('div', { class: 'meta' }, el('span', {}, 'FIDO'), el('span', {}, `${m.tool_calls.length} lookups`), el('span', {}, m.grounding.ok ? 'every number checked' : 'could not check every number')), m.text));
  log.scrollTop = log.scrollHeight; unread('fido');
}

// ------------------------------------------------------------------ inputs
function setKind(k) { kind = k; $$('#kind button').forEach((b) => b.classList.toggle('on', b.dataset.v === k)); $('#cmd').classList.toggle('hidden', k !== 'command');
  $('#prio').checked = k === 'alert'; $('#text').placeholder = k === 'alert' ? 'Meridian, Lantern. Flare alert: …' : k === 'command' ? 'Optional note to go with the command' : 'Meridian, Lantern. …'; }
$$('#kind button').forEach((b) => (b.onclick = () => setKind(b.dataset.v)));
const transmit = () => {
  const text = $('#text').value.trim(), prio = $('#prio').checked ? 'expedited' : 'normal';
  if (kind === 'command') link.send('cmd.send', { command: $('#cmd').value, text: text || `Meridian, Lantern. Command: ${$('#cmd').selectedOptions[0].text}. Over.` });
  else if (text) link.send('msg.send', { kind, text, priority: kind === 'alert' ? 'expedited' : prio, label: kind === 'alert' ? 'ALERT' : 'MSG' });
  else return;
  $('#text').value = '';
};
$('#send').onclick = transmit;
$('#text').onkeydown = (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) transmit(); };
$('#rec').onclick = async () => {
  if (!rec.supported) return toast('No microphone available in this browser.', 'error');
  if (!recording) { await rec.start(); recording = true; $('#rec').textContent = 'Stop and send'; $('#rec').classList.add('warn'); return; }
  recording = false; $('#rec').textContent = 'Record voice'; $('#rec').classList.remove('warn');
  const r = await rec.stop(); if (!r?.ok) return toast(r?.error || 'The recording failed.', 'error');
  link.send('msg.send', { kind: 'voice', text: r.transcript || $('#text').value.trim() || '(voice note)', audio_id: r.id, audio_bytes: r.bytes, priority: $('#prio').checked ? 'expedited' : 'normal', label: 'VOICE' });
};
$('#surge').onclick = () => link.send('dsn.surge', { hours: 4 });
$('#planSel').onchange = (e) => link.send('comms.plan', { plan: e.target.value });
const famSend = () => { const t = $('#famtext').value.trim(); if (!t) return; link.send('msg.send', { kind: 'text', text: t, to: 'home' }); famSent.push({ mine: true, text: t, t: S?.et || 0 }); $('#famtext').value = ''; $('#famlog').dataset.k = ''; if (S) renderFamily(S.control); };
$('#famsend').onclick = famSend; $('#famtext').onkeydown = (e) => e.key === 'Enter' && famSend();
const ask = () => { const t = $('#ask').value.trim(); if (!t) return; const log = $('#fido-log'); log.querySelector('.empty')?.remove();
  log.append(el('div', { class: 'msg mine' }, el('div', { class: 'meta' }, 'You'), t)); link.send('ai.ask', { text: t }); $('#ask').value = ''; };
$('#askb').onclick = ask; $('#ask').onkeydown = (e) => e.key === 'Enter' && ask();
$('#dbg').onclick = () => drawer('dbg', 'How FIDO worked it out', () => el('pre', {}, JSON.stringify(lastFido || { note: 'Ask FIDO something first.' }, null, 2)));
$('#prov').onclick = provenanceDrawer; $('#mcard').onclick = () => modelCardDrawer(); $('#sci').onclick = scienceDrawer;
$('#aar').onclick = () => showAAR();
$('#help').onclick = () => briefing(SEAT, true);
$('#audio').onclick = () => { if (score.enabled) { score.stop(); $('#audio').textContent = 'Turn sound on'; } else { score.start(); $('#audio').textContent = 'Turn sound off'; } };
$$('#mapsel button').forEach((b) => (b.onclick = () => { map = b.dataset.v; $$('#mapsel button').forEach((x) => x.classList.toggle('on', x === b));
  $('#orrery').classList.toggle('hidden', map !== 'orrery'); $('#spacetime').classList.toggle('hidden', map === 'orrery');
  $('#map-note').textContent = map === 'orrery' ? 'MODEL transfer · ring marks light-time apparent position'
    : 'Time runs down the page. Each message is a slanted line: it takes the light time to cross. The ship can only know what is below the dashed line.';
  drawMap(); }));
addEventListener('resize', drawMap);
// the main grid fills whatever height the header leaves
new ResizeObserver(() => document.body.style.setProperty('--head-h', $('.ctl-head').offsetHeight + 'px')).observe($('.ctl-head'));

// ------------------------------------------------------------------ debrief
async function showAAR(report) {
  report = report || await (await fetch(roomUrl('/api/after-action'))).json();
  showDebrief(report);
}
