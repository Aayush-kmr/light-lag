// Atmosphere for the 3D rooms: ambient console displays, the real sky outside
// the cockpit canopy, synthesized sound, and a bloom pass.
//
// Ambient displays are decorative "mock clusters": traces, grids and spectra
// that animate with the mission's activity but carry no numeric readouts, so
// they never present invented values. Every number a player reads still comes
// from a bound screen or instrument.
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

// ------------------------------------------------------------------ ambient
const AMBIENT_TITLES = {
  scope: 'Carrier lock', strip: 'Bus trend', matrix: 'Frame sync', spectrum: 'X-band spectrum',
  log: 'Telemetry frames', adi: 'Attitude', bars: 'Loads', ring: 'Antenna track',
};

export class Ambient {
  constructor(accent = '#d4bd92') {
    this.accent = accent; this.feeds = new Map(); this.burst = 0; this.alert = 0; this.lines = []; this.every = 80;
    this.seed = 1; this.t0 = performance.now();
  }

  material(type) {
    if (this.feeds.has(type)) return this.feeds.get(type).material;
    const canvas = document.createElement('canvas'); canvas.width = 512; canvas.height = 320;
    const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace; texture.minFilter = THREE.LinearFilter;
    const material = new THREE.MeshBasicMaterial({ map: texture, toneMapped: false });
    this.feeds.set(type, { type, canvas, g: canvas.getContext('2d'), texture, material, buf: [] });
    return material;
  }

  rand() { this.seed = (this.seed * 16807) % 2147483647; return this.seed / 2147483647; }

  update(now, state) {
    if (now - (this.last || 0) < this.every) return; this.last = now;
    const t = (now - this.t0) / 1000, k = 1 + this.burst * 3;
    const tern = state?.ship?.tern_log || state?.control?.tern_log || [];
    // TERN log entries are decision objects: show "rule: rationale", never [object Object].
    const line = (x) => (typeof x === 'string' ? x : x?.rule ? `${x.rule.replace(/_/g, ' ')}: ${x.rationale || x.text || x.outcome || ''}` : x?.text || x?.msg || '');
    if (tern.length) this.lines = tern.slice(-6).map((x) => line(x).slice(0, 46)).filter(Boolean);
    for (const f of this.feeds.values()) this.draw(f, t, k);
    this.burst = Math.max(0, this.burst - 0.035); this.alert = Math.max(0, this.alert - 0.01);
  }

  draw(f, t, k) {
    const g = f.g, W = 512, H = 320, A = this.accent, hot = this.burst > 0.05;
    g.fillStyle = '#07090a'; g.fillRect(0, 0, W, H);
    g.fillStyle = '#6f7477'; g.font = '18px B612'; g.fillText(AMBIENT_TITLES[f.type], 16, 26);
    g.fillStyle = hot ? '#e0a43a' : '#3d6b48'; g.fillRect(W - 28, 14, 12, 12);
    g.strokeStyle = '#15191b'; g.lineWidth = 1;
    for (let x = 16; x < W; x += 40) { g.beginPath(); g.moveTo(x, 40); g.lineTo(x, H - 12); g.stroke(); }
    for (let y = 40; y < H; y += 40) { g.beginPath(); g.moveTo(16, y); g.lineTo(W - 16, y); g.stroke(); }
    const trace = (fn, color, w = 2) => { g.strokeStyle = color; g.lineWidth = w; g.beginPath(); for (let x = 16; x < W - 16; x += 4) { const y = fn(x); x === 16 ? g.moveTo(x, y) : g.lineTo(x, y); } g.stroke(); };
    const n = () => (this.rand() - 0.5) * k;
    switch (f.type) {
      case 'scope':
        trace((x) => 120 + Math.sin(x / 22 + t * 3) * 34 * (0.6 + 0.4 * Math.sin(t)) + n() * 10, A);
        trace((x) => 220 + Math.sin(x / 9 - t * 5) * 14 + n() * 16, '#7fb3c2', 1.5);
        break;
      case 'strip': {
        f.buf.push([Math.sin(t * 0.7) * 20 + n() * 8, Math.cos(t * 0.4) * 30 + n() * 6, n() * 22]);
        if (f.buf.length > 120) f.buf.shift();
        ['#ebe8e1', A, '#7fb3c2'].forEach((c, j) => {
          g.strokeStyle = c; g.lineWidth = 1.6; g.beginPath();
          f.buf.forEach((v, i) => { const x = 16 + i * 4, y = 90 + j * 80 + v[j]; i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke();
        });
        break;
      }
      case 'matrix':
        for (let r = 0; r < 6; r++) for (let c = 0; c < 12; c++) {
          const v = Math.sin(r * 7.1 + c * 3.3 + Math.floor(t * (hot ? 8 : 1.5)));
          g.fillStyle = v > 0.93 ? '#e0a43a' : v > 0.2 ? '#3f7a4c' : '#1b2224';
          g.fillRect(20 + c * 40, 46 + r * 44, 32, 34);
        }
        break;
      case 'spectrum':
        for (let i = 0; i < 60; i++) {
          const peak = Math.exp(-((i - 30) ** 2) / 6) * 180 + Math.exp(-((i - 22) ** 2) / 3) * 40 + Math.exp(-((i - 38) ** 2) / 3) * 40;
          const h = 14 + peak * (0.85 + 0.15 * Math.sin(t * 4 + i)) + Math.abs(n()) * 26;
          g.fillStyle = i === 30 ? A : '#4d7f8c'; g.fillRect(18 + i * 8, H - 14 - h, 6, h);
        }
        break;
      case 'log': {
        g.font = '15px "B612 Mono"';
        const rows = 15, speed = hot ? 12 : 2, off = Math.floor(t * speed);
        for (let i = 0; i < rows; i++) {
          const idx = off + i, line = this.lines.length && idx % 5 === 0 ? this.lines[idx % this.lines.length]
            : Array.from({ length: 12 }, (_, j) => ((idx * 2654435761 + j * 40503) >>> 0).toString(16).slice(-4).toUpperCase()).join(' ').slice(0, 46);
          g.fillStyle = idx % 5 === 0 && this.lines.length ? A : i === rows - 1 ? '#ebe8e1' : '#5d8a6a';
          g.fillText(line, 16, 54 + i * 18);
        }
        break;
      }
      case 'adi': {
        const cx = W / 2, cy = H / 2 + 14, R = 118, roll = Math.sin(t * 0.21) * 0.12 + n() * 0.02, pitch = Math.sin(t * 0.13) * 30;
        g.save(); g.beginPath(); g.arc(cx, cy, R, 0, 7); g.clip();
        g.translate(cx, cy); g.rotate(roll);
        g.fillStyle = '#3a4a57'; g.fillRect(-R, -R + pitch, 2 * R, R); g.fillStyle = '#4b3d2f'; g.fillRect(-R, pitch, 2 * R, R);
        g.strokeStyle = '#ebe8e1'; g.lineWidth = 2; g.beginPath(); g.moveTo(-R, pitch); g.lineTo(R, pitch); g.stroke();
        for (let p = -60; p <= 60; p += 20) { g.beginPath(); g.moveTo(-30, pitch + p); g.lineTo(30, pitch + p); g.stroke(); }
        g.restore();
        g.strokeStyle = A; g.lineWidth = 4; g.beginPath(); g.moveTo(cx - 60, cy); g.lineTo(cx - 18, cy); g.lineTo(cx, cy + 12); g.lineTo(cx + 18, cy); g.lineTo(cx + 60, cy); g.stroke();
        g.strokeStyle = '#2a2f33'; g.lineWidth = 6; g.beginPath(); g.arc(cx, cy, R, 0, 7); g.stroke();
        break;
      }
      case 'bars':
        for (let i = 0; i < 10; i++) {
          const v = 0.35 + 0.25 * Math.sin(t * 0.8 + i * 1.7) + Math.abs(n()) * 0.15;
          g.fillStyle = '#1b2224'; g.fillRect(24 + i * 48, 50, 34, 250);
          g.fillStyle = v > 0.82 ? '#e0a43a' : i % 3 ? '#4d7f8c' : A; g.fillRect(24 + i * 48, 300 - v * 250, 34, v * 250);
        }
        break;
      case 'ring': {
        const cx = W / 2, cy = H / 2 + 14;
        g.strokeStyle = '#2a3236'; g.lineWidth = 1.5;
        for (const r of [40, 80, 120]) { g.beginPath(); g.arc(cx, cy, r, 0, 7); g.stroke(); }
        for (let a = 0; a < 12; a++) { g.beginPath(); g.moveTo(cx, cy); g.lineTo(cx + Math.cos(a * Math.PI / 6) * 124, cy + Math.sin(a * Math.PI / 6) * 124); g.stroke(); }
        const az = t * 0.05, el = 70 + Math.sin(t * 0.3) * 4 + n() * 3;
        g.fillStyle = A; g.beginPath(); g.arc(cx + Math.cos(az) * el, cy + Math.sin(az) * el, 7, 0, 7); g.fill();
        g.strokeStyle = A; g.setLineDash([4, 6]); g.beginPath(); g.arc(cx, cy, el, az - 1.2, az); g.stroke(); g.setLineDash([]);
        break;
      }
    }
    if (this.burst > 0.6 && this.rand() < 0.4) { g.fillStyle = 'rgba(224,164,58,0.08)'; g.fillRect(0, 0, W, H); }
    f.texture.needsUpdate = true;
  }
}

// ---------------------------------------------------------------------- sky
// Real sky outside the cockpit: Yale Bright Star Catalog (J2000) plus the Sun,
// Earth and Mars in their ephemeris directions from the ship. The canopy faces
// Mars, the destination; "up" is ecliptic north.
const OBLIQUITY = THREE.MathUtils.degToRad(23.4393);
const ECLIPTIC_NORTH = new THREE.Vector3(0, -Math.sin(OBLIQUITY), Math.cos(OBLIQUITY));

function glowTexture(inner, outer) {
  const c = document.createElement('canvas'); c.width = c.height = 128; const g = c.getContext('2d');
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, inner); grad.addColorStop(0.12, inner); grad.addColorStop(0.35, outer); grad.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = grad; g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}

function labelTexture(text) {
  const c = document.createElement('canvas'); c.width = 512; c.height = 128; const g = c.getContext('2d');
  g.strokeStyle = 'rgba(156,192,202,0.75)'; g.lineWidth = 3;
  g.strokeRect(6, 34, 60, 60); g.fillStyle = 'rgba(214,226,230,0.92)'; g.font = '34px B612'; g.textBaseline = 'middle'; g.fillText(text, 84, 64);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}

function starColour(k) {
  const t = Math.max(2500, Math.min(30000, k)) / 100; let r, g, b;
  r = t <= 66 ? 255 : 329.7 * (t - 60) ** -0.1332; g = t <= 66 ? 99.47 * Math.log(t) - 161.1 : 288.1 * (t - 60) ** -0.0755; b = t >= 66 ? 255 : t <= 19 ? 0 : 138.5 * Math.log(t - 10) - 305;
  return [r, g, b].map((v) => Math.max(0, Math.min(255, v)) / 255);
}

export class Sky {
  constructor(scene, sunLight) {
    this.group = new THREE.Group(); this.group.renderOrder = -10; scene.add(this.group);
    this.inner = new THREE.Group(); this.group.add(this.inner); // J2000 → cabin rotation
    this.sunLight = sunLight; this.R = 60;
    fetch('/shared/assets/stars_bsc5.json').then((r) => r.json()).then((d) => this.stars(d.stars)).catch(() => {});
    this.sun = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture('rgba(255,250,236,1)', 'rgba(255,214,150,0.18)'), toneMapped: false, depthWrite: false, fog: false }));
    this.sun.scale.setScalar(9); this.inner.add(this.sun);
    this.bodies = {};
    for (const [key, color, name] of [['earth', 'rgba(170,205,235,1)', 'Earth'], ['mars', 'rgba(232,150,104,1)', 'Mars']]) {
      const dot = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture(color, 'rgba(0,0,0,0)'), toneMapped: false, depthWrite: false, sizeAttenuation: false }));
      dot.scale.setScalar(0.012);
      const label = new THREE.Sprite(new THREE.SpriteMaterial({ map: labelTexture(name), toneMapped: false, depthWrite: false, sizeAttenuation: false, transparent: true }));
      label.scale.set(0.12, 0.03, 1); label.center.set(0.065, 0.5);
      this.inner.add(dot, label); this.bodies[key] = { dot, label, name };
    }
  }

  stars(list) {
    const pos = [], col = [], size = [];
    for (const [x, y, z, mag, k] of list) {
      if (mag > 6.2) continue;
      pos.push(x * this.R, y * this.R, z * this.R);
      const c = starColour(k), b = Math.min(1.6, 2.4 * 10 ** (-0.4 * (mag - 1)));
      col.push(c[0] * (0.35 + b), c[1] * (0.35 + b), c[2] * (0.35 + b)); size.push(Math.max(1.4, 4.2 - mag * 0.5));
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
    geo.setAttribute('size', new THREE.Float32BufferAttribute(size, 1));
    const mat = new THREE.ShaderMaterial({
      vertexColors: true, depthWrite: false, toneMapped: false,
      vertexShader: 'attribute float size; varying vec3 vC; void main(){ vC = color; gl_PointSize = size * 1.5; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
      fragmentShader: 'varying vec3 vC; void main(){ vec2 p = gl_PointCoord - 0.5; float d = length(p); if (d > 0.5) discard; gl_FragColor = vec4(vC * smoothstep(0.5, 0.0, d) * 1.6, 1.0); }',
    });
    this.points = new THREE.Points(geo, mat); this.points.frustumCulled = false; this.inner.add(this.points);
  }

  // forward: J2000 unit vector the canopy faces (Mars). Returns the J2000→cabin quaternion.
  update(viewport, cameraPos) {
    this.group.position.copy(cameraPos);
    if (!viewport) return;
    const f = new THREE.Vector3().fromArray(viewport.mars.dir).normalize();
    const r = new THREE.Vector3().crossVectors(f, ECLIPTIC_NORTH).normalize(), u = new THREE.Vector3().crossVectors(r, f);
    // Rows map J2000 into cabin axes: x = right, y = up, z = backward.
    const m = new THREE.Matrix4().set(r.x, r.y, r.z, 0, u.x, u.y, u.z, 0, -f.x, -f.y, -f.z, 0, 0, 0, 0, 1);
    this.inner.quaternion.setFromRotationMatrix(m);
    const place = (obj, dir, d) => obj.position.copy(new THREE.Vector3().fromArray(dir).normalize().multiplyScalar(d));
    place(this.sun, viewport.sun.dir, this.R * 0.9);
    // Sun's real angular size sets the disc; the glare around it is a camera effect.
    this.sun.scale.setScalar(Math.max(4, Math.tan(THREE.MathUtils.degToRad(viewport.sun.ang_diam_deg / 2)) * this.R * 0.9 * 2 * 22));
    for (const key of ['earth', 'mars']) { place(this.bodies[key].dot, viewport[key].dir, this.R * 0.85); place(this.bodies[key].label, viewport[key].dir, this.R * 0.85); }
    if (this.sunLight) {
      const s = new THREE.Vector3().fromArray(viewport.sun.dir).applyQuaternion(this.inner.quaternion);
      this.sunLight.position.copy(s.multiplyScalar(12)); this.sunLight.target.position.set(0, 1, -1);
    }
  }
}

// -------------------------------------------------------------------- sound
// All sounds are synthesized; no recordings. Quindar tones (2525 Hz to open,
// 2475 Hz to close, 250 ms) are the real NASA air-to-ground keying tones.
export class Sound {
  constructor(side) { this.side = side; this.on = false; this.ctx = null; }

  start() {
    if (this.ctx) { this.ctx.resume?.(); return; }
    try { this.ctx = new AudioContext(); } catch { return; }
    const c = this.ctx; this.master = c.createGain(); this.master.gain.value = this.on ? 0.9 : 0; this.master.connect(c.destination);
    const len = c.sampleRate * 3, buf = c.createBuffer(1, len, c.sampleRate), d = buf.getChannelData(0);
    let last = 0; for (let i = 0; i < len; i++) { const w = Math.random() * 2 - 1; last = (last + 0.02 * w) / 1.02; d[i] = last * 3.5; } // brown noise
    this.noise = buf;
    // Room tone: HVAC in mission control, fans and pumps aboard.
    const src = c.createBufferSource(); src.buffer = buf; src.loop = true;
    const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = this.side === 'ship' ? 520 : 320;
    const bed = c.createGain(); bed.gain.value = this.side === 'ship' ? 0.22 : 0.16;
    src.connect(lp).connect(bed).connect(this.master); src.start();
    const hum = c.createOscillator(); hum.frequency.value = this.side === 'ship' ? 118 : 60; const hg = c.createGain(); hg.gain.value = 0.012;
    hum.connect(hg).connect(this.master); hum.start(); this.humGain = hg; this.bedGain = bed; this.bedLevel = bed.gain.value;
    // Occasional distant console chirps.
    const chirp = () => { if (this.on && !this.burnSilent) this.beep(1400 + Math.random() * 1600, 0.05, 0.012); setTimeout(chirp, 4000 + Math.random() * 9000); };
    setTimeout(chirp, 3000);
  }

  // Conjunction aboard: the cabin hum drops out and the fans go quiet.
  quiet(on) {
    if (this.quietOn === on) return; this.quietOn = on;
    if (!this.ctx || !this.humGain) { this.quietOn = undefined; return; }
    const t = this.ctx.currentTime;
    this.humGain.gain.setTargetAtTime(on ? 0 : 0.012, t, 1.2); this.bedGain.gain.setTargetAtTime(on ? this.bedLevel * 0.35 : this.bedLevel, t, 1.2);
  }

  set(on) { this.on = on; if (on) this.start(); if (this.master) this.master.gain.setTargetAtTime(on ? 0.9 : 0, this.ctx.currentTime, 0.15); }

  tone(freq, dur, vol = 0.05, type = 'sine', at = 0) {
    if (!this.ctx || !this.on) return; const c = this.ctx, t = c.currentTime + at;
    const o = c.createOscillator(), g = c.createGain(); o.type = type; o.frequency.setValueAtTime(freq, t);
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(vol, t + 0.008); g.gain.setValueAtTime(vol, t + dur - 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g).connect(this.master); o.start(t); o.stop(t + dur + 0.02);
  }

  burstNoise(dur, freq, q, vol, at = 0) {
    if (!this.ctx || !this.on) return; const c = this.ctx, t = c.currentTime + at;
    const s = c.createBufferSource(); s.buffer = this.noise; const f = c.createBiquadFilter(); f.type = 'bandpass'; f.frequency.value = freq; f.Q.value = q;
    const g = c.createGain(); g.gain.setValueAtTime(vol, t); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    s.connect(f).connect(g).connect(this.master); s.start(t, Math.random()); s.stop(t + dur + 0.02);
  }

  beep(f = 1800, d = 0.06, v = 0.03) { this.tone(f, d, v); }
  hover() { this.tone(2600, 0.018, 0.006); }
  clunk() { this.burstNoise(0.07, 1800, 1.2, 0.5); this.tone(90, 0.09, 0.08, 'triangle'); this.burstNoise(0.03, 4200, 3, 0.25, 0.03); }
  press() { this.burstNoise(0.03, 3000, 2, 0.25); this.tone(1320, 0.07, 0.02, 'sine', 0.02); this.tone(1760, 0.07, 0.02, 'sine', 0.1); }
  open() { this.tone(880, 0.05, 0.02); this.tone(1320, 0.05, 0.02, 'sine', 0.06); this.burstNoise(0.35, 600, 0.7, 0.06, 0.02); }
  close() { this.tone(1320, 0.05, 0.02); this.tone(880, 0.06, 0.02, 'sine', 0.06); }
  quindar(intro = true) { this.tone(intro ? 2525 : 2475, 0.25, 0.04); }
  chatter() { for (let i = 0; i < 9; i++) this.tone(900 + Math.random() * 2600, 0.035, 0.012, 'square', i * 0.07); }
  alarm() { for (let i = 0; i < 6; i++) this.tone(i % 2 ? 750 : 1000, 0.22, 0.05, 'square', i * 0.24); }
  rumble() { this.burstNoise(1.6, 90, 0.6, 0.6); }

  // Phase 6, the TMI burn: a low rumble that builds for `dur` seconds, then stops
  // dead at engine cutoff with the cabin hum, and the hum fades back after the beat.
  burnStart(dur) {
    if (!this.ctx || !this.on) return; const c = this.ctx, t = c.currentTime;
    this.burnCut(true);
    const src = c.createBufferSource(); src.buffer = this.noise; src.loop = true;
    const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.setValueAtTime(70, t); lp.frequency.linearRampToValueAtTime(190, t + dur);
    const g = c.createGain(); g.gain.setValueAtTime(0.06, t); g.gain.linearRampToValueAtTime(1.1, t + dur);
    const sub = c.createOscillator(); sub.type = 'sine'; sub.frequency.setValueAtTime(34, t); sub.frequency.linearRampToValueAtTime(42, t + dur);
    const sg = c.createGain(); sg.gain.setValueAtTime(0.01, t); sg.gain.linearRampToValueAtTime(0.16, t + dur);
    src.connect(lp).connect(g).connect(this.master); sub.connect(sg).connect(this.master); src.start(t); sub.start(t);
    this.burnNodes = { src, sub, g, sg };
  }

  burnCut(quietOnly = false) {
    const n = this.burnNodes; this.burnNodes = null;
    if (!this.ctx) return; const t = this.ctx.currentTime;
    if (n) { for (const x of [n.g, n.sg]) { x.gain.cancelScheduledValues(t); x.gain.setValueAtTime(x.gain.value, t); x.gain.linearRampToValueAtTime(0, t + 0.012); } n.src.stop(t + 0.03); n.sub.stop(t + 0.03); }
    if (quietOnly) return;
    // Pure silence: the room tone goes too.
    for (const x of [this.humGain, this.bedGain]) if (x) { x.gain.cancelScheduledValues(t); x.gain.setValueAtTime(0, t); }
    this.burnSilent = true;
  }

  humBack(dur = 0.6) {
    if (!this.burnSilent || !this.ctx) return; this.burnSilent = false;
    const t = this.ctx.currentTime, tc = Math.max(0.05, dur / 3);
    if (this.humGain) this.humGain.gain.setTargetAtTime(this.quietOn ? 0 : 0.012, t, tc);
    if (this.bedGain) this.bedGain.gain.setTargetAtTime(this.quietOn ? this.bedLevel * 0.35 : this.bedLevel, t, tc);
  }
}

// -------------------------------------------------------------------- bloom
export function composer(renderer, scene, camera) {
  const fx = new EffectComposer(renderer);
  fx.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(innerWidth / 2, innerHeight / 2), 0.55, 0.5, 0.82);
  fx.addPass(bloom); fx.addPass(new OutputPass());
  fx.bloom = bloom; return fx;
}
