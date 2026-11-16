// Upgrade A (Phase 6): the trans-Mars injection burn, played in every client of a
// room after the review board passes the design and before the first scene card.
//
// The server records the moment and sends `burn` in each snapshot (timings, the
// design's delta-v, the light time); this file only plays it. Nothing here keeps
// numbers of its own: every timing comes from data/story/launch.yaml.
//
//   Cockpit (ship seats): T-10 on the displays, a building rumble and camera shake,
//     engine glow in the window, flickering annunciators, BURN on the TERN display,
//     the delta-v counter on the engineering display; then engine cutoff: everything
//     stops at once, a beat of silence, the stars settle, the cabin hum fades back in.
//   Earth room (Earth seats): TMI BURN and the counter on the flight-dynamics tile,
//     a Quindar tone and one "Cutoff confirmed" line, one light time late.
//   Spectator: a short engine-glow pulse on the ship model and the counter.
//   Reduced motion or low effects: no shake, flicker or glow; the counter and the
//     cutoff line only.
//
// Audio hooks: window 'll-audio' CustomEvents (see docs/audio-hooks.md):
//   tmi.start, tmi.count (detail.n), tmi.cutoff, tmi.silence_end, tmi.end,
//   tmi.earth_quindar, tmi.earth_line.
import { el, num } from './ll.js';

// Capture aid, like ?fxdebug=slow: ?tmihold=10.5 holds the sequence at that second
// (here, just after engine cutoff) so a slow machine can screenshot it. Never in play.
const HOLD = Number(new URLSearchParams(location.search).get('tmihold')) || null;
const cue = (name, detail = {}) => dispatchEvent(new CustomEvent('ll-audio', { detail: { cue: name, ...detail } }));
const ease = (k) => 1 - Math.pow(1 - Math.min(1, Math.max(0, k)), 3);

export class LaunchFX {
  // side: 'ship' | 'earth' | 'spectator'. room: the ImmersiveRoom (may be null).
  // view: the spectator's SpaceView (may be null).
  constructor({ side, room = null, view = null }) {
    this.side = side; this.room = room; this.view = view;
    this.burn = null; this.t0 = null; this.done = new Set(); this.node = null; this.raf = null;
    this.reducedMq = matchMedia('(prefers-reduced-motion: reduce)');
  }

  get calm() { return this.reducedMq.matches || !!this.room?.fxLow || !!this.room?.dashboardMode; }

  // Call with every state snapshot.
  update(s) {
    const b = s?.burn;
    if (!b || !b.active) {
      // The server moved on (mission started): finish anything still playing.
      if (this.burn && s?.phase !== 'review' && !HOLD) this.finish();
      return;
    }
    const key = String(b.n);
    if (this.done.has(key) || this.burn) return;
    // Earth hears the ship one light time later (MODEL, about 0.02 s at TMI).
    const lag = this.side === 'earth' ? (b.lt_s || 0) : 0;
    this.burn = b; this.key = key;
    this.t0 = performance.now() - (b.elapsed_s - lag) * 1000;
    this.start();
  }

  start() {
    const b = this.burn, earth = this.side === 'earth';
    document.body.classList.add('tmi-burning');
    if (this.room?.focus) this.room.close(false);
    const dvNum = el('b', { class: 'num tmi-dv-num' }, '0.00');
    const count = el('div', { class: 'tmi-count num', 'aria-hidden': 'true' }, '');
    const cut = el('div', { class: 'tmi-cut', hidden: true }, earth ? `${b.earth.line}.` : 'Engine cutoff.');
    this.node = el('div', { class: `tmi tmi-${this.side}${this.calm ? ' calm' : ''}`, role: 'status', 'aria-live': 'polite', 'aria-label': b.title },
      el('div', { class: 'tmi-kicker' }, earth ? `Lantern · ${b.earth.tile}` : b.title),
      earth ? el('div', { class: 'tmi-sub haze small' }, `${b.title}. Houston hears it ${num(b.lt_s, 2)} s after the ship.`) : null,
      this.calm ? null : count,
      el('div', { class: 'tmi-dv' }, el('span', { class: 'haze' }, b.dv_label), ' ', dvNum, el('span', { class: 'u' }, ' km/s'), ' ',
        el('span', { class: 'model-tag', title: b.dv_note }, 'MODEL')),
      cut);
    document.body.append(this.node);
    this.els = { dvNum, count, cut };
    if (this.room) this.room.burnState = { phase: 'burn', k: 0, n: 10, dv: 0, title: b.title, earth: b.earth, cockpit: b.cockpit, total: b.dv_kms, calm: this.calm };
    if (this.view && b.vehicle) this.view.burnPose?.(b.vehicle, b.lt_s);
    cue('tmi.start', { side: this.side });
    if (this.room && this.side === 'ship' && !this.calm) this.room.sound.burnStart?.(Math.max(0.1, b.timing.countdown_s - this.elapsed()));
    const loop = () => { this.raf = requestAnimationFrame(loop); this.tick(); };
    loop();
  }

  elapsed() { return (performance.now() - this.t0) / 1000; }

  tick() {
    const b = this.burn; if (!b) return;
    const t = this.elapsed(), T = b.timing, C = T.countdown_s, S = T.silence_s, total = T.total_s;
    if (t < 0) return;                                    // Earth: the light has not arrived yet
    if (HOLD && t >= HOLD && this.held) return;
    const k = Math.min(1, t / C), dv = b.dv_kms * ease(k), n = Math.max(0, Math.ceil(C - t));
    this.els.dvNum.textContent = num(t >= C ? b.dv_kms : dv, 2);
    if (t < C) {
      this.els.count.textContent = `T-${n}`;
      if (!this.done.has('n' + n)) { this.done.add('n' + n); cue('tmi.count', { n, side: this.side }); }
      if (this.room?.burnState) Object.assign(this.room.burnState, { phase: 'burn', k, n, dv });
      this.view?.burnGlow?.(this.calm ? 0.6 : 0.5 + 0.5 * k + 0.08 * Math.sin(t * 23));
    } else if (!this.done.has('cut')) {
      this.done.add('cut');
      this.els.count.textContent = '';
      this.els.cut.hidden = false;
      if (this.room?.burnState) Object.assign(this.room.burnState, { phase: 'cut', k: 1, n: 0, dv: b.dv_kms, cutAt: performance.now() });
      this.view?.burnGlow?.(0);
      cue('tmi.cutoff', { side: this.side });
      if (this.room) {
        if (this.side === 'ship') this.room.sound.burnCut?.();
        else this.earthLine();
        this.room.paint?.();
      }
    }
    if (t >= C + S && !this.done.has('silence')) {
      this.done.add('silence'); cue('tmi.silence_end', { side: this.side });
      if (this.room?.burnState) this.room.burnState.phase = 'settle';
      if (this.room && this.side === 'ship') this.room.sound.humBack?.(T.settle_s);
    }
    if (HOLD && t >= HOLD) { this.held = true; document.body.dataset.tmiHeld = '1'; return; }
    if (t >= total) this.finish();
  }

  // Houston: Quindar open, one line, Quindar close (browser speech only if sound is on).
  earthLine() {
    const snd = this.room?.sound, line = this.burn.earth.line;
    cue('tmi.earth_quindar'); cue('tmi.earth_line', { text: line });
    if (!snd?.on) return;
    snd.quindar(true);
    try {
      const u = new SpeechSynthesisUtterance(line); u.rate = 1.05; u.pitch = 0.95; u.volume = 0.8;
      u.onend = () => snd.quindar(false);
      setTimeout(() => speechSynthesis.speak(u), 300);
    } catch { setTimeout(() => snd.quindar(false), 900); }
  }

  finish() {
    if (!this.burn) return;
    cancelAnimationFrame(this.raf);
    this.done.add(this.key);
    if (this.room) { this.room.burnState = null; this.room.sound.humBack?.(0.2); this.room.paint?.(); }
    this.view?.burnGlow?.(0);
    const n = this.node; this.node = null; this.burn = null;
    document.body.classList.remove('tmi-burning');
    if (n) { n.classList.add('out'); setTimeout(() => n.remove(), this.calm ? 0 : 700); }
    cue('tmi.end', { side: this.side });
  }
}
