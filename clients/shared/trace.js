// Design trace: the line a callout leaves in a message area ("Your design"),
// its "why" drawer, and the spectator's lower-third. The server decides what
// fires and when (server/lightlag/sim/callouts.py); this only shows it.
import { drawer, el } from './ll.js';

let cat = null;
const catalog = async () => (cat = cat || (await (await fetch('/api/catalog')).json()));
const SEAT = { control: 'Flight Director', scientist: 'Mission Scientist', ship: 'Commander', engineer: 'Flight Engineer', both: 'The whole team' };

export function whyDrawer(c) {
  drawer('why', 'Why: your design', async () => {
    const C = await catalog().catch(() => ({ sources: {} }));
    return el('div', { class: 'stack why' },
      el('p', { class: 'lede' }, c.text),
      el('table', { class: 'kv' },
        el('tr', {}, el('td', {}, 'The choice'), el('td', { class: 'v txt' }, c.choice || '–')),
        el('tr', {}, el('td', {}, 'Whose call'), el('td', { class: 'v txt' }, `${SEAT[c.seat] || c.seat}, ${c.step} step`)),
        el('tr', {}, el('td', {}, 'When'), el('td', { class: 'v txt' }, `Mission day ${c.day}`))),
      c.counter ? el('div', { class: 'science' }, el('b', {}, 'If you had chosen differently'), el('div', {}, c.counter),
        el('div', { class: 'src' }, el('span', { class: 'model-tag' }, 'MODEL'), ' The same model formula, re-evaluated with the other choice. The mission was not re-run.')) : null,
      c.arrived_et != null && c.side === 'ship' ? el('p', { class: 'haze small' }, `This happened aboard; Lantern learned it when the ship's report arrived, ${Math.round(c.lt_s || 0)} s of light time later.`) : null,
      (c.sources || []).length ? el('div', {}, el('h3', {}, 'Sources'), el('ul', { class: 'src-list' }, c.sources.map((k) => {
        const s = C.sources?.[k] || { title: k };
        return el('li', {}, s.url ? el('a', { href: s.url, target: '_blank', rel: 'noopener' }, s.title) : s.title);
      }))) : null);
  });
}

export function traceItem(c) {
  return el('div', { class: 'msg trace', 'data-callout': c.id },
    el('div', { class: 'meta' }, el('span', { class: 'trace-tag' }, 'Your design'), el('span', {}, `day ${c.day}`), el('span', {}, c.step)),
    el('div', {}, c.text, ' ', el('button', { class: 'ghost small why-btn', 'aria-label': 'Why: show the design choice behind this', onclick: () => whyDrawer(c) }, 'why')));
}

// Add a callout to a message list once, newest first or last as the list prefers.
export function addTrace(list, c, { prepend = false } = {}) {
  if (!list || list.querySelector(`[data-callout="${c.id}"]`)) return;
  list.querySelector('.empty')?.remove();
  const item = traceItem(c);
  if (prepend) list.prepend(item); else { list.append(item); list.scrollTop = list.scrollHeight; }
}

let third = null, thirdTimer = null;
export function lowerThird(c, host = document.body, ms = 14000) {
  third?.remove(); clearTimeout(thirdTimer);
  third = el('div', { class: 'lower-third', role: 'status', 'aria-live': 'polite' },
    el('div', { class: 'lt-tag' }, 'Their design, in flight'), el('div', { class: 'lt-text' }, c.text),
    el('div', { class: 'lt-sub' }, c.counter ? c.counter : `${c.step} step`));
  host.append(third);
  thirdTimer = setTimeout(() => { third?.classList.add('out'); setTimeout(() => third?.remove(), 700); }, ms);
}
