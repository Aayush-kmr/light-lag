// LIGHT-LAG client core: WebSocket state API, formatting, the guide bar,
// role briefings, drawers and toasts. Plain ES modules, no build step.

export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'html') e.innerHTML = v;
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
export const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// ---------------------------------------------------------------- formats
export function dur(s, opts = {}) {
  if (s === null || s === undefined || !isFinite(s)) return '–';
  const neg = s < 0; s = Math.abs(s);
  let out;
  if (s < 60) out = `${s.toFixed(s < 10 && opts.precise ? 1 : 0)} s`;
  else if (s < 3600) out = `${Math.floor(s / 60)} min ${String(Math.floor(s % 60)).padStart(2, '0')} s`;
  else if (s < 86400 * 2) out = `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')} min`;
  else out = `${(s / 86400).toFixed(1)} days`;
  return (neg ? '−' : '') + out;
}
export const num = (v, d = 1) => (v === null || v === undefined || !isFinite(v) ? '–' : Number(v).toFixed(d));
export const utc = (iso) => (iso || '').replace('T', ' ').replace('Z', '');
export const etDate = (et) => new Date(Date.UTC(2000, 0, 1, 12) + (et - 69.184) * 1000);
export const etIso = (et) => etDate(et).toISOString().slice(0, 16).replace('T', ' ');
/** How old a number is, relative to the unavoidable light time: 0 fresh .. 3 stale. */
export function ageClass(age, lt) {
  if (age == null) return 'age-3';
  const extra = age - (lt || 0);
  return extra < 60 ? 'age-0' : extra < 600 ? 'age-1' : extra < 3600 ? 'age-2' : 'age-3';
}

// ---------------------------------------------------------------- socket
// ---------------------------------------------------------------- rooms (Phase 5)
// Every game is a room with a four-letter code; the old single game is MAIN.
// Every API call and in-app link carries the code, so a team on several devices
// stays in one mission and two groups never collide.
export const ROOM = (new URLSearchParams(location.search).get('room') || 'MAIN').toUpperCase().replace(/[^A-Z]/g, '') || 'MAIN';
export const roomUrl = (path) => (ROOM === 'MAIN' ? path : `${path}${path.includes('?') ? '&' : '?'}room=${ROOM}`);
export let seatToken = null;           // issued on WebSocket join; lets this seat fly the mission again
export const inviteUrl = (seatPath = '/spectator/') => new URL(roomUrl(seatPath), location.origin).href;
function roomLinks(root = document) {
  if (ROOM === 'MAIN') return;
  root.querySelectorAll('a[href^="/control/"], a[href^="/ship/"], a[href^="/spectator/"], a[href^="/factsheet/"]').forEach((a) => {
    if (!/[?&]room=/.test(a.getAttribute('href'))) a.setAttribute('href', roomUrl(a.getAttribute('href')));
  });
}
if (document.readyState === 'loading') addEventListener('DOMContentLoaded', () => roomLinks()); else roomLinks();
export function roomEnded(text) {
  document.querySelector('.room-ended')?.remove();
  document.body.append(el('div', { class: 'room-ended', role: 'alertdialog', 'aria-modal': 'true' },
    el('div', {}, el('h2', {}, 'This mission room has ended'), el('p', {}, text || `Room ${ROOM} is no longer running. Rooms close after a while with nobody connected.`),
      el('a', { class: 'primary', href: '/' }, 'Back to the start'))));
}
// A small HUD chip: the room code and a copy-invite-link control.
export function roomChip(seatPath = '/spectator/') {
  const btn = el('button', { class: 'room-chip', title: 'Copy a link that opens this mission on another device', 'aria-label': `Room ${ROOM}. Copy invite link` },
    el('span', { class: 'room-chip-code' }, ROOM === 'MAIN' ? 'Room MAIN' : `Room ${ROOM}`), el('span', { class: 'room-chip-copy' }, 'Copy invite link'));
  btn.onclick = async () => {
    const url = inviteUrl(seatPath);
    try { await navigator.clipboard.writeText(url); toast(`Invite link copied: ${url}`, 'info', 4000); }
    catch { prompt('Copy this link to invite someone to the mission:', url); }
  };
  return btn;
}

export class Link {
  constructor(role, handlers = {}) {
    this.role = role; this.h = handlers; this.state = null; this.hello = null; this.backoff = 500;
    this.connect();
  }
  connect() {
    const p = location.protocol === 'https:' ? 'wss' : 'ws';
    const host = new URLSearchParams(location.search).get('server') || location.host;
    const ws = (this.ws = new WebSocket(`${p}://${host}/ws/${this.role}?room=${ROOM}`));
    ws.onopen = () => { this.backoff = 500; this.h.open?.(); };
    ws.onclose = () => { if (this.ended) return; this.h.close?.(); setTimeout(() => this.connect(), this.backoff); this.backoff = Math.min(this.backoff * 2, 5000); };
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === 'state') { this.state = m; this.h.state?.(m); }
      else if (m.type === 'hello') { this.hello = m; seatToken = m.token || null; this.h.hello?.(m); }
      else if (m.type === 'error' && m.code === 'no_room') { this.ended = true; roomEnded(); }
      else if (m.type === 'event') this.h.event?.(m);
      else if (m.type === 'ai.reply') this.h.ai?.(m);
      else if (m.type === 'reply') this.h.reply?.(m);
    };
  }
  send(type, data = {}) { if (this.ws?.readyState === 1) this.ws.send(JSON.stringify({ type, ...data })); }
}

// ---------------------------------------------------------------- toasts
let toastBox;
export function toast(text, level = 'info', ms = 6000) {
  if (!toastBox) {
    toastBox = el('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' });
    const stack = document.querySelector('.room-stack');
    if (stack) stack.insertBefore(toastBox, stack.querySelector('.room-task')); else document.body.append(toastBox);
  }
  const t = el('div', { class: `toast ${level}` }, text);
  toastBox.append(t);
  setTimeout(() => t.remove(), ms);
  while (toastBox.children.length > 5) toastBox.firstChild.remove();
}

// ---------------------------------------------------------------- act cards
let lastScene = null;
export function sceneCard(dir) {
  const sc = dir?.scene; if (!sc || sc.id === lastScene) return;
  lastScene = sc.id;
  document.querySelector('.card')?.remove();
  const c = el('div', { class: 'card' }, el('div', { class: 'act' }, `Act ${sc.act}: ${dir.act_title || sc.act_title || ''}`),
    el('h1', {}, sc.title.replace(/^.*\/\s*/, '')), el('p', {}, sc.card || ''));
  const stack = document.querySelector('.room-stack');
  if (stack) stack.prepend(c); else document.body.append(c);
  setTimeout(() => { c.style.opacity = '0'; setTimeout(() => c.remove(), 900); }, 6000);
}
export function objectivesList(dir) {
  return el('ul', { class: 'objectives' }, (dir?.objectives || []).map((o) => el('li', { class: o.done ? 'done' : '' }, o.text)));
}

// ---------------------------------------------------------------- the guide
// For each objective: what to do, in plain words, and which control to point at.
const GUIDE = {
  control: {
    comm_check: ['Say hello to Meridian.', 'Type a short check-in under Send, then press Transmit.', '#send-panel'],
    storm_warning: ['Warn Meridian about the flare.', 'Choose Alert under Send. If no antenna is listening, book an emergency pass first.', '#send-panel'],
    family_answered: ['Theo Reyes is asking about his wife.', 'Answer him on the family line, under From Meridian.', '#family'],
    policy_signed: ["Get TERN's final rules aboard before commanding stops.", "Open TERN's rules below, review them, then press Sign and send.", '[data-tab=rules]'],
    control_letter: ['Leave Imani something to hear after the silence.', 'Write it under Send and press Transmit. Messages still go through; only commands stop.', '#send-panel'],
  },
  ship: {
    readback: ['Read Lantern’s instruction back to them.', 'Repeat the key points of their message in your reply, then press Transmit.', '#text'],
    hga_point: ['Your antenna is drifting off Earth.', 'Systems tab, Antenna: engage the gimbal brake.', '[data-act=hga_lock]'],
    goodnight_live: ['Lila is on the line.', 'Answer her on the paper card at the bottom of the screen.', '#prompt'],
    answer_lila: ['Lila is waiting for you.', 'Open her letter on the left, then answer her on the paper card.', '#prompt'],
    explain_lila: ['Tell Lila what is about to happen.', 'Write to her on the paper card at the bottom of the screen.', '#prompt'],
    pump_fixed: ['Coolant loop A is overheating.', 'Systems tab, Thermal: switch coolant loop.', '[data-act=pump_switch]'],
    shelter: ['Protons are on their way.', 'Get into the storm shelter before they arrive.', '#shelter'],
    policy_signed: ["Countersign TERN's rules for the silence.", 'TERN tab: press Countersign.', '#countersign'],
    scrubber_fixed: ['The CO₂ scrubber valve is stuck.', 'Systems tab, CO₂: swap the scrubber bed by hand.', '[data-act=scrubber_swap_bed]'],
    letters_played: ['Everything from home just arrived at once.', 'Open them in whatever order you choose.', '#letters'],
  },
};
// Whole phases that are not about one objective (Phase 4).
const PHASE_GUIDE = {
  review: ['The review board is checking your design.', 'Five reviewers, one at a time. A pass launches; a failure sends everyone back to the bay and costs launch-window days.'],
  debrief: ['The mission is over.', 'Read the debrief: each design choice, the moment it mattered, and what it changed. Fly it again from the bottom of the page.'],
  scrubbed: ['The launch window closed.', 'The next one opens about 26 months later. Start over from the landing page.'],
};
const IDLE = {
  control: ['Nothing needs you right now.', 'Watch the light ribbon, read the ship’s numbers as they age, or ask FIDO a question.'],
  ship: ['Nothing needs you right now.', 'Drag to look around. The line is your antenna beam to Earth; the dots on it are messages in flight.'],
};

let lastHint = null;
export function guide(node, role, s, extra = null) {
  if (!node) return;
  let what, how, target = null, urgent = false;
  if (extra) ({ what, how, target, urgent } = extra);
  else if (PHASE_GUIDE[s?.phase]) [what, how] = PHASE_GUIDE[s.phase];
  else {
    const open = (s?.director?.objectives || []).find((o) => !o.done && (o.side === role || o.side === 'both'));
    const g = open && GUIDE[role][open.id];
    if (g) [what, how, target] = g; else if (open) { what = open.text; how = ''; } else [what, how] = IDLE[role];
  }
  const sc = s?.director?.scene;
  const where = sc ? `Act ${sc.act}, ${sc.title.replace(/^.*\/\s*/, '')}` : '';
  const key = what + how + target + urgent + where;
  if (node.dataset.k !== key) {
    node.dataset.k = key;
    node.classList.add('coach'); node.classList.toggle('urgent', !!urgent);
    node.innerHTML = '';
    node.append(el('span', { class: 'mark' }), el('span', { class: 'what' }, what), el('span', { class: 'how' }, how), el('span', { class: 'where' }, where));
  }
  pointAt(target);
}
export function pointAt(sel) {
  if (lastHint) lastHint.classList.remove('hint-target');
  lastHint = null;
  if (!sel) return;
  let t = document.querySelector(sel);
  if (t && t.offsetParent === null) {               // hidden inside a tab: point at the tab instead
    const pane = t.closest('[data-pane]');
    t = pane ? document.querySelector(`[data-tab="${pane.dataset.pane}"]`) : null;
  }
  if (t) { t.classList.add('hint-target'); lastHint = t; }
}

// ---------------------------------------------------------------- tabs
export function tabs(root, onChange) {
  const btns = $$('[data-tab]', root), panes = $$('[data-pane]', root);
  const show = (id) => {
    btns.forEach((b) => { const selected = b.dataset.tab === id; b.classList.toggle('on', selected); b.setAttribute('role', 'tab'); b.setAttribute('aria-selected', String(selected)); });
    panes.forEach((p) => p.classList.toggle('hidden', p.dataset.pane !== id));
    onChange?.(id);
  };
  btns.forEach((b) => (b.onclick = () => show(b.dataset.tab)));
  show(btns[0]?.dataset.tab);
  return show;
}

// ---------------------------------------------------------------- briefing
const BRIEF = {
  control: { title: 'You are Flight.', lede: 'Lantern Control, Houston. You can see more than anyone, but everything you see is already old.', points: [
    ['The ribbon across the top is the gap to Meridian-1.', 'Everything you send crawls across it at the speed of light. The big number is how long that takes today.'],
    ['Received telemetry is the ship as it was.', 'Each reading keeps its sampling age, including during a blackout.'],
    ['Messages leave only when an antenna can hear the ship.', 'The Deep Space Network has three. If none is free, book an emergency pass.'],
    ['First you design the mission with the team; a review board checks it before launch.', 'In flight, lines marked Your design show which choice decided what. Press why for the numbers.'],
    ['The bar under the clock says what to do next.', 'The relevant control is outlined.']] },
  scientist: { title: 'You are the Mission Scientist.', lede: 'Lantern Science, Houston. You decide what this mission is for, and you see the ship only as it was.', points: [
    ['The mission exists to bring data home.', 'Every instrument costs mass, power and money, and its data has to fit through the antenna.'],
    ['Downlink is the bottleneck.', 'The Deep Space Network hears the ship only during passes. What does not fit waits, and old data can expire.'],
    ['You share this console with Flight.', 'If nobody has taken the Flight Director seat, the AI plays it and you may make its calls.'],
    ['A review board checks the design before launch.', 'In flight, lines marked Your design show what your payload and radio choices did. Press why for the numbers.'],
    ['The bar under the clock says what to do next.', 'The relevant control is outlined.']] },
  engineer: { title: 'You are Flight Engineer Daniel Okafor.', lede: 'Aboard Meridian-1 with three crewmates. Power, life support and every repair are yours.', points: [
    ['Drag to look around.', 'The line is the antenna’s beam to Earth. Dots on it are messages in flight.'],
    ['The side panel holds the ship’s systems.', 'Battery, coolant loops, CO2 scrubbers, the antenna. When something breaks, you are closer to it than Houston is.'],
    ['TERN, the onboard AI, works with you.', 'It acts within the rules the team agreed on, and tells you when it had to break them.'],
    ['You share this console with the Commander.', 'If nobody has taken the Commander seat, the AI plays it and you may make its calls.'],
    ['A review board checks the power design before launch.', 'In flight, lines marked Your design show what your array and battery did when something broke. Press why for the numbers.']] },
  ship: { title: 'You are Commander Imani Reyes.', lede: 'Aboard Meridian-1 with a crew of four. You see your ship as it is right now, and Earth only as it was minutes ago.', points: [
    ['Drag to look around.', 'The line is your antenna’s beam to Earth. Dots on it are messages in flight.'],
    ['TERN, the onboard AI, watches the ship.', 'It acts within the rules you and Lantern agree on, and tells you when it had to break them.'],
    ['The Flight Engineer station holds systems, radiation and messages.', 'Letters from Lila arrive on paper.'],
    ['A review board checks the design before launch.', 'In flight, lines marked Your design show which choice decided what. Press why for the numbers.'],
    ['The guide at the top says what to do next.', 'The relevant control is outlined.']] },
};
export function briefing(role, force = false) {
  const key = 'lightlag.briefed.' + role;
  try { if (!force && localStorage.getItem(key)) return; } catch { /* storage may be unavailable */ }
  const b = BRIEF[role]; if (!b) return;
  const points = document.body.classList.contains('immersive-mode') ? [
    ['Click a screen to work.', 'The camera approaches the monitor. Back to my station, or Escape, returns you to the room. Drag to look around. My station brings you back to your seat; Whole room (Whole cabin aboard) steps back to see everything.'],
    ...b.points.slice(1, -1),
    ['Your next objective appears below the console.', 'Click it to reach the relevant instrument. All screens lists every screen and the dashboard view. Effects switches between full and low graphics.'],
  ] : b.points;
  const close = () => { s.remove(); try { localStorage.setItem(key, '1'); } catch { /* ignore */ } };
  const s = el('div', { class: 'scrim', onclick: (e) => e.target === s && close() },
    el('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true' }, el('h1', {}, b.title), el('div', { class: 'lede' }, b.lede),
      el('ol', {}, points.map(([h, t]) => el('li', {}, el('b', {}, h), ' ', el('span', { class: 'haze' }, t)))),
      el('button', { class: 'primary', onclick: close }, 'Take your seat')));
  document.body.append(s);
  s.querySelector('button').focus();
}

// ---------------------------------------------------------------- drawers
export function drawer(id, title, render) {
  id = 'drawer-' + id;
  let d = document.getElementById(id);
  if (!d) {
    d = el('div', { class: 'drawer', id, role: 'dialog' }, el('div', { class: 'row' }, el('h1', { class: 'grow' }, title),
      el('button', { class: 'ghost', onclick: () => d.classList.remove('open') }, 'Close')), el('div', { class: 'sep' }), el('div', { class: 'body' }));
    document.body.append(d);
  } else d.querySelector('h1').textContent = title;
  d.classList.add('open');
  Promise.resolve(render()).then((node) => { const body = d.querySelector('.body'); body.innerHTML = ''; body.append(node); });
}
export async function provenanceDrawer() {
  drawer('prov', 'Where the data comes from', async () => {
    const p = await (await fetch('/api/provenance')).json();
    const label = { used: ['In use', 'go'], fetch: ['Downloaded before play', 'signal'], planned: ['Planned', 'lantern'] };
    return el('div', { class: 'list' }, p.sources.map((s) => el('div', { class: 'msg' },
      el('div', { class: 'row' }, el('b', { class: 'grow' }, s.name), el('span', { class: 'badge ' + label[s.status][1] }, label[s.status][0])),
      el('div', { class: 'haze' }, s.use), el('div', { class: 'meta' }, el('span', {}, s.provider), el('span', {}, s.terms)),
      el('div', { class: 'meta' }, s.url), s.note ? el('div', { class: 'meta lantern' }, s.note) : null)));
  });
}
export async function modelCardDrawer() {
  drawer('mcard', 'The AI on board', async () => {
    const r = await (await fetch('/api/model-card')).json();
    const h = await (await fetch('/api/health')).json();
    const a = r.anomaly_eval;
    return el('div', { class: 'stack' },
      el('table', { class: 'kv' }, ...Object.values(h.agents).map((g) => el('tr', {}, el('td', {}, g.name), el('td', { class: 'v' }, `${g.model}, ${g.llm_available ? 'running locally' : 'fallback'}`))),
        el('tr', {}, el('td', {}, 'Anomaly detector'), el('td', { class: 'v' }, h.anomaly_detector)),
        el('tr', {}, el('td', {}, 'Internet reachable'), el('td', { class: 'v' }, h.airplane?.internet_reachable ? 'yes' : 'no (offline)'))),
      a ? el('div', {}, el('h2', {}, 'Anomaly model, held-out test'), el('table', { class: 'kv' },
        ...['tune_types', 'held_out_types', 'all'].map((g) => el('tr', {}, el('td', {}, g.replace(/_/g, ' ')), el('td', { class: 'v' }, `precision ${a.results[g].precision}, recall ${a.results[g].recall}`))))) : null,
      el('details', {}, el('summary', {}, 'Raw reports'), el('pre', {}, JSON.stringify(r, null, 2))));
  });
}
let cards = null;
export async function science(id) {
  cards = cards || (await (await fetch('/api/science-cards')).json()).cards;
  const c = cards.find((x) => x.id === id || x.trigger === id); if (!c) return null;
  return el('div', { class: 'science' }, el('b', {}, c.title), el('div', {}, c.body), el('div', { class: 'src' }, 'Source: ' + c.source));
}
export async function scienceDrawer() {
  cards = cards || (await (await fetch('/api/science-cards')).json()).cards;
  drawer('sci', 'The science behind it', () => el('div', { class: 'stack' }, cards.map((c) =>
    el('div', { class: 'science' }, el('b', {}, c.title), el('div', {}, c.body), el('div', { class: 'src' }, 'Source: ' + c.source)))));
}

export function linkState(link) {
  if (!link) return ['Waiting', '', ''];
  if (link.blackout) return ['Blackout', 'alarm', 'The Sun is between Earth and the ship.'];
  if (link.uplink_open && link.downlink_open) return [link.moratorium ? 'Open, commands held' : 'Open', link.moratorium ? 'lantern' : 'go', link.reason];
  if (link.uplink_open) return ['Earth can send', 'lantern', link.reason];
  if (link.downlink_open) return ['Ship can send', 'lantern', link.reason];
  return ['No antenna', 'lantern', link.reason];
}
// Message colors share the client's token palette; only alerts use caution ink.
const kindTokens = { text: '--phosphor', voice: '--phosphor', alert: '--lantern', command: '--signal', policy: '--signal',
  policy_answer: '--signal', policy_request: '--signal', letter: '--phosphor', telemetry: '--dim', status: '--dim', tern_log: '--dim', ping: '--signal' };
export const KIND_COLORS = Object.fromEntries(Object.entries(kindTokens).map(([k, token]) => [k, getComputedStyle(document.body).getPropertyValue(token).trim()]));
export const KIND_NAMES = { text: 'message', voice: 'voice note', alert: 'alert', command: 'command', policy: 'TERN rules', policy_answer: 'authorization',
  policy_request: 'request', letter: 'letter', telemetry: 'telemetry', status: 'receipt', tern_log: 'TERN log', ping: 'hello' };

// ---------------------------------------------------------------- reality (Phase 6)
// "Meanwhile, in reality": real events from the same 2020 window, once each, never
// scored. A note to the players about the real world, so it carries no light delay.
export function realityNode(r, { long = false } = {}) {
  const items = r?.items || [];
  const line = (it) => el('div', { class: 'reality-item' }, el('span', { class: 'reality-date' }, it.date_label + ': '), it.text,
    long && it.url ? el('a', { class: 'reality-src', href: it.url, target: '_blank', rel: 'noopener' }, ' Source') : null);
  return el('div', { class: 'reality' }, el('div', { class: 'reality-kicker' }, r?.coasted && items.length > 1 ? 'While you were coasting, in reality' : 'Meanwhile, in reality'),
    items.map(line));
}
export function realityToast(r) { toast(realityNode(r), 'reality', (r?.items?.length || 1) > 1 ? 14000 : 10000); }
