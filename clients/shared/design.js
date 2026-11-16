// The design bay: nine decisions, four seats, four budgets that must close.
// Modelled on a concurrent-engineering session (JPL Team X, ESA CDF). Each seat
// owns its lines; every change shows what it moved. All numbers come from the
// server's evaluation (server/lightlag/sim/design.py), never from here.
import { drawer, el, num, science, roomUrl } from './ll.js';
import { drawPorkchop, nearestCell, rampCss } from './viz.js';

const STEPS = [
  { id: 'objective', title: 'Objective', owner: 'scientist' },
  { id: 'payload', title: 'Science payload', owner: 'scientist' },
  { id: 'launch', title: 'Launch date', owner: 'control' },
  { id: 'vehicle', title: 'Launch vehicle', owner: 'control' },
  { id: 'power', title: 'Power', owner: 'engineer' },
  { id: 'shield', title: 'Crew protection', owner: 'ship' },
  { id: 'radio', title: 'Radio time', owner: 'control' },
  { id: 'rules', title: "TERN's rules", owner: 'both' },
  { id: 'go', title: 'Go or no-go', owner: 'both' },
];
const FIRST_STEP = { scientist: 0, control: 2, engineer: 4, ship: 5 };
const WHO = { control: 'Flight', scientist: 'The Mission Scientist', ship: 'The Commander', engineer: 'The Flight Engineer', both: 'The whole team' };
const SIDE = { control: 'control', scientist: 'control', ship: 'ship', engineer: 'ship' };
const SEAT_ORDER = ['control', 'scientist', 'ship', 'engineer'];
const BUDGETS = ['mass', 'power', 'data', 'cost'];
const BAND = { green: 'Green', yellow: 'Yellow', red: 'Red' };
const PLANS = [
  ['lean', 'Lean', 'One antenna pass a day. Cheapest, and the longest waits: a warning can sit on the ground for most of a day.'],
  ['standard', 'Standard', 'Two passes a day, about what a real Mars mission gets. Some gaps.'],
  ['continuous', 'Continuous', 'Every complex, around the clock. Messages leave the moment they are written.'],
];
const PERMS = [['auto', 'Acts alone'], ['ask', 'Asks first'], ['forbid', 'Never']];
const etOf = (iso) => (Date.parse(iso) - Date.UTC(2000, 0, 1, 12)) / 1000 + 69.184;
const day = (iso) => (iso ? new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }) : '–');
const pct = (m) => `${Math.round(m * 100)}%`;
const money = (v) => `$${Math.round(v).toLocaleString('en-US')} M`;
const gbit = (mbit) => (mbit >= 1000 ? `${num(mbit / 1000, 1)} Gbit` : `${Math.round(mbit)} Mbit`);
const bandOf = (m) => (m < 0 ? 'red' : m < 0.1 ? 'yellow' : 'green');     // display only: the server decides validity
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
// Numbers tick to their new value (off under prefers-reduced-motion).
function tickTo(node, from, to, fmt, ms = 500) {
  if (reduced() || from === undefined || from === to || !isFinite(from) || !isFinite(to)) { node.textContent = fmt(to); return; }
  const t0 = performance.now();
  const step = (now) => { const k = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - k, 3); node.textContent = fmt(from + (to - from) * e); if (k < 1 && node.isConnected) requestAnimationFrame(step); };
  node.textContent = fmt(from); requestAnimationFrame(step);
}

export class DesignUI {
  constructor(root, link, role) {
    this.root = root; this.link = link; this.role = role; this.side = SIDE[role] || role; this.pc = null; this.view = null; this.d = null; this.policy = null; this.cat = null;
    root.innerHTML = '';
    root.classList.add('design');
    this.nav = el('nav', { class: 'steps', 'aria-label': 'Mission design steps' });
    this.main = el('section', { class: 'step-main' });
    this.sum = el('aside', { class: 'summary board', 'aria-label': 'Budget board' });
    root.append(this.nav, this.main, this.sum);
    this.pork = el('canvas', { class: 'porkchop', 'aria-label': 'Porkchop plot of Earth to Mars transfers' });
    this.pork.addEventListener('click', (e) => {
      if (!this.canEdit('control') || !this.view) return;
      const r = this.pork.getBoundingClientRect(), c = this.view.pick(e.clientX - r.left, e.clientY - r.top);
      if (this.pc.dv_total[c.i][c.j] === null) return;
      this.set({ launch_et: this.pc.launch_et[c.j], arrival_et: this.pc.arrival_et[c.i] });
    });
    this.pork.addEventListener('mousemove', (e) => this.hover(e));
    this.pork.addEventListener('mouseleave', () => this.tip?.classList.add('hidden'));
    this.tip = el('div', { class: 'pc-tip hidden' });
    fetch(roomUrl('/api/porkchop')).then((r) => r.json()).then((pc) => { this.pc = pc; this.marks = [{ label: 'Perseverance', cell: nearestCell(pc, etOf('2020-07-30T12:00:00Z'), etOf('2021-02-18T20:55:00Z')) }]; this.draw(); });
    fetch('/api/catalog').then((r) => r.json()).then((c) => { this.cat = c; this.render(true); });
    this.cards = {};
    for (const id of ['porkchop', 'payload', 'vehicle', 'power', 'data', 'margin'])
      science(id).then((n) => { this.cards[id] = n; this.render(true); });
    addEventListener('resize', () => this.draw());
    this.step = FIRST_STEP[role] ?? 0;
  }

  set(changes) { this.link.send('design.set', { changes }); }

  // A seat can make its own calls, and the calls of an AI-held seat on its side
  // (the server enforces the same rule), so a solo player is never locked out.
  canEdit(owner) {
    if (owner === 'both' || owner === this.role) return true;
    const seat = this.d?.seats?.[owner];
    return !!seat && seat.ai && SIDE[owner] === this.side;
  }

  go(i) { this.step = Math.max(0, Math.min(STEPS.length - 1, i)); this.render(true); }

  feasible(i, j) {        // shading only: would this trip still fit the rockets the budget can buy?
    const d = this.d; if (!d?.mass || !this.pc || !d.campaign) return true;
    const dv = this.pc.dv_total[i][j]; if (dv === null) return false;
    return d.mass.imleo_t * Math.exp((dv - d.dv_total) / (380 * 9.80665 / 1000)) <= d.campaign.max_imleo_t;
  }
  draw() {
    if (!this.pork.isConnected) return;
    const sel = this.pc && this.d ? nearestCell(this.pc, this.d.launch_et, this.d.arrival_et) : null;
    this.view = drawPorkchop(this.pork, this.pc, sel, (i, j) => this.feasible(i, j), this.marks || []);
  }
  hover(e) {
    if (!this.view) return;
    const r = this.pork.getBoundingClientRect(), c = this.view.pick(e.clientX - r.left, e.clientY - r.top), pc = this.pc;
    const dv = pc.dv_total[c.i][c.j];
    const tof = (pc.arrival_et[c.i] - pc.launch_et[c.j]) / 86400;
    this.tip.classList.remove('hidden');
    this.tip.style.transform = `translate(${e.clientX - r.left + 14}px, ${e.clientY - r.top + 10}px)`;
    this.tip.innerHTML = dv === null ? 'No transfer' : `<b>${day(pc.launch_iso[c.j])}</b> to <b>${day(pc.arrival_iso[c.i])}</b><br>`
      + `<span class="num">${num(dv, 2)} km/s</span>, <span class="num">${Math.round(tof)}</span> days${this.feasible(c.i, c.j) ? '' : '<br><span class="lantern">Too heavy for the launch budget</span>'}`;
  }

  update(s) {
    this.d = s.design; this.policy = s.policy; this.judge = s.judge; this.review = s.review;
    const key = JSON.stringify([s.design, s.policy?.version, s.policy?.rules, s.review?.n, s.review?.active]);
    if (key === this._key) return;      // only re-render on change, so inputs stay usable at 4 Hz
    this._key = key;
    this.render(false);
  }

  render(stepChanged) {
    const d = this.d; if (!d) return;
    this.renderNav(); this.renderSummary();
    const st = STEPS[this.step];
    const seatKey = JSON.stringify(Object.fromEntries(Object.entries(d.seats || {}).map(([k, v]) => [k, v.ai])));
    const seatsChanged = seatKey !== this._seatKey; this._seatKey = seatKey;
    if (!stepChanged && !seatsChanged && st.id === 'launch' && this.pork.isConnected) { this.draw(); this.renderLaunchFacts(); return; }
    const scroll = this.main.scrollTop;
    this.main.innerHTML = '';
    const mine = this.canEdit(st.owner);
    const head = el('header', { class: 'step-head' },
      el('div', { class: 'owner ' + (mine ? 'mine' : '') }, st.owner === 'both' ? 'The whole team decides this' : st.owner === this.role ? 'Your call'
        : mine ? `${WHO[st.owner]}'s call. That seat is AI-held, so you can make it.` : `${WHO[st.owner]}'s call. You can watch it change.`),
      el('h1', {}, this[st.id + 'Title']()));
    this.main.append(head, (this.cat || st.id === 'launch' || st.id === 'rules' ? this[st.id + 'Step'](d, mine) : el('p', { class: 'haze' }, 'Loading the catalog.')) || '');
    this.main.append(el('footer', { class: 'step-foot' },
      this.step > 0 ? el('button', { class: 'ghost', onclick: () => this.go(this.step - 1) }, `Back to ${STEPS[this.step - 1].title.toLowerCase()}`) : el('span'),
      this.step < STEPS.length - 1 ? el('button', { class: 'primary', onclick: () => this.go(this.step + 1) }, `Next: ${STEPS[this.step + 1].title.toLowerCase()}`) : null));
    if (!stepChanged) this.main.scrollTop = scroll;
    if (st.id === 'launch') requestAnimationFrame(() => this.draw());
  }

  renderNav() {
    const v = this.d.votes || {}, b = this.d.budgets || {};
    this.nav.innerHTML = '';
    this.nav.append(el('h2', {}, 'Design the mission'), el('p', { class: 'haze small' }, 'Nine decisions, four seats. Close mass, power, data and cost with margin, then launch.'));
    const ol = el('ol');
    const owns = { payload: ['data'], vehicle: ['mass', 'cost'], power: ['power'], radio: ['data'], objective: ['cost'] };
    STEPS.forEach((st, i) => {
      const nGo = SEAT_ORDER.filter((r) => v[r] === 'GO').length;
      const red = (owns[st.id] || []).some((k) => b[k]?.band === 'red');
      const status = st.id === 'go' ? (nGo === 4 ? 'Launching' : `${nGo} of 4 go`) : WHO[st.owner].replace(/^The /, '') + (st.owner === 'both' ? '' : ' decides');
      ol.append(el('li', { class: (i === this.step ? 'on ' : '') + (this.canEdit(st.owner) ? 'mine ' : '') + (red ? 'red' : '') },
        el('button', { onclick: () => this.go(i), 'aria-current': i === this.step ? 'step' : null },
          el('span', { class: 'n' }, String(i + 1)), el('span', { class: 't' }, st.title), el('span', { class: 's' }, status + (red ? ', over budget' : '')))));
    });
    this.nav.append(ol);
  }

  // ------------------------------------------------------- provenance
  model(note) { return el('span', { class: 'model-tag', title: note || 'MODEL: an assumption, not a measured or published value' }, 'MODEL'); }
  src(keys, label = 'sources') {
    keys = (keys || []).filter(Boolean);
    if (!keys.length || !this.cat) return null;
    return el('button', { class: 'src-link', onclick: (e) => { e.stopPropagation(); this.sources(keys); } }, label);
  }
  sources(keys) {
    drawer('src', 'Where these numbers come from', () => el('div', { class: 'list' }, keys.map((k) => {
      const s = this.cat.sources[k] || { title: k };
      return el('div', { class: 'msg' }, el('b', {}, s.title), el('div', { class: 'meta' }, el('span', {}, s.publisher || '')),
        s.url ? el('div', { class: 'meta' }, el('a', { href: s.url, target: '_blank', rel: 'noopener' }, s.url)) : null);
    })));
  }
  tags(item, field) {      // MODEL tag when this field of a catalog item is a model value
    return (item.model || []).includes(field) ? this.model(item.note) : null;
  }

  // ------------------------------------------------------- budget board
  renderSummary() {
    const d = this.d, v = d.votes || {};
    this.sum.innerHTML = '';
    // Who has voted sits right under the bars, so it stays above the fold on a laptop.
    const votes = [
      el('div', { class: 'votes' }, SEAT_ORDER.map((r) => {
        const seat = d.seats?.[r] || {};
        return el('div', { class: 'vote ' + (v[r] === 'GO' ? 'go' : v[r] === 'NO-GO' ? 'nogo' : '') + (r === this.role ? ' me' : ''), title: seat.name || r },
          el('span', {}, seat.title || r, seat.ai ? el('i', { class: 'ai-tag', title: 'No one has taken this seat: the AI plays it' }, ' AI') : null),
          el('b', {}, v[r] === 'GO' ? 'Go' : v[r] === 'NO-GO' ? 'No-go' : 'Waiting'));
      })),
      ...(d.valid ? [] : [el('p', { class: 'lantern small' }, `Can't launch: ${(d.reasons || [d.reason]).length} budget${(d.reasons || []).length === 1 ? '' : 's'} open. See Go or no-go.`)]),
      ...(this.judge ? [el('p', { class: 'small signal' }, 'Demo mode: if nobody decides, the mission launches by itself in a moment.')] : []),
      ...(this.failedReview() ? [this.reviewBanner()] : [])];
    this.sum.append(el('h2', {}, 'Budget board'),
      el('div', { class: 'trip' }, el('div', {}, el('span', { class: 'haze small' }, 'Launch'), el('b', {}, day(d.launch_iso))),
        el('div', { class: 'arrow' }), el('div', {}, el('span', { class: 'haze small' }, 'Mars'), el('b', {}, day(d.arrival_iso)))));
    this.sum.append(...votes);
    if (d.budgets) {
      this.sum.append(el('div', { class: 'review-wrap' }, el('table', { class: 'budget-review', 'aria-label': 'Mission budget review' },
        el('caption', {}, 'Allocated, used and margin · MODEL budgets'),
        el('thead', {}, el('tr', {}, ['Budget', 'Allocated', 'Used', 'Margin'].map((label) => el('th', { scope: 'col' }, label)))),
        el('tbody', {}, BUDGETS.map((k) => {
          const b = d.budgets[k], status = b.band === 'red' ? 'Fail' : b.band === 'yellow' ? 'Pass · low margin' : 'Pass';
          return el('tr', { class: b.band },
            el('td', {}, b.label, ' ', el('span', { class: 'dim small' }, b.unit), this.src(b.sources, 'src'), el('span', { class: 'band' }, status)),
            el('td', {}, k === 'cost' ? money(b.limit) : num(b.limit, 1)), el('td', {}, k === 'cost' ? money(b.value) : num(b.value, 1)), el('td', {}, pct(b.margin)));
        })))));
      const c = d.campaign;
      this.sum.append(el('div', { class: 'campaign' }, el('span', { class: 'haze small' }, 'Launch campaign'),
        el('b', { class: c.launches > c.limit ? 'alarm' : '' }, c.text),
        el('span', { class: 'num small ' + (d.mass.feasible ? 'go' : 'alarm') }, `${num(d.mass.imleo_t, 1)} / ${num(c.max_imleo_t, 1)} t in LEO · ${d.mass.feasible ? 'Pass' : 'Fail'}`),
        el('span', { class: 'dim small' }, `Transfer ${num(d.tof_days, 1)} d · C3 ${num(d.c3, 2)} km²/s²`),
        el('span', { class: 'dim small' }, `Radio ${num(d.data.hours, 0)} h/day · MODEL allocation`), this.src(c.sources)));
      this.sum.append(this.ripple(d.changes || []));
      this.sum.append(el('details', { class: 'lines', open: this._linesOpen ?? true, ontoggle: (e) => { this._linesOpen = e.target.open; } },
        el('summary', {}, 'Mass in low Earth orbit (IMLEO)', el('span', { class: 'num' }, `${num(d.mass.imleo_t, 1)} t`)),
        el('table', { class: 'kv' }, d.mass.lines.map((x) => el('tr', {}, el('td', {}, x.label, ' ', x.model ? this.model(x.note) : null, this.src(x.sources, 'src')),
          el('td', { class: 'v' }, x.t < 1 ? `${Math.round(x.t * 1000)}` : num(x.t, 1), el('span', { class: 'u' }, x.t < 1 ? 'kg' : 't')))))));
      this.sum.append(el('details', { class: 'lines', open: this._costOpen ?? false, ontoggle: (e) => { this._costOpen = e.target.open; } },
        el('summary', {}, 'Cost', el('span', { class: 'num' }, money(d.cost.total_musd))),
        el('table', { class: 'kv' }, d.cost.lines.map((x) => el('tr', {}, el('td', {}, x.label, ' ', x.model ? this.model() : null, this.src(x.sources, 'src')),
          el('td', { class: 'v' }, money(x.musd)))))));
    }
  }
  failedReview() { const r = this.review; return r && !r.active && r.verdict === 'fail' ? r : null; }
  reviewBanner() {
    const r = this.failedReview();
    return el('div', { class: 'review-banner', role: 'status' }, el('b', {}, `The review board failed attempt ${r.n}.`),
      el('ul', {}, r.fails.map((f) => el('li', {}, `${f.area}: ${f.text}`))),
      el('div', { class: 'small haze' }, `Launch slipped ${r.slip_days} days. Fix it, then vote again.`));
  }
  bar(k, b) {
    this._bands = this._bands || {}; this._margins = this._margins || {};
    const crossed = this._bands[k] && this._bands[k] !== b.band, prevM = this._margins[k];
    this._bands[k] = b.band; this._margins[k] = b.margin;
    const mnode = el('span', { class: 'num' });
    tickTo(mnode, prevM, b.margin, (m) => `margin ${pct(m)}`);
    const frac = b.limit > 0 ? Math.min(1, b.value / b.limit) : 1;
    const val = k === 'cost' ? `${money(b.value)} of ${money(b.limit)}` : `${num(b.value, k === 'mass' ? 0 : 1)} of ${num(b.limit, k === 'mass' ? 0 : 1)} ${b.unit}`;
    return el('div', { class: 'budget ' + b.band + (crossed ? ' crossed' : '') },
      el('div', { class: 'row' }, el('b', { class: 'grow' }, b.label, ' ', b.model ? this.model(`MODEL: limit is ${b.limit_text}`) : null, this.src(b.sources, 'src')),
        el('span', { class: 'band-word' }, BAND[b.band])),
      el('div', { class: 'bar' }, el('i', { style: `width:${frac * 100}%` })),
      el('div', { class: 'row small' }, el('span', { class: 'num grow' }, val + (k === 'mass' ? `, limit ${b.limit_text}` : '')), mnode));
  }
  ripple(changes) {
    const key = JSON.stringify(changes), fresh = key !== this._rippleKey;
    this._rippleKey = key;
    const box = el('div', { class: 'ripple' + (fresh ? ' fresh' : ''), 'aria-live': 'polite' }, el('span', { class: 'haze small' }, 'Last change'));
    if (!changes.length) box.append(el('p', { class: 'dim small' }, 'Change anything and its effects appear here.'));
    else box.append(el('ol', {}, changes.map((c, i) => el('li', { style: `animation-delay:${Math.min(i, 5) * 40}ms`, class: /\((red)\)/.test(c) ? 'alarm' : /\(yellow\)/.test(c) ? 'lantern' : '' }, c))));
    return box;
  }

  // ------------------------------------------------------------ steps
  objectiveTitle() { return 'What is this mission for?'; }
  objectiveStep(d, mine) {
    const cov = (o) => d.payload.reduce((s, i) => s + ((this.cat.instruments.find((x) => x.id === i)?.serves || {})[o.id] || 0), 0);
    return el('div', { class: 'stack' },
      el('p', { class: 'lede' }, 'Pick one goal. It decides which instruments are worth their mass, how much data has to come home, and how much money the sponsor will put up.'),
      el('div', { class: 'dcards three', role: 'radiogroup' }, this.cat.objectives.map((o) => el('button', {
        class: 'dcard' + (d.objective === o.id ? ' on' : ''), role: 'radio', 'aria-checked': String(d.objective === o.id), disabled: !mine,
        onclick: () => this.set({ objective: o.id }) },
      el('b', {}, o.name), el('span', { class: 'haze' }, o.pitch),
      el('dl', {}, el('dt', {}, 'Cost cap'), el('dd', { class: 'num' }, money(o.cost_cap_musd), ' ', this.tags(o, 'cost_cap_musd')),
        el('dt', {}, 'Data needed'), el('dd', { class: 'num' }, `${o.min_data_gbit.toLocaleString('en-US')} Gbit`, ' ', this.tags(o, 'min_data_gbit')),
        el('dt', {}, 'Payload fit'), el('dd', { class: 'num' }, `${num(cov(o), 1)}`, ' ', this.model('MODEL: the sum of how much each carried instrument serves this goal. 1.0 or more covers it.')))))),
      d.science ? el('div', { class: 'science fit ' + d.science.verdict }, el('b', {}, 'Science fit'), el('div', {}, d.science.text),
        el('div', { class: 'src' }, `${num(d.science.returned_gbit, 0)} Gbit comes home over ${d.data.stay_days} days at Mars. Coverage and data floor are MODEL.`)) : null);
  }

  payloadTitle() { return 'What do you carry?'; }
  payloadStep(d, mine) {
    const on = new Set(d.payload);
    const toggle = (id) => this.set({ payload: on.has(id) ? d.payload.filter((x) => x !== id) : [...d.payload, id] });
    const obj = this.cat.objectives.find((o) => o.id === d.objective);
    const fit = (w) => (w >= 0.8 ? 'Made for it' : w >= 0.4 ? 'Helps' : w > 0 ? 'A little' : 'No help');
    return el('div', { class: 'stack' },
      el('p', { class: 'lede' }, `Every instrument flew on a real NASA mission. Each one costs mass, power and money, and its data has to fit through the antenna. Goal: ${obj?.name.toLowerCase()}.`),
      el('div', { class: 'dcards inst' }, this.cat.instruments.map((i) => el('div', { class: 'dcard' + (on.has(i.id) ? ' on' : '') + (mine ? '' : ' locked'), role: 'checkbox', 'aria-checked': String(on.has(i.id)),
        tabindex: mine ? 0 : -1, onclick: () => mine && toggle(i.id), onkeydown: (e) => mine && (e.key === ' ' || e.key === 'Enter') && (e.preventDefault(), toggle(i.id)) },
      el('div', { class: 'row' }, el('b', { class: 'grow' }, i.name), el('span', { class: 'pick' }, on.has(i.id) ? 'Carried' : 'Add')),
      el('span', { class: 'haze small' }, i.heritage),
      el('dl', {}, el('dt', {}, 'Mass'), el('dd', { class: 'num' }, `${num(i.mass_kg, i.mass_kg < 10 ? 1 : 0)} kg`, this.tags(i, 'mass_kg')),
        el('dt', {}, 'Power'), el('dd', { class: 'num' }, `${num(i.power_w, i.power_w < 10 ? 1 : 0)} W`, this.tags(i, 'power_w')),
        el('dt', {}, 'Data'), el('dd', { class: 'num' }, `${gbit(i.data_mbit_day)}/day`, ' ', this.tags(i, 'data_mbit_day')),
        el('dt', {}, 'Cost'), el('dd', { class: 'num' }, money(i.cost_musd), ' ', this.tags(i, 'cost_musd'))),
      el('div', { class: 'row small' }, el('span', { class: 'grow ' + ((i.serves[d.objective] || 0) >= 0.8 ? 'go' : 'haze') }, fit(i.serves[d.objective] || 0)), this.src(i.sources))))),
      this.cards.payload);
  }

  launchTitle() { return 'When do you leave for Mars?'; }
  launchStep(d, mine) {
    this.facts = el('div', { class: 'facts' });
    const wrap = el('div', { class: 'pc-wrap' }, this.pork, this.tip);
    const body = el('div', { class: 'stack' },
      el('p', { class: 'lede' }, 'Earth and Mars line up for a cheap trip about every 26 months. Each square is one possible trip, solved for real. ',
        mine ? 'Click a square to pick your launch and arrival dates.' : 'Flight picks the dates.'),
      wrap,
      el('div', { class: 'row legend' }, el('span', { class: 'haze small' }, 'Cheaper'), el('span', { class: 'ramp', style: `background:${rampCss()}` }),
        el('span', { class: 'haze small' }, 'Costlier'), el('span', { class: 'swatch' }), el('span', { class: 'haze small' }, 'Inside: your rocket can lift it within the launch and cost limits')),
      this.facts, this.cards.porkchop || null);
    this.renderLaunchFacts();
    return body;
  }
  renderLaunchFacts() {
    const d = this.d; if (!this.facts) return;
    this.facts.innerHTML = '';
    for (const [k, v, u] of [['Launch energy (C3)', num(d.c3, 1), 'km²/s²'], ['Leaving Earth', num(d.dv_tmi, 2), 'km/s'], ['Braking at Mars', num(d.dv_moi, 2), 'km/s'], ['Propellant', num(d.mass?.propellant_t, 1), 't']])
      this.facts.append(el('div', {}, el('span', { class: 'haze small' }, k), el('b', { class: 'num' }, v, el('span', { class: 'u' }, u))));
  }

  vehicleTitle() { return 'Which rocket do you buy?'; }
  vehicleStep(d, mine) {
    const imleo = d.mass.imleo_t, limit = d.constants.launch_limit;
    return el('div', { class: 'stack' },
      el('p', { class: 'lede' }, `Meridian-1 is assembled in low Earth orbit from several launches. It weighs ${num(imleo, 1)} t there, so you buy as many whole rockets as it takes. The campaign can dock at most ${limit} before the window closes.`),
      el('div', { class: 'dcards two', role: 'radiogroup' }, this.cat.vehicles.map((v) => {
        const n = Math.ceil(imleo / v.leo_t - 1e-9), over = n > limit;
        return el('button', { class: 'dcard' + (d.vehicle === v.id ? ' on' : '') + (over ? ' over' : ''), role: 'radio', 'aria-checked': String(d.vehicle === v.id), disabled: !mine,
          onclick: () => this.set({ vehicle: v.id }) },
        el('div', { class: 'row' }, el('b', { class: 'grow' }, v.name), el('span', { class: 'haze small' }, v.operator)),
        el('dl', {}, el('dt', {}, 'To low orbit'), el('dd', { class: 'num' }, `${num(v.leo_t, 1)} t`, ' ', this.tags(v, 'leo_t')),
          el('dt', {}, 'Price'), el('dd', { class: 'num' }, money(v.price_musd)),
          el('dt', {}, 'For this ship'), el('dd', { class: 'num ' + (over ? 'alarm' : '') }, `${n} × = ${money(n * v.price_musd)}`)),
        el('div', { class: 'row small' }, el('span', { class: 'grow ' + (over ? 'alarm' : 'haze') }, over ? `${n} launches: more than the campaign can dock` : `Fits in ${n} launch${n === 1 ? '' : 'es'}`),
          this.src(v.sources)));
      })),
      el('p', { class: 'haze small' }, 'Launch count limit and per-launch assembly risk are MODEL. Prices and capacities are published figures; open each card\'s sources.'),
      this.cards.vehicle);
  }

  powerTitle() { return 'How much power do you fly?'; }
  powerStep(d, mine) {
    const p = d.power, r2 = p.r_au * p.r_au;
    return el('div', { class: 'stack' },
      el('p', { class: 'lede' }, `At Mars the Sun delivers 43% of what it does at Earth. There the ship's life support, heaters and systems draw ${num(p.ship_load_kw, 2)} kW, and the instruments add ${num(p.instruments_w, 0)} W.`),
      el('h3', {}, 'Solar array'),
      el('div', { class: 'dcards three', role: 'radiogroup' }, this.cat.solar_arrays.map((a) => {
        const at = a.kw_at_1au / r2, m = (at - p.load_kw) / at, b = bandOf(m);
        return el('button', { class: 'dcard' + (d.array === a.id ? ' on' : ''), role: 'radio', 'aria-checked': String(d.array === a.id), disabled: !mine, onclick: () => this.set({ array: a.id }) },
          el('b', {}, a.name),
          el('dl', {}, el('dt', {}, 'At Earth'), el('dd', { class: 'num' }, `${num(a.kw_at_1au, 0)} kW`, ' ', this.tags(a, 'kw_at_1au')),
            el('dt', {}, 'At Mars'), el('dd', { class: 'num' }, `${num(at, 1)} kW`),
            el('dt', {}, 'Mass'), el('dd', { class: 'num' }, `${Math.round(a.mass_t * 1000)} kg`, ' ', this.tags(a, 'mass_t')),
            el('dt', {}, 'Margin'), el('dd', { class: 'num band-' + b }, `${pct(m)} ${BAND[b]}`)),
          el('div', { class: 'row small' }, el('span', { class: 'grow haze' }, a.note), this.src(a.sources)));
      })),
      el('h3', {}, 'Battery'),
      el('div', { class: 'dcards three', role: 'radiogroup' }, this.cat.batteries.map((b) => el('button', { class: 'dcard' + (d.battery === b.id ? ' on' : ''), role: 'radio',
        'aria-checked': String(d.battery === b.id), disabled: !mine, onclick: () => this.set({ battery: b.id }) },
      el('b', {}, b.name),
      el('dl', {}, el('dt', {}, 'Mass'), el('dd', { class: 'num' }, `${Math.round(b.mass_t * 1000)} kg`, ' ', this.tags(b, 'mass_t')),
        el('dt', {}, 'Reserve'), el('dd', { class: 'num' }, `${num(b.kwh / p.load_kw, 1)} h at full load`)),
      el('div', { class: 'row small' }, el('span', { class: 'grow haze' }, b.note), this.src(b.sources))))),
      el('div', { class: 'science' }, el('b', {}, 'If one array string fails'),
        el('div', {}, p.string_loss_kw >= 0 ? `This array still has ${num(p.string_loss_kw, 1)} kW to spare at Mars.`
          : `The ship runs ${num(-p.string_loss_kw, 1)} kW short at Mars, and the battery carries the gap for about ${num(p.batt_kwh / -p.string_loss_kw, 0)} hours. Behind the Sun, Earth cannot help.`),
        el('div', { class: 'src' }, 'Same flight model the mission runs: sim/subsystems.py')),
      this.cards.power);
  }

  shieldTitle() { return 'How much do you protect the crew?'; }
  shieldStep(d, mine) {
    const slider = (key, label, max, step, help) => el('div', { class: 'choice' },
      el('div', { class: 'row' }, el('b', { class: 'grow' }, label), el('span', { class: 'num' }, `${d[key]} g/cm²`)),
      el('input', { type: 'range', min: 0, max, step, value: d[key], disabled: !mine, 'aria-label': label,
        onchange: (e) => this.set({ [key]: +e.target.value }) }),
      el('p', { class: 'haze small' }, help));
    return el('div', { class: 'stack' },
      el('p', { class: 'lede' }, 'Space radiation comes two ways: a slow, steady drizzle of cosmic rays, and rare solar storms that can deliver a month of dose in an hour. Everything you add is mass the rockets have to lift.'),
      slider('shelter_gcm2', 'Storm shelter, a water wall around the sleep stations', 40, 5,
        `In a storm, the shelter lets through ${Math.round(d.sep_factor_shelter * 100)}% of the dose outside.`),
      slider('hull_liner_gcm2', 'Plastic liner for the whole habitat', 10, 1,
        `Cuts the cosmic-ray drizzle to ${Math.round(d.gcr_factor * 100)}%. Cosmic rays are hard to stop, so this barely helps.`),
      el('label', { class: 'choice check' }, el('input', { type: 'checkbox', checked: d.solar_monitor, disabled: !mine,
        onchange: (e) => this.set({ solar_monitor: e.target.checked }) }),
        el('span', {}, el('b', {}, 'Carry your own solar flare monitor'), el('span', { class: 'haze small' },
          ' Adds 0.35 t. Without it, Imani hears about a flare only when Earth’s warning arrives.'))));
  }

  radioTitle() { return 'How much antenna time do you book?'; }
  radioStep(d, mine) {
    const dd = d.data;
    return el('div', { class: 'stack' },
      el('p', { class: 'lede' }, `Three dish complexes, in California, Spain and Australia, are shared by every deep-space mission. Between passes, messages wait on the ground. Your payload makes ${gbit(dd.demand_mbit_day)} a day.`),
      el('div', { class: 'options', role: 'radiogroup' }, PLANS.map(([k, t, help]) => {
        const p = dd.by_plan[k];
        return el('button', { class: 'option' + (d.comms_plan === k ? ' on' : ''), role: 'radio',
          'aria-checked': d.comms_plan === k ? 'true' : 'false', disabled: !mine, onclick: () => this.set({ comms_plan: k }) },
        el('b', {}, t), el('span', { class: 'haze' }, help),
        el('span', { class: 'num' }, `${gbit(p.capacity_mbit_day)}/day home`),
        el('span', { class: 'num band-' + p.band }, `Data margin ${pct(p.margin)} ${BAND[p.band]}`));
      })),
      el('p', { class: 'haze small' }, `Averaged over ${dd.stay_days} days at Mars, using the game's own link model at the real Earth to Mars distance. `,
        `${dd.dark_days} of those days are behind the Sun and carry nothing.`),
      this.cards.data);
  }

  rulesTitle() { return 'What may TERN do without asking?'; }
  rulesStep() {
    const p = this.policy; if (!p) return el('div');
    return el('div', { class: 'stack' },
      el('p', { class: 'lede' }, 'TERN is the ship’s AI. When Earth is minutes away, someone aboard has to be allowed to act. Anyone on the team can change these.'),
      el('div', { class: 'rules' }, p.rules.map((r) => el('div', { class: 'rule' }, el('span', { class: 'grow' }, r.label),
        el('div', { class: 'seg', role: 'radiogroup', 'aria-label': r.label }, PERMS.map(([k, t]) => el('button', { class: r.permission === k ? 'on' : '', role: 'radio',
          'aria-checked': r.permission === k ? 'true' : 'false', onclick: () => this.link.send('policy.set', { rules: [{ id: r.id, permission: k }] }) }, t)))))),
      el('div', { class: 'science' }, el('b', {}, 'One rule sits above all of these'),
        el('div', {}, 'If harm will arrive before a question could reach Earth and an answer come back, TERN acts anyway, and tells everyone it broke the rules.')));
  }

  goTitle() { return 'Are you go for launch?'; }
  goStep(d) {
    const v = d.votes || {}, me = d.seats?.[this.role]?.title || 'You';
    const reasons = d.reasons || (d.reason ? [d.reason] : []);
    return el('div', { class: 'stack' },
      el('p', { class: 'lede' }, 'When all four seats say go, the Flight Readiness Review checks the design: five reviewers, the same numbers, plus the cases the budget board does not run. A pass launches. A failure sends you back here and costs launch-window days. Seats nobody has taken are played by AI and follow the humans. Changing anything clears every vote.'),
      this.failedReview() ? this.reviewBanner() : null,
      reasons.length ? el('div', { class: 'reasons' }, el('b', {}, 'Open budgets'), el('ul', {}, reasons.map((r) => el('li', {}, r + '.'))))
        : el('p', { class: 'go' }, 'Every budget closes. Mass, power, data and cost all have margin.'),
      el('div', { class: 'row' },
        el('button', { class: 'primary big', disabled: !d.valid, onclick: () => this.link.send('design.vote', { vote: 'GO' }) }, `${me} is go`),
        el('button', { class: 'warn', onclick: () => this.link.send('design.vote', { vote: 'NO-GO' }) }, 'Not yet')),
      el('p', { class: 'haze' }, (() => { const wait = SEAT_ORDER.filter((r) => r !== this.role && !d.seats?.[r]?.ai && v[r] !== 'GO').map((r) => d.seats?.[r]?.title || r);
        return wait.length ? `Waiting on ${wait.join(', ')}.` : 'Everyone else with a seat is go.'; })()),
      this.cards.margin);
  }
}
