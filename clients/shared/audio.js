// State-driven adaptive score (Web Audio). Four stems, three game
// parameters, and silence designed as a feature.
//
//   params (0..1, smoothed):  delay   = log-scaled one-way light time
//                             danger  = max(alarm level, storm dose rate)
//                             silence = conjunction (1 inside the blackout)
//   stems:   bed     - always-on drone, ducks under silence
//            pulse   - rhythmic layer that slows and thins as delay grows
//            danger  - tension layer, follows `danger`
//            room    - near-silent room tone that takes over during blackout
//
// Drop real stems in /shared/audio/stems/{bed,pulse,danger,room}.ogg and they
// replace the procedural placeholders automatically. See docs/audio-hooks.md.
export class Score {
  constructor() { this.ctx = null; this.p = { delay: 0, danger: 0, silence: 0 }; this.t = { delay: 0, danger: 0, silence: 0 }; this.enabled = false; }

  async start() {
    if (this.ctx) { this.ctx.resume(); this.master.gain.value = 0.5; this.enabled = true; return; }
    const ctx = this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    this.master = ctx.createGain(); this.master.gain.value = 0.5; this.master.connect(ctx.destination);
    this.g = {};
    for (const k of ['bed', 'pulse', 'danger', 'room']) { this.g[k] = ctx.createGain(); this.g[k].gain.value = 0; this.g[k].connect(this.master); }
    for (const k of Object.keys(this.g)) {
      const ok = await this._loadStem(k);
      if (!ok) this._procedural(k);
    }
    this.enabled = true;
    this._loop();
  }

  stop() { if (!this.ctx) return; this.master.gain.value = 0; this.enabled = false; }

  async _loadStem(k) {
    try {
      const r = await fetch(`/shared/audio/stems/${k}.ogg`); if (!r.ok) return false;
      const buf = await this.ctx.decodeAudioData(await r.arrayBuffer());
      const src = this.ctx.createBufferSource(); src.buffer = buf; src.loop = true; src.connect(this.g[k]); src.start();
      return true;
    } catch { return false; }
  }

  _procedural(k) { // placeholder voices: quiet, unobtrusive, replace with real stems
    const ctx = this.ctx;
    if (k === 'bed') {
      for (const f of [55, 82.4, 110.0]) { const o = ctx.createOscillator(); o.type = 'sine'; o.frequency.value = f; const g = ctx.createGain(); g.gain.value = 0.05; o.connect(g).connect(this.g.bed); o.start(); }
    } else if (k === 'pulse') {
      const o = ctx.createOscillator(); o.type = 'triangle'; o.frequency.value = 220; const g = ctx.createGain(); g.gain.value = 0;
      o.connect(g).connect(this.g.pulse); o.start(); this.pulseGain = g;
    } else if (k === 'danger') {
      const o = ctx.createOscillator(); o.type = 'sawtooth'; o.frequency.value = 46.25; const f = ctx.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = 300;
      const g = ctx.createGain(); g.gain.value = 0.06; o.connect(f).connect(g).connect(this.g.danger); o.start();
    } else if (k === 'room') {
      const n = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate), d = n.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * 0.02;
      const s = ctx.createBufferSource(); s.buffer = n; s.loop = true; const f = ctx.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = 400;
      s.connect(f).connect(this.g.room); s.start();
    }
  }

  // called with every server state snapshot
  update(s) {
    const g = s?.geometry; if (!g) return;
    this.t.delay = Math.min(1, Math.log10(1 + g.lt_up_s) / Math.log10(1 + 1320));
    const link = s.comms?.link;
    this.t.silence = link?.blackout ? 1 : 0;
    const alarms = s.ship?.alarms || [];
    const lvl = Math.max(0, ...alarms.map((a) => ({ caution: 0.4, warning: 0.7, emergency: 1 }[a.level] || 0)));
    const dose = s.ship?.telemetry?.dose_rate ?? s.control?.telemetry?.dose_rate ?? 0;
    this.t.danger = Math.max(lvl, Math.min(1, Math.max(0, (dose - 100) / 3000)));
  }

  _loop() {
    if (!this.ctx) return;
    const k = 0.03;
    for (const key of Object.keys(this.p)) this.p[key] += (this.t[key] - this.p[key]) * k;
    const { delay, danger, silence } = this.p, now = this.ctx.currentTime;
    this.g.bed.gain.setTargetAtTime((1 - silence) * 0.8, now, 0.3);
    this.g.pulse.gain.setTargetAtTime((1 - silence) * (1 - delay) * 0.6, now, 0.3);
    this.g.danger.gain.setTargetAtTime((1 - silence * 0.7) * danger, now, 0.3);
    this.g.room.gain.setTargetAtTime(0.15 + silence * 0.85, now, 0.6);
    if (this.pulseGain) { // a heartbeat that slows as the gap grows
      const period = 0.6 + 2.4 * delay, phase = (now % period) / period;
      this.pulseGain.gain.setTargetAtTime(phase < 0.08 ? 0.05 : 0, now, 0.02);
    }
    requestAnimationFrame(() => this._loop());
  }
}

// One-shot UI sounds
export function quindar(ctx, start = true) {
  if (!ctx) return;
  const o = ctx.createOscillator(), g = ctx.createGain();
  o.frequency.value = start ? 2525 : 2475; g.gain.value = 0.08; o.connect(g).connect(ctx.destination);
  o.start(); o.stop(ctx.currentTime + 0.25);
}
