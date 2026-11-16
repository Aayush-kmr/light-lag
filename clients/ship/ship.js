// Meridian-1 (Player B, Cmdr. Imani Reyes). The view is the ship; the side
// panel is the ship's systems, TERN, radiation and the radio.
import { $, $$, el, dur, num, Link, toast, sceneCard, guide, tabs, briefing, drawer, provenanceDrawer, modelCardDrawer,
  scienceDrawer, science, linkState, etIso , roomUrl, roomChip, ROOM, realityToast } from '/shared/ll.js';
import { LaunchFX } from '/shared/launch.js';
import { DesignUI } from '/shared/design.js';
import { Score, quindar } from '/shared/audio.js';
import { Recorder, playAudio, speak } from '/shared/voice.js';
import { SpaceView } from '/shared/viewport.js';
import { showDebrief } from '/shared/debrief.js';
import { ReviewOverlay } from '/shared/review.js';
import { addTrace } from '/shared/trace.js';
import { createRoom } from '/shared/room.js';
const board = new ReviewOverlay(), PRE = ['design', 'review', 'scrubbed'];

let S = null, meta = null, design = null, lastPhase = null, lastTern = null, recording = false, to = 'control';
const sent = [];
const score = new Score(), rec = new Recorder();
let view = null;
try { view = new SpaceView($('#view')); } catch (e) { console.warn('3D view unavailable', e); $('#stage').append(el('div', { class: 'empty', style: 'padding:120px 24px' }, 'This browser cannot draw the 3D view (WebGL is off). Everything else works.')); }
$('#view').addEventListener('vehicle-configured', (e) => {
  $('#craft-config').textContent = e.detail.text;
  $('#craft-config').title = 'MODEL: schematic proportions; shielding is areal density, array span encodes rated power. ' + e.detail.instruments.join(', ');
});

const showTab = tabs($('#side'), (id) => { $(`[data-tab="${id}"] .dot`)?.remove();
  if (id === 'crew') { const pane = $('#crew-pane'); pane.scrollTop = 0; pane.parentElement && (pane.parentElement.scrollTop = 0); } });
const unread = (id) => { const b = $(`[data-tab="${id}"]`); if (!b.classList.contains('on') && !b.querySelector('.dot')) b.append(el('span', { class: 'dot' })); };

// Two ship seats share this console: the Commander (default) and the
// Flight Engineer (?seat=engineer). Both see the ship as it is now.
const SEAT = new URLSearchParams(location.search).get('seat') === 'engineer' ? 'engineer' : 'ship';
document.body.dataset.seat = SEAT;
$('#seat-name').textContent = SEAT === 'engineer' ? 'Flight Engineer' : 'Commander';
for (const [seat, id] of [['ship', 'commander-station'], ['engineer', 'side']]) {
  $('#' + id).classList.toggle('is-active', seat === SEAT);
  $(`[data-seat-state="${seat}"]`).textContent = seat === SEAT ? 'Your station' : 'Partner station';
}
showTab(SEAT === 'engineer' ? 'systems' : 'tern');
if (SEAT === 'engineer') document.title = document.title.replace(/Meridian-1|LIGHT-LAG/, (m) => m + ' / Flight Engineer');
document.querySelector('.topbar .spacer')?.after(roomChip('/'));
const room = createRoom({ side: 'ship', seat: SEAT, action: (action) => link.send('ship.action', { action }) });
const launch = new LaunchFX({ side: 'ship', room });          // Phase 6: the trans-Mars injection burn
const link = new Link(SEAT, {
  hello: (h) => { meta = h.meta; if (room) room.meta = h.meta; $('#tern-mode').textContent = /fallback/.test(h.agents.ship.mode) ? 'No local model: rules only' : 'Local model'; },
  state: onState, event: onEvent, ai: (m) => ternReply(m),
  reply: (m) => { if (m.data?.text) toast(m.data.text, m.data.ok === false ? 'warning' : 'info', 3500); },
  close: () => toast('Lost the connection to the game server. Reconnecting.', 'error', 2500),
});

// ------------------------------------------------------------------ state
let lastObjective = null, signedAt = null;
function onState(s) {
  S = s;
  room?.update(s);
  launch.update(s);
  document.body.classList.toggle('design-phase', PRE.includes(s.phase));
  view?.update(s);
  if (s.phase !== lastPhase) {
    if (lastPhase && !PRE.includes(lastPhase) && s.phase === 'design') return location.reload();   // fly it again
    lastPhase = s.phase;
    $('#design').classList.toggle('hidden', !PRE.includes(s.phase) || (room && !room.dashboardMode && room.focus !== 'design'));
    if (PRE.includes(s.phase) && !design) design = new DesignUI($('#design'), link, SEAT);
    if (!PRE.includes(s.phase) && (!room || room.dashboardMode)) briefing(SEAT);
    if (s.phase === 'debrief') fetch(roomUrl('/api/after-action')).then((r) => r.json()).then(debrief).catch(() => {});
  }
  board.update(s);
  if (PRE.includes(s.phase)) { design?.update(s); return; }
  for (const c of s.callouts || []) if (!seenTrace.has(c.id)) { seenTrace.add(c.id); addTrace($('#tern-log'), c); }
  if (!s.geometry) return;
  score.update(s);
  const g = s.geometry, sh = s.ship, L = s.comms.link, [lb, lcls, why] = linkState(L);
  $('#viewport-source').textContent = s.ephemeris?.backend === 'spice' ? 'REAL Sun direction · SPICE' : 'MODEL · analytic ephemeris';
  $('#t-day').textContent = `T+ ${num(g.mission_day, 1)} d`;
  $('#t-utc').textContent = s.utc.replace('T', ' ').replace('Z', '');
  $('#t-home').textContent = s.home_local.replace(/^\w+ /, '');
  $('#t-lt').textContent = dur(g.lt_down_s, { precise: true });
  $('#t-link').innerHTML = ''; $('#t-link').append(el('span', { class: 'badge ' + lcls, title: why }, lb));
  sceneCard(s.director);
  // the guide, and open the tab it points into the first time an objective appears
  const open = (s.director.objectives || []).find((o) => !o.done && (o.side === 'ship' || o.side === 'both'));
  const urgent = open?.id === 'shelter' || (open?.id === 'scrubber_fixed' && sh.telemetry?.ppco2 > 6);
  const waitSign = open?.id === 'policy_signed' && signedAt === 'policy_signed';
  const floodComing = open?.id === 'letters_played' && !(sh.letters || []).some((L) => !L.played);
  guide($('#coach'), 'ship', s, floodComing ? { what: 'Everything from home is on its way at once.', how: 'The messages held behind the Sun are arriving. They will appear in the middle of the screen.', target: null }
    : waitSign ? { what: 'You have countersigned TERN’s rules.', how: 'Lantern’s signed copy is still on its way. Nothing else to do.', target: null } : null);
  $('#coach').classList.toggle('urgent', !!urgent);
  if (open && open.id !== lastObjective) {
    lastObjective = open.id;
    const pane = { hga_point: 'systems', pump_fixed: 'systems', scrubber_fixed: 'systems', shelter: 'radiation', policy_signed: 'tern', readback: 'lantern' }[open.id];
    if (pane) showTab(pane);
  }
  renderStatus(sh); renderSystems(sh); renderDose(sh); renderInbox(sh); renderPolicy(sh.policy); renderCrew(s.crew);
  renderPrompt(s.director.prompt); renderLetters(sh);
  $('#eta').textContent = to === 'home' ? `Reaches Houston in ${dur(g.lt_down_s)}` : `Lantern hears this in ${dur(g.lt_down_s)}`;
  $('#reason').textContent = L && !L.downlink_open ? 'No antenna is listening. It will wait aboard.' : '';
}

function renderStatus(sh) {
  const box = $('#status'), al = sh.alarms || [], u = meta?.units || {}, lab = meta?.labels || {};
  const chips = al.map((a) => el('span', { class: 'chip ' + a.level }, `${lab[a.channel] || a.channel}`, el('span', { class: 'num' }, `${num(a.value, 1)} ${(u[a.channel] || '').replace('uSv/h', 'µSv/h')}`)));
  if (sh.attitude) chips.push(el('span', { class: 'chip lantern' }, 'Storm attitude: tanks to the Sun, no link to Earth'));
  if (sh.shelter) chips.push(el('span', { class: 'chip lantern' }, 'In the storm shelter'));
  const key = chips.map((c) => c.textContent).join('|');
  if (box.dataset.k !== key) { box.dataset.k = key; box.innerHTML = ''; box.append(...chips); }
}

// ------------------------------------------------------------------ systems
const SYS = [
  { id: 'power', name: 'Power', ch: ['batt_soc', 'bus_v'], read: (t) => [['Battery', num(t.batt_soc, 0), '%'], ['Solar', num(t.solar_kw, 1), 'kW'], ['Bus', num(t.bus_v, 1), 'V']],
    acts: (m) => [['science', 'Science payload', m.science_on ? 'on' : 'off'], ['heaters_eco', 'Heater eco mode', m.heaters_eco ? 'on' : 'off'], ['breaker_reset', 'Cycle the load breaker', 'by hand']] },
  { id: 'thermal', name: 'Coolant', ch: ['loop_a_c', 'loop_b_c', 'cabin_c'], read: (t, m) => [['Loop A', num(t.loop_a_c, 1), '°C'], ['Loop B', num(t.loop_b_c, 1), '°C'], ['Running', m.active_loop, ''], ['Cabin', num(t.cabin_c, 1), '°C']],
    acts: () => [['pump_switch', 'Switch coolant loop'], ['heater_relay_open', 'Open the stuck heater relay', 'by hand']] },
  { id: 'co2', name: 'Air (CO₂)', ch: ['ppco2'], read: (t, m) => [['CO₂', num(t.ppco2, 2), 'mmHg'], ['Scrubber bed used', Math.round(m.bed_sat * 100), '%']],
    acts: () => [['scrubber_swap_bed', 'Swap the scrubber bed', 'by hand']] },
  { id: 'antenna', name: 'Antenna', ch: ['link_db'], read: (t, m) => [['Pointing error', num(m.hga_err_deg, 2), '°'], ['Link margin', num(t.link_db, 1), 'dB']],
    acts: () => [['hga_trim', 'Trim the antenna'], ['hga_lock', 'Engage the gimbal brake']] },
  { id: 'sensors', name: 'Sensors', ch: [], read: () => [], acts: () => [['sensor_recal', 'Recalibrate all sensors']] },
];
function renderSystems(sh) {
  const t = sh.telemetry || {}, m = sh.modes || {}, box = $('#systems'), al = sh.alarms || [];
  if (!box.dataset.built) {
    box.dataset.built = 1;
    for (const s of SYS) box.append(el('div', { class: 'sys', id: 'sys-' + s.id },
      el('header', {}, el('h2', {}, s.name), el('span', { class: 'badge go', 'data-st': '' }, 'Normal')),
      el('div', { class: 'readouts' }), el('div', { class: 'acts' })));
  }
  for (const s of SYS) {
    const node = $('#sys-' + s.id), worst = al.filter((a) => s.ch.includes(a.channel)).map((a) => a.level);
    const lvl = worst.includes('emergency') ? ['Emergency', 'alarm'] : worst.includes('warning') ? ['Warning', 'alarm'] : worst.includes('caution') ? ['Caution', 'lantern'] : ['Nominal', 'go'];
    const st = node.querySelector('[data-st]'); st.className = 'badge ' + lvl[1]; st.textContent = lvl[0];
    const r = node.querySelector('.readouts'), vals = s.read(t, m);
    if (r.children.length !== vals.length) { r.innerHTML = ''; vals.forEach(() => r.append(el('div', { class: 'readout' }, el('span', { class: 'k' }), el('span', { class: 'v' })))); }
    vals.forEach(([k, v, u], i) => { const d = r.children[i]; d.firstChild.textContent = k; d.lastChild.innerHTML = `${v}<span class="u">${u}</span>`; });
    const a = node.querySelector('.acts'), acts = s.acts(m), key = JSON.stringify(acts);
    if (a.dataset.k !== key) {
      a.dataset.k = key; a.innerHTML = '';
      for (const [act, label, note] of acts) a.append(el('button', { 'data-act': act, onclick: () => link.send('ship.action', { action: act }) }, label, note ? el('span', { class: 'm' }, note) : null));
    }
  }
}

// ------------------------------------------------------------------ radiation
let sciRad = false;
function renderDose(sh) {
  const d = sh.dose, rate = sh.telemetry?.dose_rate ?? 0;
  $('#bridge-dose').textContent = num(sh.telemetry?.dose_rate, 1);
  $('#bridge-total').textContent = num(d.total_msv, 1);
  $('#bridge-limit').textContent = `${num(d.fraction_of_limit * 100, 1)}% of ${num(d.career_limit_msv, 0)} mSv limit`;
  $('#dose-now').textContent = num(sh.telemetry?.dose_rate, 1);
  $('#dose-now').className = 'v ' + (rate > 1000 ? 'alarm' : rate > 300 ? 'lantern' : '');
  $('#dose-ctx').textContent = rate > 300 ? 'A solar particle storm is passing through the ship.' : 'Normal background: cosmic rays from outside the solar system.';
  $('#dose-total').textContent = `${num(d.total_msv, 1)} mSv`;
  $('#dose-bar').style.width = Math.min(100, d.fraction_of_limit * 100) + '%';
  $('#dose-bar').style.background = d.fraction_of_limit >= 1 ? 'var(--alarm)' : 'var(--haze)';
  $('#dose-limit').textContent = `${num(d.fraction_of_limit * 100, 1)}% of NASA's career limit (${d.career_limit_msv} mSv)`;
  const key = [d.sep_msv, d.gcr_msv, d.peak_usv_h].join();
  if ($('#dose-split').dataset.k !== key) {
    $('#dose-split').dataset.k = key;
    $('#dose-split').innerHTML = `<tr><td>From cosmic rays</td><td class="v">${num(d.gcr_msv, 1)}<span class="u">mSv</span></td></tr>
      <tr><td>From solar storms</td><td class="v">${num(d.sep_msv, 1)}<span class="u">mSv</span></td></tr>
      <tr><td>Highest rate so far</td><td class="v">${num(d.peak_usv_h, 1)}<span class="u">µSv/h</span></td></tr>`;
  }
  $('#shelter').textContent = sh.shelter ? 'Leave storm shelter' : 'Enter storm shelter';
  $('#shelter').className = rate > 300 && !sh.shelter ? 'warn' : '';
  if (!sciRad) { sciRad = true; science('shielding').then((n) => n && $('#sci-rad').append(n)); }
}

// ------------------------------------------------------------------ messages
function renderInbox(sh) {
  const box = $('#inbox'), cmds = new Set((sh.commands || []).map((c) => c.id));
  const items = [...(sh.inbox || []).map((m) => ({ ...m, t: m.created_et })), ...sent.map((m) => ({ ...m, t: m.et, mine: true }))].sort((a, b) => a.t - b.t);
  const key = items.map((m) => m.id + (cmds.has(m.id) ? 'c' : '')).join();
  if (box.dataset.k === key) return;
  box.dataset.k = key; box.innerHTML = '';
  if (!items.length) { box.append(el('div', { class: 'empty' }, 'Nothing from Lantern yet. Their messages arrive here, already minutes old.')); return; }
  for (const m of items) {
    if (m.mine) { box.append(el('div', { class: 'msg mine' }, el('div', { class: 'meta' }, el('span', {}, m.to === 'home' ? 'You, to Lila' : 'You, to Lantern'), el('span', {}, etIso(m.et))), m.text)); continue; }
    const kind = m.kind === 'alert' ? 'alert' : m.kind === 'command' ? 'cmd' : '';
    box.append(el('div', { class: 'msg ' + kind },
      el('div', { class: 'meta' }, el('span', {}, m.from + (m.kind === 'alert' ? ', alert' : m.kind === 'command' ? ', command' : '')),
        el('span', {}, `written ${etIso(m.created_et)}`), el('span', {}, `${dur(m.lt_s)} in transit`)),
      el('div', {}, m.text),
      m.crew_judgment && m.crew_judgment.verdict !== 'ok' ? el('div', { class: 'crew-note' }, `Crew held this order: ${m.crew_judgment.reason}`) : null,
      m.audio_id ? el('button', { class: 'ghost small', onclick: () => playAudio(m.audio_id) }, 'Play voice') : null,
      m.kind === 'command' && cmds.has(m.id) ? el('button', { class: 'primary', style: 'margin-top:6px', onclick: () => link.send('ship.action', { action: 'execute_command', args: { id: m.id } }) }, 'Carry out this command') : null));
  }
  box.parentElement.scrollTop = box.parentElement.scrollHeight;
}

const PERM = { auto: 'Acts alone', ask: 'Asks first', forbid: 'Never' };
function renderPolicy(p) {
  if (!p) return;
  const key = JSON.stringify([p.version, p.signed_by, p.rules.map((r) => r.permission)]);
  if ($('#policy').dataset.k === key) return; $('#policy').dataset.k = key;
  const signed = p.signed_by.includes('ship');
  $('#policy').innerHTML = '';
  $('#policy').append(el('div', { class: 'policy-card' }, p.rules.map((r) => el('div', { class: 'rule' }, el('span', { class: 'grow' }, r.label), el('span', { class: 'perm ' + r.permission }, PERM[r.permission])))),
    el('div', { class: 'row', style: 'margin-top:10px' }, el('span', { class: 'grow small dim' }, `Version ${p.version}. Signed by ${p.signed_by.map((x) => (x === 'ship' ? 'you' : 'Lantern')).join(' and ') || 'nobody yet'}.`),
      el('button', { id: 'countersign', disabled: signed, onclick: () => { link.send('policy.sign'); signedAt = lastObjective; } }, signed ? 'Countersigned' : 'Countersign')));
}

// ------------------------------------------------------------------ Lila
let promptKey = null;
function renderPrompt(p) {
  const box = $('#prompt');
  if (!p) { box.classList.add('hidden'); promptKey = null; return; }
  if (promptKey === p.id) return; promptKey = p.id;
  box.classList.remove('hidden'); box.innerHTML = '';
  const ta = el('textarea', { rows: 3, 'aria-label': 'Your answer to Lila' }, p.suggestion || '');
  const send = () => { const t = ta.value.trim(); if (!t) return; link.send('msg.send', { kind: 'letter', text: t, to: 'home' }); sent.push({ id: 'l' + Date.now(), text: t, to: 'home', et: S.et }); box.classList.add('hidden'); };
  const close = () => { link.send('prompt.dismiss'); box.classList.add('hidden'); };
  const eta = el('span', { class: 'foot grow', style: 'margin:0' }, `She hears it in ${dur(S?.geometry?.lt_down_s)}.`);
  const writing = el('div', { class: p.suggestion ? '' : 'hidden' },
    p.suggestion ? el('div', { class: 'foot', style: 'margin:0 0 6px' }, 'Some words to start from. Change them, or write your own.') : null, ta,
    el('div', { class: 'row' }, el('button', { onclick: send }, 'Send to Lila'), eta, el('button', { class: 'ghost', onclick: close }, 'Not now')));
  // a prompt without suggested words is the narrator talking: offer to write, don't demand it
  const offer = p.suggestion ? null : el('div', { class: 'row' }, el('button', { onclick: () => { offer.remove(); writing.classList.remove('hidden'); ta.focus(); } }, 'Write to Lila'),
    el('span', { class: 'grow' }), el('button', { class: 'ghost', onclick: close }, 'Close'));
  box.append(el('div', { class: 'paper' }, el('div', { class: 'from' }, p.suggestion ? 'Write to Lila, at home in Houston' : 'Home'), el('div', { class: 'body' }, p.text), offer, writing));
}

function renderLetters(sh) {
  const unplayed = (sh.letters || []).filter((L) => !L.played), box = $('#letters');
  box.classList.toggle('hidden', !unplayed.length);
  if (!unplayed.length) document.body.classList.remove('flooding');
  const key = unplayed.map((L) => L.id).join();
  if (box.dataset.k === key) return; box.dataset.k = key;
  box.innerHTML = '';
  if (!unplayed.length) return;
  const flood = unplayed.filter((L) => L.flood).length > 1;
  box.classList.toggle('flood', flood); document.body.classList.toggle('flooding', flood);
  box.append(el('h2', {}, flood ? `${unplayed.length} messages from home arrived at once` : unplayed.length > 1 ? `${unplayed.length} messages from home` : 'A message from home'),
    ...(flood ? [el('p', { class: 'haze small' }, 'They were held behind the Sun. Open them in whatever order you like.')] : []),
    ...unplayed.map((L) => el('button', { class: 'envelope ' + (L.from === 'lila' ? 'lila' : 'theo'), onclick: () => openLetter(L) },
      el('span', { class: 'seal' }), el('span', { class: 'grow' }, el('span', { class: 'who' }, L.from === 'lila' ? 'Lila' : 'Theo'), el('br'),
        el('span', { class: 'when' }, `recorded ${L.created_local}`)))));
}
function openLetter(L) {
  link.send('letter.play', { id: L.id });
  if (score.ctx) quindar(score.ctx, true);
  const close = () => s.remove();
  const s = el('div', { class: 'scrim', onclick: (e) => e.target === s && close() },
    el('div', { class: 'paper letter-sheet', role: 'dialog', 'aria-modal': 'true' },
      el('div', { class: 'from' }, `${L.from === 'lila' ? 'Lila' : 'Theo'}, ${L.created_local}`), el('div', { class: 'body' }, L.text),
      el('div', { class: 'row', style: 'margin-top:16px' }, el('span', { class: 'foot grow', style: 'margin:0' }, `It waited ${dur(L.delivered_et - L.created_et)} to reach you.`),
        el('button', { onclick: close }, 'Fold it away'))));
  document.body.append(s); s.querySelector('button').focus();
}

// ------------------------------------------------------------------ the crew
// Fatigue, stress, trust and morale as short bars, always with a word (never colour alone).
const CSTAT = [['fatigue', 'Tiredness', ['rested', 'a bit tired', 'tired', 'exhausted'], true],
  ['stress', 'Stress', ['calm', 'alert', 'strained', 'overwhelmed'], true],
  ['trust_ground', 'Trusts Houston', ['barely', 'somewhat', 'mostly', 'fully'], false],
  ['morale', 'Morale', ['low', 'flat', 'good', 'high'], false]];
const cword = (v, w) => w[Math.max(0, Math.min(3, Math.floor((v ?? 0) * 4)))];
function renderCrew(c) {
  if (c?.members) {
    const key = JSON.stringify(c.members.map((m) => [m.name, m.task?.label, m.asleep]));
    if ($('#bridge-crew').dataset.k !== key) {
      $('#bridge-crew').dataset.k = key;
      $('#bridge-crew').replaceChildren(...c.members.map((m) => el('div', { class: 'bridge-person' },
        el('span', {}, m.name), el('span', { class: 'task' }, m.task?.label || (m.asleep ? 'Asleep' : 'Off duty')))));
    }
  }
  if (!c?.members) return;
  $('#crew-mode').textContent = c.enabled ? 'Crew act on their own' : 'Crew AI off';
  const box = $('#crew-cards');
  const order = (m) => (m.role === 'Commander' ? 0 : m.seat === 'engineer' ? 1 : 2);    // the Commander first
  for (const m of [...c.members].sort((a, b) => order(a) - order(b))) {
    let card = $('#cc-' + m.id);
    if (!card) {
      const sel = el('select', { 'aria-label': `Ask ${m.first}`, onchange: (e) => { if (e.target.value) link.send('crew.assign', { crew: m.id, task: e.target.value }); e.target.value = ''; } },
        el('option', { value: '' }, `Ask ${m.first} to…`), (m.ask || []).map((a) => el('option', { value: a.id }, a.label)));
      card = el('div', { class: 'crew-card', id: 'cc-' + m.id },
        el('header', {}, el('b', { class: 'name' }, m.name), el('span', { class: 'role' }, m.role), el('span', { class: 'badge plain tag' })),
        el('div', { class: 'task' }, el('span', { class: 'tl' }), el('span', { class: 'eta small dim' })),
        el('div', { class: 'bar task-bar' }, el('i', { style: 'width:0' })),
        el('div', { class: 'cstats' }, CSTAT.map(([k, label]) => el('div', { class: 'cstat', 'data-k': k }, el('span', { class: 'k' }, label), el('span', { class: 'cbar' }, el('i')), el('span', { class: 'w' })))),
        el('div', { class: 'said' }), el('div', { class: 'ask-row' }, sel));
      box.append(card);
    }
    const you = m.human && m.seat === SEAT, tag = card.querySelector('.tag');
    tag.textContent = you ? 'You' : m.human ? 'Played by a person' : 'AI'; tag.className = 'badge plain tag ' + (you ? 'you' : m.human ? 'person' : 'ai');
    const t = m.task, label = m.human ? (you ? 'That’s you.' : 'Another player is at this seat.') : t ? t.label : m.asleep ? 'Asleep' : 'Off duty';
    card.querySelector('.tl').textContent = label;
    card.querySelector('.eta').textContent = t?.eta_s ? `${dur(t.eta_s)} left` : '';
    card.querySelector('.task-bar').classList.toggle('hidden', !(t && t.eta_s != null));
    card.querySelector('.task-bar i').style.width = `${Math.round((t?.progress || 0) * 100)}%`;
    for (const [k, , words, badHigh] of CSTAT) {
      const row = card.querySelector(`[data-k="${k}"]`), v = m[k] ?? 0;
      row.querySelector('.cbar i').style.width = `${Math.round(v * 100)}%`;
      row.querySelector('.w').textContent = cword(v, words);
      row.classList.toggle('hi', badHigh ? v >= 0.75 : v < 0.25);
    }
    const said = m.said?.text ? `“${m.said.text}”` : '';
    if (card.querySelector('.said').textContent !== said) card.querySelector('.said').textContent = said;
    card.querySelector('.ask-row').classList.toggle('hidden', m.human || !c.enabled);
  }
}
function crewLine(e) {
  const log = $('#crew-said'); log.querySelector('.empty')?.remove();
  log.append(el('div', { class: 'msg crew' }, el('div', { class: 'meta' }, el('span', {}, e.name), el('span', {}, e.role), el('span', {}, S ? etIso(S.et) : '')), e.text));
  while (log.children.length > 60) log.firstChild.remove();
  log.parentElement.scrollTop = log.parentElement.scrollHeight;
  unread('crew'); toast(`${e.name.replace(/^Dr\. /, '').split(' ')[0]}: ${e.text}`, 'info', 6000);
}

// ------------------------------------------------------------------ events
function onEvent(e) {
  if (e.kind === 'crew_say') crewLine(e);
  if (e.kind === 'tern') ternLine(e.text, e.level);
  if (e.kind === 'message') { unread('lantern'); toast(`${e.message.from}: ${e.message.text.slice(0, 140)}`, e.message.kind === 'alert' ? 'warning' : 'info', 9000); if (score.ctx) quindar(score.ctx); }
  if (e.kind === 'tern_decision' && e.decision.outcome === 'broke_policy') toast(`TERN broke its rules: ${e.decision.rationale}`, 'warning', 12000);
  if (e.kind === 'objective') toast('Done: ' + e.text, 'info', 4000);
  if (e.kind === 'receipt') toast(`Houston received your message ${e.ref}.`, 'info', 3000);
  if (e.kind === 'ping') toast(`Someone on Earth waved at you, ${dur(e.lt_s)} ago.`, 'info', 3000);
  if (e.kind === 'slowdown') toast('Time slowed down: TERN needs you.', 'warning', 5000);
  if (e.kind === 'debrief') debrief(e.report);
  if (e.kind === 'reality') realityToast(e.reality);          // Phase 6, never scored
  if (e.kind === 'callout') { seenTrace.add(e.callout.id); addTrace($('#tern-log'), e.callout); unread('tern'); toast('Your design: ' + e.callout.text, 'info', 9000); }
}
const seenTrace = new Set();
function ternLine(text, level = 'info', who = 'TERN') {
  const log = $('#tern-log'); log.querySelector('.empty')?.remove();
  log.append(el('div', { class: 'msg ' + (who === 'TERN' ? 'tern' : 'mine') }, el('div', { class: 'meta' }, el('span', {}, who), el('span', {}, S ? etIso(S.et) : '')), text));
  log.parentElement.scrollTop = log.parentElement.scrollHeight;
  if (who === 'TERN') { unread('tern'); if (level !== 'info') toast('TERN: ' + text, level, 9000); if (score.enabled) speak(text); }
}
function ternReply(m) { lastTern = m; $('#tern-mode').textContent = m.source === 'llm' ? 'Local model, checked' : 'Rules, checked'; ternLine(m.text); }
let debriefShown = false;
function debrief(r) { if (debriefShown && !r?.debrief) return; debriefShown = true; showDebrief(r); }

// ------------------------------------------------------------------ inputs
$('#shelter').onclick = () => link.send('ship.action', { action: S?.ship?.shelter ? 'shelter_exit' : 'shelter_enter' });
$$('#to button').forEach((b) => (b.onclick = () => { to = b.dataset.v; $$('#to button').forEach((x) => x.classList.toggle('on', x === b));
  $('#text').placeholder = to === 'home' ? 'Lila, it’s Mom. …' : 'Lantern, Meridian. …'; if (S) onState(S); }));
const transmit = () => { const t = $('#text').value.trim(); if (!t) return;
  link.send('msg.send', { kind: to === 'home' ? 'letter' : 'text', text: t, to }); sent.push({ id: 's' + Date.now(), text: t, to, et: S?.et || 0 }); $('#text').value = ''; if (S) renderInbox(S.ship); };
$('#send').onclick = transmit;
$('#text').onkeydown = (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) transmit(); };
$('#rec').onclick = async () => {
  if (!rec.supported) return toast('No microphone available in this browser.', 'error');
  if (!recording) { await rec.start(); recording = true; $('#rec').textContent = 'Stop and send'; $('#rec').classList.add('warn'); return; }
  recording = false; $('#rec').textContent = 'Record voice'; $('#rec').classList.remove('warn');
  const r = await rec.stop(); if (!r?.ok) return toast(r?.error || 'The recording failed.', 'error');
  link.send('msg.send', { kind: to === 'home' ? 'letter' : 'voice', text: r.transcript || $('#text').value.trim() || '(voice note)', audio_id: r.id, audio_bytes: r.bytes, to });
  sent.push({ id: 'v' + Date.now(), text: r.transcript || '(voice note)', to, et: S?.et || 0 });
};
const ask = () => { const t = $('#ask').value.trim(); if (!t) return; ternLine(t, 'info', 'You'); link.send('ai.ask', { text: t }); $('#ask').value = ''; };
$('#askb').onclick = ask; $('#ask').onkeydown = (e) => e.key === 'Enter' && ask();
$('#dbg').onclick = () => drawer('dbg', 'What TERN looked up', () => el('pre', {}, JSON.stringify(lastTern || { note: 'Ask TERN something first.' }, null, 2)));
$('#prov').onclick = provenanceDrawer; $('#mcard').onclick = () => modelCardDrawer(); $('#sci').onclick = scienceDrawer;
$('#help').onclick = () => briefing(SEAT, true);
$('#aar').onclick = () => fetch(roomUrl('/api/after-action')).then((r) => r.json()).then(debrief);
$('#audio').onclick = () => { if (score.enabled) { score.stop(); $('#audio').textContent = 'Turn sound on'; } else { score.start(); $('#audio').textContent = 'Turn sound off'; } };
const seg = (id, fn) => $$(`#${id} button`).forEach((b) => (b.onclick = () => { $$(`#${id} button`).forEach((x) => x.classList.toggle('on', x === b)); fn(b.dataset.v); }));
seg('mode', (v) => view?.setMode(v)); seg('look', (v) => view?.setLook(v));
