
// The debrief, shared by every seat: how it went, what each design choice did
// in flight (decision, then flight event, then outcome), the mission on one
// line, the crew, the science, what scored and what didn't, TERN's decisions,
// the order Imani chose, and a way to fly it again. No WebGL here: it has to
// open fast and print cleanly. All numbers come from the server
// (server/lightlag/sim/debrief.py).
// Honesty rule (UI pass): a value the report does not have renders as "Unavailable", never as 0
// or a hardcoded limit.
import { el, num, roomUrl, seatToken, toast } from './ll.js';
import { whyDrawer } from './trace.js';

const RULE = { shelter_alert: 'Storm alarm', storm_attitude: 'Tanks toward the Sun', scrubber_cycle: 'CO₂ scrubber swap', pump_failover: 'Coolant loop switch',
  load_shed_science: 'Science payload powered down', hga_repoint: 'Antenna re-pointed', execute_uplink: 'Uplinked command run' };
const STATUS = { 'within policy': 'within the rules', 'exception requested': 'asked Lantern first', 'approved by Lantern': 'Lantern said yes',
  'denied by Lantern': 'Lantern said no', 'POLICY BREAK': 'broke the rules', 'held per policy': 'held, as the rules say' };
const SVG = 'http://www.w3.org/2000/svg';
// Missing values say so: never 0, never a made-up limit.
const has = (v) => v !== null && v !== undefined && isFinite(v);
const val = (v, d = 1, unit = '') => (has(v) ? `${Number(v).toFixed(d)}${unit}` : 'Unavailable');
const txt = (v) => (v === null || v === undefined || v === '' ? 'Unavailable' : String(v));
const svg = (tag, attrs = {}, ...kids) => { const e = document.createElementNS(SVG, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); kids.forEach((k) => e.append(k)); return e; };

// --------------------------------------------------------------- pieces
function decisionsAndLetters(r) {
  return el('div', { class: 'stack' },
    el('h2', {}, 'What TERN decided'),
    el('div', { class: 'list' }, (r.decisions || []).map((d) => el('div', { class: 'msg ' + (d.outcome === 'broke_policy' ? 'alert' : 'tern') },
      el('div', { class: 'meta' }, el('span', {}, RULE[d.rule] || d.rule), el('span', {}, STATUS[d.policy_status] || d.policy_status), el('span', {}, (d.iso || '').slice(0, 16).replace('T', ' '))), d.rationale))),
    (r.letters_order || []).length ? el('h2', {}, 'The order Imani chose') : null,
    el('div', { class: 'stack' }, (r.letters_order || []).map((L, i) => el('div', { class: 'paper' }, el('div', { class: 'from' }, `${i + 1}. ${L.created_local || ''}`), el('div', { class: 'letter-text' }, L.text)))),
    el('details', {}, el('summary', {}, 'The whole timeline'),
      el('pre', {}, (r.timeline || []).slice(-200).map((t) => `${t.iso}  ${t.side.padEnd(7)} ${t.text}`).join('\n'))));
}

const cap = (t) => (typeof t === 'string' && t ? t[0].toUpperCase() + t.slice(1) : t);
function chainCard(c) {
  const who = c.decision.title + (c.decision.name && c.decision.name !== c.decision.title ? `, ${c.decision.name}` : '') + (c.decision.ai ? ' (AI)' : '');
  return el('li', { class: 'chain' + (c.lien_true ? ' lien' : '') },
    el('div', { class: 'link decision' }, el('span', { class: 'k' }, 'Decision'), el('b', {}, cap(c.decision.text)), el('span', { class: 'haze small' }, `${who}, ${c.decision.step}`)),
    el('div', { class: 'arrow', 'aria-hidden': 'true' }, '→'),
    el('div', { class: 'link event' }, el('span', { class: 'k' }, `Day ${num(c.event.day, 0)}`), el('b', {}, cap(c.event.text))),
    el('div', { class: 'arrow', 'aria-hidden': 'true' }, '→'),
    el('div', { class: 'link outcome' }, el('span', { class: 'k' }, 'Outcome'), el('span', {}, cap(c.outcome.text)),
      c.outcome.counter ? el('span', { class: 'haze small' }, cap(c.outcome.counter)) : null,
      el('div', { class: 'row small' }, c.lien_true ? el('span', { class: 'lien-tag' }, 'Lien came true') : null,
        c.outcome.model ? el('span', { class: 'model-tag' }, 'MODEL') : null,
        c.id.startsWith('c') ? el('button', { type: 'button', class: 'why-btn small', 'aria-label': `Why: ${c.decision.text}`, onclick: () => whyDrawer({ ...c, text: c.outcome.text, counter: c.outcome.counter, choice: c.decision.text, seat: c.decision.seat, day: c.event.day }) }, 'Why?') : null)));
}

function timelineSvg(t) {
  const W = 1000, H = 150, x = (d) => 20 + (W - 40) * Math.max(0, Math.min(1, d / Math.max(1, t.days)));
  const s = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'tl', role: 'img', 'aria-label': `Mission timeline, ${Math.round(t.days)} days` });
  if (t.conjunction) s.append(svg('rect', { x: x(t.conjunction[0]), y: 18, width: Math.max(3, x(t.conjunction[1]) - x(t.conjunction[0])), height: 92, class: 'tl-conj' }),
    svg('text', { x: x(t.conjunction[0]) - 4, y: 14, class: 'tl-lab', 'text-anchor': 'end' }, 'Conjunction: no signal'));
  s.append(svg('line', { x1: 20, x2: W - 20, y1: 110, y2: 110, class: 'tl-axis' }));
  const maxLt = Math.max(1, ...t.lt.map((p) => p[1]));
  if (t.lt.length) s.append(svg('polyline', { points: t.lt.map((p) => `${x(p[0]).toFixed(1)},${(110 - 80 * p[1] / maxLt).toFixed(1)}`).join(' '), class: 'tl-lt' }),
    svg('text', { x: W - 20, y: 110 - 80 - 4, class: 'tl-lab', 'text-anchor': 'end' }, `light time, up to ${num(maxLt, 0)} min`));
  t.scenes.forEach((sc, i) => { s.append(svg('line', { x1: x(sc.d0), x2: x(sc.d0), y1: 104, y2: 116, class: 'tl-tick' }));
    s.append(svg('text', { x: x(sc.d0), y: 132 + (i % 2) * 13, class: 'tl-scene', 'text-anchor': i === t.scenes.length - 1 ? 'end' : 'start' }, sc.title.replace(/^.*\/\s*/, ''))); });
  if (t.storm_day != null) s.append(svg('path', { d: `M${x(t.storm_day)} 96 l6 -12 h-12 z`, class: 'tl-storm' }), svg('text', { x: x(t.storm_day) + 8, y: 88, class: 'tl-lab' }, 'Storm'));
  if (t.arrival_day != null) s.append(svg('text', { x: x(t.arrival_day), y: 102, class: 'tl-lab', 'text-anchor': 'middle' }, 'Mars'));
  t.callouts.forEach((c) => { const m = svg('circle', { cx: x(c.day), cy: 60, r: 5, class: 'tl-co' }); m.append(svg('title', {}, `Day ${c.day}: ${c.text}`)); s.append(m); });
  return s;
}

function spark(points, cls, label) {
  const W = 160, H = 34, xs = points.map((p) => p[0]), d0 = Math.min(...xs), d1 = Math.max(...xs, d0 + 1);
  const s = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'spark ' + cls, role: 'img', 'aria-label': label });
  if (points.length) s.append(svg('polyline', { points: points.map(([d, v]) => `${((d - d0) / (d1 - d0) * W).toFixed(1)},${(H - 2 - (v ?? 0) * (H - 4)).toFixed(1)}`).join(' ') }));
  return s;
}

function crewCard(c) {
  const word = (v, w) => w[Math.max(0, Math.min(3, Math.floor((v ?? 0) * 4)))];
  return el('div', { class: 'crew-card' }, el('div', { class: 'row' }, el('b', { class: 'grow' }, c.name), el('span', { class: 'haze small' }, c.role + (c.ai ? ', AI' : ', a player'))),
    el('div', { class: 'sp' }, el('span', { class: 'small haze' }, `Stress, ended ${word(c.final.stress, ['calm', 'steady', 'strained', 'at the edge'])}`), spark(c.stress, 'stress', `${c.name} stress over the mission`)),
    el('div', { class: 'sp' }, el('span', { class: 'small haze' }, `Morale, ended ${word(c.final.morale, ['low', 'flat', 'good', 'high'])}`), spark(c.morale, 'morale', `${c.name} morale over the mission`)),
    c.decision ? el('p', { class: 'small' }, el('span', { class: 'dim' }, 'Their decision: '), c.decision) : null,
    c.last_line ? el('p', { class: 'small last' }, `“${c.last_line}”`) : null);
}

function flyAgain(fa) {
  const go = async (suggestion) => {
    const r = await fetch(roomUrl(`/api/fly-again${suggestion ? '?suggestion=1' : ''}`), { method: 'POST', headers: { 'X-Seat-Token': seatToken || '' } });
    if (!r.ok) { toast((await r.json().catch(() => ({}))).error || 'Could not start a new flight.', 'error'); return; }
    location.reload();
  };
  return el('section', { class: 'db-sec fly' }, el('h2', {}, 'Fly it again'),
    fa ? el('p', { class: 'lede' }, fa.text, ' ', el('span', { class: 'model-tag', title: 'Re-evaluated by the design bay with one change; not flown' }, 'MODEL')) : el('p', { class: 'haze' }, 'Nothing obvious to change. Try a riskier design and see what the board says.'),
    el('div', { class: 'row' }, fa ? el('button', { class: 'primary', onclick: () => go(true) }, 'Fly it again with this change') : null,
      el('button', { class: fa ? '' : 'primary', onclick: () => go(false) }, 'Fly it again as designed')),
    el('p', { class: 'small haze' }, 'Everyone goes back to the design bay with the flown design loaded. Seats are kept.'));
}

// --------------------------------------------------------------- the page
export function aarView(r) {
  const d = r.debrief;
  if (!d) return decisionsAndLetters(r);
  const v = d.verdict, sc = r.score || {}, sci = d.science || {};
  const tiles = v.grades.map((g) => el('div', { class: 'tile' }, el('span', { class: 'haze small' }, g.label), el('b', { class: 'num' }, g.value),
    g.grade ? el('span', { class: 'grade ' + (g.grade === 'missed' || g.grade === 'F' || g.grade === 'D' ? 'alarm' : g.grade === 'C' ? 'lantern' : 'go') },
      g.grade === 'met' ? 'Objective met' : g.grade === 'missed' ? 'Objective missed' : `Grade ${g.grade}`) : null));
  const need = sci.min_data_gbit, rel = sci.relevant_gbit, pctHome = has(rel) && has(need) && need > 0 ? Math.min(1, rel / need) : null;
  return el('div', { class: 'debrief-body' },
    el('section', { class: 'db-sec verdict' }, el('div', { class: 'kicker' }, 'Debrief'), el('h1', {}, v.sentence), el('div', { class: 'tiles' }, tiles),
      d.liens.length ? el('p', { class: 'small' }, el('b', {}, 'Liens from the review: '), d.liens.map((L) => `${L.area} (${L.came_true ? 'came true' : 'did not come true'})`).join('; '), '.') : null),
    el('section', { class: 'db-sec' }, el('h2', {}, 'Your design, then what happened'),
      el('p', { class: 'haze small' }, 'Each line starts with a choice made in the design bay, then the moment in flight it mattered, then what it changed. Counterfactuals re-evaluate the same model with the other choice (MODEL); nothing was re-flown.'),
      el('ol', { class: 'chains' }, d.chains.map(chainCard))),
    el('section', { class: 'db-sec' }, el('h2', {}, 'The mission on one line'), timelineSvg(d.timeline),
      el('p', { class: 'small haze' }, 'Dots are the moments your design spoke up. The shaded band is the conjunction; the line is the light time between Houston and the ship.')),
    el('section', { class: 'db-sec' }, el('h2', {}, 'The crew'), el('div', { class: 'crew-grid' }, d.crew.map(crewCard)),
      el('p', { class: 'small dim' }, 'Morale is shown, never scored.')),
    el('section', { class: 'db-sec' }, el('h2', {}, 'Science'),
      el('table', { class: 'kv' }, ...[['Recorded aboard', val(sci.generated_gbit, 1, ' Gbit')], ['Delivered to Earth', val(sci.delivered_gbit, 1, ' Gbit')],
        [`For the objective (${txt(sci.objective_name || sci.objective)})`, `${val(rel, 1)} of ${val(need, 0, ' Gbit')} needed, ${sci.objective_met == null ? 'Unavailable' : sci.objective_met ? 'met' : 'missed'}`],
        ['Still aboard or in flight', has(sci.on_recorder_gbit) && has(sci.in_flight_gbit) ? val(sci.on_recorder_gbit + sci.in_flight_gbit, 1, ' Gbit') : 'Unavailable'], ['Lost', val(sci.lost_gbit, 1, ' Gbit')]]
        .map(([k, x]) => el('tr', {}, el('td', {}, k), el('td', { class: 'v txt' }, x)))),
      pctHome == null ? null : el('div', { class: 'bar sci-bar', role: 'img', 'aria-label': `Objective ${Math.round(100 * pctHome)}% delivered` }, el('i', { style: `width:${100 * pctHome}%` })),
      el('p', { class: 'small haze' }, (sci.lost_why || []).join(' '))),
    el('section', { class: 'db-sec' }, el('h2', {}, 'What scored, what didn’t'),
      el('div', { class: 'two' }, el('div', {}, el('h3', {}, 'Scored'), el('table', { class: 'kv' }, d.scored.map((x) => el('tr', {}, el('td', {}, x.label), el('td', { class: 'v txt' }, x.value + (x.grade ? `, ${x.grade}` : '')))))),
        el('div', {}, el('h3', {}, 'Shown, never scored'), el('div', { class: 'stack' }, d.not_scored.map((x) => el('div', {}, el('b', {}, x.label), ' ', el('span', { class: 'num' }, x.value), el('div', { class: 'small haze' }, x.why))))))),
    el('section', { class: 'db-sec' }, decisionsAndLetters(r),
      el('p', { class: 'small dim' }, `Messages sent, delivered, expired: ${txt(sc.bundles?.sent)}, ${txt(sc.bundles?.delivered)}, ${txt(sc.bundles?.expired)}. Waves from the audience: ${txt(sc.pings_from_audience)}.`)),
    flyAgain(d.fly_again));
}

let page = null;
export function showDebrief(report) {
  page?.remove();
  page = el('div', { class: 'debrief-page', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Mission debrief' },
    el('div', { class: 'db-bar' }, el('b', { class: 'grow' }, 'Meridian-1 debrief'),
      el('a', { class: 'small', href: roomUrl('/factsheet/'), target: '_blank', rel: 'noopener' }, 'Mission fact sheet'),
      el('button', { class: 'ghost', onclick: () => print() }, 'Print'),
      el('button', { class: 'ghost', onclick: () => { page.remove(); page = null; document.body.classList.remove('debrief-open'); } }, 'Close')),
    aarView(report));
  document.body.append(page);
  document.body.classList.add('debrief-open');
  page.querySelector('button')?.focus();
  return page;
}
