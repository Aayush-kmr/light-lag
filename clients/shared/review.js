// The Flight Readiness Review: one shared screen on every client.
// The room dims, the five areas resolve one at a time (a number ticks to its
// final value, then the verdict word appears), then the board's verdict.
// Timings come from the server (data/review_board.yaml), so this file holds no
// numbers of its own. With prefers-reduced-motion everything shows at once.
//
// Audio hooks for Phase 5: window 'll-audio' CustomEvents with detail.cue in
//   review.dim, review.area (detail.area, detail.verdict), review.verdict (detail.verdict),
//   review.close. See docs/audio-hooks.md.
import { el, science, roomUrl, seatToken } from './ll.js';

const WORD = { pass: 'PASS', lien: 'PASS WITH LIEN', fail: 'FAIL' };
const CLASS = { pass: 'go', lien: 'lantern', fail: 'alarm' };
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
export const audioCue = (cue, detail = {}) => dispatchEvent(new CustomEvent('ll-audio', { detail: { cue, ...detail } }));

function fmtNum(v, unit) {
  if (v === null || v === undefined || !isFinite(v)) return '–';
  const a = Math.abs(v), d = unit === '%' || a < 10 ? 1 : 0;
  return Number(v).toFixed(d);
}

export class ReviewOverlay {
  constructor() { this.node = null; this.key = null; this.timers = []; this.dismissed = new Set(); }

  // Call with every state snapshot.
  update(s) {
    const r = s?.review;
    if (s?.phase === 'scrubbed' && s.scrub) return this.scrub(s.scrub);
    if (s?.burn) return this.close();                  // Phase 6: after a pass the board gives way to the TMI burn
    if (!r) return this.close();
    const key = `${r.n}`;
    if (r.active) {
      if (this.key !== key) this.open(r, r.elapsed_s || 0);
      return;
    }
    // A finished review stays on screen until dismissed when it failed; a pass
    // gives way to the flight.
    if (r.verdict === 'fail' && s.phase === 'design' && !this.dismissed.has(key)) {
      if (this.key !== key) this.open(r, 1e9);
      return;
    }
    if (this.key === key && r.verdict !== 'fail') this.close();
  }

  close() {
    this.timers.forEach(clearTimeout); this.timers = [];
    if (this.node) { this.node.remove(); this.node = null; audioCue('review.close'); }
    this.key = null;
  }

  open(r, elapsed) {
    this.close();
    this.key = `${r.n}`;
    const t = r.timing, now = reduced() ? 1e9 : elapsed;
    const areas = r.areas.map((a) => this.area(a));
    const verdict = el('div', { class: 'rv-verdict ' + CLASS[r.verdict], role: 'status', 'aria-live': 'polite' });
    const foot = el('div', { class: 'rv-foot' });
    this.node = el('div', { class: 'review-room', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Flight Readiness Review' },
      el('div', { class: 'rv-panel' },
        el('div', { class: 'rv-head' }, el('div', { class: 'rv-kicker' }, `Flight Readiness Review${r.n > 1 ? `, attempt ${r.n}` : ''}`),
          el('h1', {}, 'The board is checking your design.'),
          el('p', { class: 'haze' }, `Five reviewers read the same numbers as your budget board, plus the cases it does not run: one array string lost, the reference storm, the cosmic-ray dose. Launch ${r.launch_iso.slice(0, 10)}.`)),
        el('ol', { class: 'rv-areas' }, areas.map((x) => x.node)), verdict, foot));
    document.body.append(this.node);
    audioCue('review.dim');
    const at = (sec, fn) => { const ms = Math.max(0, (sec - now) * 1000); if (ms <= 0) fn(true); else this.timers.push(setTimeout(() => fn(false), ms)); };
    areas.forEach((x, i) => at(t.dim_s + i * t.area_s, (instant) => x.resolve(instant || reduced(), t.area_s)));
    at(t.dim_s + areas.length * t.area_s, () => this.verdict(r, verdict, foot));
  }

  area(a) {
    const numEl = el('b', { class: 'num rv-num' }, '–'), word = el('span', { class: 'rv-word' }, '');
    const node = el('li', { class: 'rv-area pending' },
      el('div', { class: 'rv-title' }, el('b', {}, a.title), el('span', { class: 'dim small' }, a.persona)),
      el('div', { class: 'rv-lead' }, el('span', { class: 'haze small' }, a.lead.label), el('span', {}, numEl, el('span', { class: 'u' }, ' ' + (a.lead.unit || '')))),
      word,
      el('details', { class: 'rv-findings' }, el('summary', {}, 'Findings'),
        el('ul', {}, a.findings.map((f) => el('li', { class: 'rv-f ' + f.status },
          el('span', { class: 'rv-fword' }, WORD[f.status]), ' ', f.text, ' ',
          el('span', { class: 'dim small' }, `Threshold: ${f.threshold}. `, f.model ? el('span', { class: 'model-tag', title: f.note }, 'MODEL') : null,
            f.sources?.length ? ` Source: ${f.sources.join(', ')}` : ''))))));
    const resolve = (instant, dur) => {
      node.classList.remove('pending'); node.classList.add('on', CLASS[a.verdict]);
      const end = Number(a.lead.number);
      const done = () => { numEl.textContent = fmtNum(end, a.lead.unit); word.textContent = WORD[a.verdict]; word.className = 'rv-word ' + CLASS[a.verdict]; audioCue('review.area', { area: a.id, verdict: a.verdict }); };
      if (instant || !isFinite(end)) return done();
      const t0 = performance.now(), span = Math.min(1800, dur * 600);
      const step = (now) => { const k = Math.min(1, (now - t0) / span), e = 1 - Math.pow(1 - k, 3);
        numEl.textContent = fmtNum(end * e, a.lead.unit); if (k < 1) requestAnimationFrame(step); else done(); };
      requestAnimationFrame(step);
    };
    return { node, resolve };
  }

  verdict(r, box, foot) {
    box.innerHTML = '';
    box.append(el('div', { class: 'rv-big' }, WORD[r.verdict]));
    if (r.verdict === 'fail') {
      box.append(el('p', {}, 'The board will not fly this design.'), el('ul', {}, r.fails.map((f) => el('li', {}, el('b', {}, f.area + ': '), f.text))),
        el('p', { class: 'lantern' }, `Rework costs ${r.slip_days} days of the launch window. The launch date slips, the trip changes, and every seat votes again.`));
      const back = el('button', { class: 'primary', onclick: () => { this.dismissed.add(this.key); this.close(); } }, 'Back to the design bay');
      foot.append(back);
      back.focus();
    } else {
      box.append(el('p', {}, r.liens.length ? `Go for launch, with ${r.liens.length} lien${r.liens.length > 1 ? 's' : ''}. They fly with the ship.` : 'Go for launch.'));
      if (r.liens.length) box.append(el('ul', { class: 'rv-liens' }, r.liens.map((L) => el('li', {}, el('b', {}, L.area + ': '), L.text))));
      foot.append(el('span', { class: 'haze small' }, 'Launching.'));
    }
    foot.append(el('a', { class: 'small', href: roomUrl('/factsheet/'), target: '_blank', rel: 'noopener' }, 'Mission fact sheet'));
    if (r.verdict !== 'pass') science(r.verdict === 'fail' ? 'slip' : 'liens').then((n) => n && box.append(n)).catch(() => {});
    audioCue('review.verdict', { verdict: r.verdict });
  }

  scrub(sc) {
    if (this.key === 'scrub') return;
    this.close(); this.key = 'scrub';
    const again = el('button', { class: 'primary', onclick: async () => { await fetch(roomUrl('/api/fly-again?fresh=1'), { method: 'POST', headers: { 'X-Seat-Token': seatToken || '' } }); location.reload(); } }, 'Start over');
    this.node = el('div', { class: 'review-room', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Mission scrubbed' },
      el('div', { class: 'rv-panel' }, el('div', { class: 'rv-kicker alarm' }, 'Scrubbed'),
        el('h1', {}, 'The window closed on the ground.'), el('p', { class: 'lede' }, sc.text),
        el('p', { class: 'haze small' }, `Window closed ${String(sc.window_end_iso).slice(0, 10)} after ${sc.reviews} reviews. Synodic period ${sc.synodic_days} days: NASA Mars Fact Sheet.`),
        el('div', { class: 'rv-foot' }, again)));
    document.body.append(this.node);
    again.focus();
  }
}
