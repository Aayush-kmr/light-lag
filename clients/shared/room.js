// A physical workspace around the existing, authoritative role clients.
// In-world screens show snapshot data; focusing a screen mounts its original
// HTML instruments. Forms, commands, delayed knowledge and IDs stay intact.
import * as THREE from 'three';
import { RoomBuilder } from './room-geometry.js';
import { $, el, num, dur, etIso, roomUrl, roomChip } from './ll.js';
import { drawOrrery } from './viz.js';
import { Ambient, Sky, Sound, composer } from './room-fx.js';
// Every phase before launch shows the design bay: design, the review board, a scrub.
const PRE_FLIGHT = new Set(['design', 'review', 'scrubbed']);

const PANELS = {
  design: ['Mission design', null], trajectory: ['Flight dynamics', '.wall-plot'],
  dsn: ['Deep Space Network', '.wall-dsn', '.link-settings'], weather: ['Space weather', '.wall-weather'],
  telemetry: ['Received vehicle telemetry', '#ship-col'], radio: ['Communications', '.radio-desk'],
  science: ['Science return', '#sci-pane'], crew: ['Crew operations', '#crew-pane'],
  autonomy: ['Autonomy and policy', '[data-pane="rules"]'], assistant: ['Flight assistant', '[data-pane="fido"]'],
};
const SHIP_PANELS = {
  design: ['Flight configuration', null], systems: ['Engineering instruments', '#side [data-pane="systems"]'],
  commander: ['Commander · crew safety', '#commander-station'], tern: ['Mission computer · TERN', '#side [data-pane="tern"]'],
  radio: ['Air-to-ground communications', '#side [data-pane="lantern"]'],
  crew: ['Crew operations', '#side [data-pane="crew"]'], optical: ['Exterior inspection', '.cabin-window'],
  window: ['Exterior inspection', '.cabin-window'],
};
// Display palette: near-black glass, off-white ink, one accent per side.
// Caution/warning colours stay semantic (amber caution, red warning, green nominal).
const INK = '#ebe8e1', META = '#8f9599', LINE = '#24282b', BG = '#0b0d0e';
const CAUTION = '#e0a43a', WARNING = '#e8685c', NOMINAL = '#9cc995';
let ACCENT = '#d4bd92';

export class ImmersiveRoom {
  constructor({ side, seat, action }) {
    this.side = side; this.ship = side === 'ship'; this.seat = seat; this.action = action;
    this.panels = this.ship ? SHIP_PANELS : PANELS;
    this.targets = []; this.feeds = new Map(); this.gauges = []; this.controls = [];
    this.state = null; this.focus = null; this.moved = [];
    this.reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.root = el('div', { id: 'room-root', 'data-room': side });
    this.canvas = el('canvas', { id: 'room-scene', 'aria-label': this.ship ? 'Interactive Meridian spacecraft cabin' : 'Interactive Lantern mission control room' });
    this.vignette = el('div', { class: 'room-vignette', 'aria-hidden': 'true' });
    this.root.append(this.canvas, this.vignette); document.body.prepend(this.root);
    this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(1.5, devicePixelRatio || 1));
    this.renderer.toneMapping = THREE.AgXToneMapping; this.renderer.toneMappingExposure = this.ship ? 0.95 : 1.0;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.type = THREE.PCFShadowMap;
    ACCENT = this.ship ? '#9cc0ca' : '#d4bd92';
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(this.ship ? 64 : 56, innerWidth / innerHeight, 0.03, 80);
    this.camera.up.set(0, 1, 0); this.look = new THREE.Vector3();
    // Lights, materials and geometry live in room-geometry.js / room-earth.js / room-cabin.js.
    this.ambient = new Ambient(ACCENT); this.sound = new Sound(side);
    const send = action; this.action = (...a) => { send(...a); this.react('command'); };
    const builder = new RoomBuilder(this.scene,
      (...args) => this.screen(...args), (...args) => this.control(...args), (...args) => this.gauge(...args), this.renderer,
      { ambient: (type) => this.ambient.material(type) });
    this.views = this.ship ? builder.cabin(seat) : builder.earth(seat);
    this.lamps = builder.lamps; this.blinkers = builder.blinkers; this.alertLights = builder.alertLights; this.chase = builder.chase;
    if (builder.sunLight) this.sky = new Sky(this.scene, builder.sunLight);
    // Bloom: displays and lamps glow like they do on camera. Effects run "full" or "low"
    // (no bloom, no shadows, pixel ratio 1, slower ambient screens): ?fx=low|full forces
    // one; otherwise the last choice is remembered, and on a first visit the frame time
    // is measured for 3 s and a slow machine drops to low by itself (Phase 5).
    this.fxComposer = composer(this.renderer, this.scene, this.camera);
    const fxParam = new URLSearchParams(location.search).get('fx');
    let saved = null; try { saved = localStorage.getItem('lightlag.fx'); } catch { /* storage blocked */ }
    this.fxMode = fxParam === 'low' || fxParam === 'full' ? fxParam : saved === 'low' || saved === 'full' ? saved : null;
    this.fxSlowDebug = new URLSearchParams(location.search).get('fxdebug') === 'slow';
    this.setEffects(this.fxMode || 'full', false);
    if (!this.fxMode) this.measure = { start: null, last: null, dts: [] };
    // Sound starts with the first interaction (browsers block audio before one).
    const wake = () => { if (this.soundPref !== false && !this.sound.on) { this.sound.set(true); $('#room-sound') && ($('#room-sound').textContent = 'Sound on'); } };
    addEventListener('pointerdown', wake, { once: true }); addEventListener('keydown', wake, { once: true });
    if (builder.master) {
      this.master = builder.master;
      this.target(this.master.mesh, 'Master caution', 'master-caution', 0.1, 0.07, () => this.acknowledge());
    }
    this.camera.position.fromArray(this.views.home); this.look.fromArray(this.views.look); this.camera.lookAt(this.look);
    this.makeUI(); this.makeInput();
    document.body.classList.add('immersive-mode');
    this.resize = () => {
      this.renderer.setSize(innerWidth, innerHeight, false); this.fx?.setSize(innerWidth, innerHeight); this.camera.aspect = innerWidth / innerHeight; this.camera.updateProjectionMatrix();
      if (this.focus && !this.tween) this.layoutPanel();
    };
    addEventListener('resize', this.resize); this.resize();
    this.lastPaint = 0; this.lastFrame = 0;
    this.renderer.setAnimationLoop((t) => this.frame(t));
    fetch(roomUrl('/api/trajectory')).then((r) => r.json()).then((t) => { this.trajectory = t; }).catch(() => {});
    fetch('/api/catalog').then((r) => r.json()).then((c) => { this.catalog = c; this.paint(); }).catch(() => {});
    document.fonts.ready.then(() => this.paint());
    this.dashboardMode = new URLSearchParams(location.search).get('view') === 'dashboard';
    if (this.dashboardMode) this.setDashboard(true);
  }

  makeUI() {
    this.hudTitle = el('div', { class: 'room-location' }, el('a', { href: '/' }, 'Light-lag'),
      el('span', {}, this.ship ? 'Meridian / Flight deck' : 'Lantern / Mission operations'));
    this.clock = el('span', { class: 'room-clock' }, 'Connecting to mission');
    this.hud = el('header', { class: 'room-hud' }, this.hudTitle, this.clock,
      el('div', { class: 'room-hud-actions' },
        el('button', { onclick: () => this.home() }, 'My station'),
        el('button', { onclick: () => this.overview() }, this.ship ? 'Whole cabin' : 'Whole room'),
        el('button', { id: 'room-stations', onclick: () => this.menu.classList.toggle('hidden') }, 'All screens'),
        el('button', { id: 'room-sound', onclick: () => this.soundToggle() }, 'Sound off'),
        el('button', { id: 'room-fx', title: 'Full: bloom and shadows. Low: lighter on slow computers.', onclick: () => this.setEffects(this.fxLow ? 'full' : 'low', true) }, `Effects: ${this.fxLow ? 'low' : 'full'}`),
        roomChip('/')));
    this.hint = el('div', { class: 'room-hint' }, 'Click a screen to work · drag to look around');
    this.caption = el('div', { class: 'room-caption' }, this.ship ? 'Commander / live aboard' : 'Flight Director / received knowledge');
    this.root.append(this.hud, this.hint, this.caption);
    this.task = el('button', { class: 'room-task', onclick: () => this.open(this.taskPanel || 'design') }, 'Review the mission design on your workstation');
    // One bottom-left stack: the scene card on top, newest messages below it, the
    // task button last (ll.js puts cards and toasts here when it exists).
    this.stack = el('div', { class: 'room-stack' }, this.task);
    document.body.append(this.stack);      // outside the 3D root so the dashboard view keeps its toasts
    this.menu = el('nav', { class: 'room-station-menu hidden', 'aria-label': 'Workstation screens' },
      el('div', { class: 'room-menu-head' }, 'Choose an instrument', el('button', { onclick: () => this.menu.classList.add('hidden') }, 'Close')),
      ...Object.entries(this.panels).map(([key, p]) => el('button', { 'data-open-instrument': key, onclick: () => this.open(key) }, p[0])),
      el('button', { onclick: () => { this.menu.classList.add('hidden'); $('#help')?.click(); } }, 'Mission briefing'),
      el('button', { class: 'room-dashboard-link', onclick: () => this.setDashboard(true) }, 'Accessible dashboard view'));
    this.root.append(this.menu);
    this.panelTitle = el('span', {}, 'Instrument');
    this.panelBody = el('div', { class: 'instrument-body' });
    this.panel = el('section', { id: 'instrument-panel', class: 'instrument-panel hidden', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Workstation instrument', 'data-state': 'closed' },
      el('header', { class: 'instrument-chrome' }, el('button', { id: 'room-back', onclick: () => this.close() }, 'Back to my station'), this.panelTitle,
        el('span', { class: 'instrument-origin' }, 'Live mission interface')), this.panelBody);
    document.body.append(this.panel);
    this.dashboardReturn = el('button', { class: 'room-dashboard-return', onclick: () => this.setDashboard(false) }, 'Return to 3D station');
    document.body.append(this.dashboardReturn);
    this.record = el('div', { class: 'room-design-record' });
  }

  makeInput() {
    const ray = new THREE.Raycaster(), pointer = new THREE.Vector2();
    const hit = (e) => {
      pointer.set(e.clientX / innerWidth * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
      ray.setFromCamera(pointer, this.camera); return ray.intersectObjects(this.targets.map((t) => t.mesh), false)[0]?.object;
    };
    this.canvas.addEventListener('pointerdown', (e) => {
      if (this.focus) return;
      this.drag = { x: e.clientX, y: e.clientY, moved: false, look: this.look.clone() }; this.canvas.setPointerCapture(e.pointerId);
    });
    this.canvas.addEventListener('pointermove', (e) => {
      if (this.drag && !this.focus) {
        const dx = e.clientX - this.drag.x, dy = e.clientY - this.drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 6) this.drag.moved = true;
        if (this.drag.moved) {
          const v = this.drag.look.clone().sub(this.camera.position);
          const spherical = new THREE.Spherical().setFromVector3(v);
          spherical.theta -= dx * 0.0015; spherical.phi = Math.max(0.65, Math.min(2.1, spherical.phi + dy * 0.0015));
          this.look.copy(this.camera.position).add(new THREE.Vector3().setFromSpherical(spherical)); this.camera.lookAt(this.look);
        }
      } else {
        const object = hit(e), target = this.targets.find((t) => t.mesh === object);
        if (target && target.key !== this.hoverKey) this.sound.hover();
        this.hoverKey = target?.key;
        this.canvas.style.cursor = target ? 'pointer' : 'grab'; this.hint.textContent = target ? `${target.title} · click to operate` : 'Click a screen to work · drag to look around';
      }
    });
    this.canvas.addEventListener('pointerup', (e) => {
      if (this.drag && !this.drag.moved) {
        const object = hit(e), target = this.targets.find((t) => t.mesh === object); target?.run();
      }
      this.drag = null;
    });
    addEventListener('keydown', (e) => {
      // Let an opened letter, briefing or report own its keyboard interaction.
      if (document.querySelector('body > .scrim, body > dialog[open], body > .drawer.open')) return;
      if (e.key === 'Escape') { this.menu.classList.add('hidden'); if (this.focus) this.close(); }
      if (e.key === 'Tab' && this.focus && this.panel.dataset.state === 'open') {
        const items = [...this.panel.querySelectorAll('button, input, select, textarea, a[href], [tabindex="0"]')]
          .filter((node) => !node.disabled && node.getClientRects().length);
        const first = items[0], last = items.at(-1);
        if (e.shiftKey && (document.activeElement === first || !this.panel.contains(document.activeElement))) {
          e.preventDefault(); last?.focus();
        } else if (!e.shiftKey && (document.activeElement === last || !this.panel.contains(document.activeElement))) {
          e.preventDefault(); first?.focus();
        }
      }
    });
  }

  feed(kind, aspect = 1.6) {
    const id = `${kind}@${aspect.toFixed(2)}`;
    if (this.feeds.has(id)) return this.feeds.get(id);
    // Canvas follows the physical screen's shape so text is never stretched.
    const H = Math.min(480, Math.round(2048 / aspect)), W = Math.round(H * aspect);
    const canvas = document.createElement('canvas'); canvas.width = Math.max(320, W); canvas.height = H;
    const ctx = canvas.getContext('2d'); const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
    texture.minFilter = THREE.LinearFilter; texture.anisotropy = Math.min(4, this.renderer.capabilities.getMaxAnisotropy());
    const material = new THREE.MeshBasicMaterial({ map: texture, toneMapped: false });
    const feed = { kind, canvas, ctx, texture, material }; this.feeds.set(id, feed); this.drawFeed(feed); return feed;
  }

  screen(kind, title, w, h, parent, x, y, z, interactive) {
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), this.feed(kind, w / h).material);
    mesh.position.set(x, y, z); parent.add(mesh);
    if (interactive) this.target(mesh, title, kind, w, h, () => this.open(kind, mesh));
    return mesh;
  }

  target(mesh, title, key, w, h, run) {
    const button = el('button', { class: 'room-hotspot', 'aria-label': title, 'data-room-target': key, onclick: () => run() });
    button.addEventListener('focus', () => { this.hint.textContent = `${title} · Enter to operate`; });
    this.root.append(button); this.targets.push({ mesh, title, key, w, h, run, button });
  }

  control(key, title, mesh, pivot, { guard = null, lamp = null } = {}) {
    const ctrl = { key, title, mesh, pivot, guard, lamp }; this.controls.push(ctrl);
    const run = () => {
      if (key === 'radio') return this.open('radio');
      if (!this.state?.ship || this.state.phase !== 'mission') { this.hint.textContent = 'Vehicle controls become available after launch'; return; }
      const action = key === 'shelter' ? (this.state.ship.shelter ? 'shelter_exit' : 'shelter_enter') : key;
      this.action(action); this.sound.clunk(); this.hint.textContent = `${title} · command sent aboard`;
      if (pivot) { ctrl.pressedUntil = performance.now() + 180; ctrl.reachedUntil = performance.now() + 1400; }
    };
    this.target(mesh, title, `control-${key}`, mesh.geometry.parameters.width || 0.42, mesh.geometry.parameters.height || 0.25, run);
  }

  // Recessed analog gauge: matte bezel, dark face, needle on a hub.
  gauge(channel, title, x, y, z, parent, r = 0.23) {
    const group = new THREE.Group(); group.position.set(x, y, z); parent.add(group);
    const matte = new THREE.MeshStandardMaterial({ color: 0x111314, roughness: 0.6, metalness: 0.2 });
    const bezel = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.16, r * 1.2, r * 0.22, 40), matte);
    bezel.rotation.x = Math.PI / 2; bezel.position.z = -r * 0.04; group.add(bezel);
    const lip = new THREE.Mesh(new THREE.TorusGeometry(r * 1.06, r * 0.07, 10, 40), matte); lip.position.z = r * 0.07; group.add(lip);
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 256;
    const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace; texture.anisotropy = 4;
    const face = new THREE.Mesh(new THREE.CircleGeometry(r, 40), new THREE.MeshBasicMaterial({ map: texture, toneMapped: false })); face.position.z = r * 0.075; group.add(face);
    const pivot = new THREE.Group(); pivot.position.z = r * 0.1; group.add(pivot);
    const needle = new THREE.Mesh(new THREE.BoxGeometry(r * 0.035, r * 0.74, r * 0.02), new THREE.MeshBasicMaterial({ color: 0xf0e9da, toneMapped: false })); needle.position.y = r * 0.3; pivot.add(needle);
    const hub = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.09, r * 0.09, r * 0.06, 16), matte); hub.rotation.x = Math.PI / 2; hub.position.z = r * 0.12; group.add(hub);
    this.gauges.push({ channel, title, canvas, texture, pivot });
    this.target(face, title, `gauge-${channel}`, r * 2, r * 2, () => this.open(channel === 'dose' ? 'commander' : 'systems'));
  }

  acknowledge() {
    this.sound.press();
    if (!this.alarmSig) { this.hint.textContent = 'Master caution · no caution or warning active'; return; }
    this.ackSig = this.alarmSig; this.hint.textContent = 'Master caution acknowledged · open Engineering for the cause';
  }

  moveCamera(position, look, done) {
    const to = position.clone ? position.clone() : new THREE.Vector3().fromArray(position);
    const target = look.clone ? look.clone() : new THREE.Vector3().fromArray(look);
    if (this.reduced) { this.camera.position.copy(to); this.look.copy(target); this.camera.lookAt(target); done?.(); return; }
    this.tween = { time: performance.now(), start: this.camera.position.clone(), look: this.look.clone(), to, target, done };
  }

  home() { if (this.focus) this.close(false); this.menu.classList.add('hidden'); this.moveCamera(this.views.home, this.views.look); }
  overview() { if (this.focus) this.close(false); this.menu.classList.add('hidden'); this.moveCamera(this.views.overview, this.views.overviewLook); }

  open(kind, mesh) {
    if (!this.panels[kind]) return;
    if (this.focus) this.close(false);
    this.menu.classList.add('hidden'); this.sound.open(); this.ambient.burst = Math.max(this.ambient.burst, 0.4);
    this.focus = kind; this.focusMesh = mesh || this.targets.find((t) => t.key === kind)?.mesh;
    this.panel.dataset.kind = kind; this.panelTitle.textContent = this.panels[kind][0];
    this.panel.setAttribute('aria-label', this.panels[kind][0]); this.panel.dataset.state = 'approaching';
    this.panel.classList.add('hidden'); document.body.classList.add('instrument-focused');
    const show = () => {
      if (this.focus !== kind) return;
      this.mount(kind); this.layoutPanel(); this.panel.classList.remove('hidden'); this.panel.dataset.state = 'open';
      $('#room-back').focus(); dispatchEvent(new Event('resize'));
    };
    if (this.focusMesh && innerWidth > 760) {
      const center = new THREE.Vector3(); this.focusMesh.getWorldPosition(center);
      const normal = new THREE.Vector3(0, 0, 1).applyQuaternion(this.focusMesh.getWorldQuaternion(new THREE.Quaternion()));
      const w = this.focusMesh.geometry.parameters.width, h = this.focusMesh.geometry.parameters.height;
      const tangent = Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
      // The design bay is the densest instrument: let it fill most of the viewport.
      const fillH = kind === 'design' ? 0.93 : 0.76, fillW = kind === 'design' ? 0.97 : 0.84;
      const distance = Math.max(h / (2 * tangent * fillH), w / (2 * tangent * this.camera.aspect * fillW));
      this.moveCamera(center.clone().addScaledVector(normal, distance), center, show);
    } else show();
  }

  mount(kind) {
    if (kind !== 'design' && PRE_FLIGHT.has(this.state?.phase)) {
      this.panelBody.append(el('div', { class: 'room-design-record' }, el('h2', {}, this.panels[kind][0]),
        el('p', { class: 'haze' }, 'This station is standing by for launch. The team is reviewing the mission design.'),
        el('button', { class: 'primary', onclick: () => this.open('design') }, 'Open mission design')));
      return;
    }
    const tabs = { science: 'science', crew: 'crew', autonomy: 'rules', assistant: 'fido', tern: 'tern', systems: 'systems', radio: this.ship ? 'lantern' : null };
    if (tabs[kind]) $(`[data-tab="${tabs[kind]}"]`)?.click();
    let sources;
    if (kind === 'design') sources = PRE_FLIGHT.has(this.state?.phase) ? [$('#design')] : [this.record];
    else sources = this.panels[kind].slice(1).filter(Boolean).map((selector) => $(selector));
    for (const node of sources.filter(Boolean)) {
      const marker = document.createComment('room instrument home'); if (node.parentNode) node.before(marker);
      this.moved.push({ node, marker, hidden: node.classList.contains('hidden') });
      node.classList.add('room-mounted'); node.classList.remove('hidden'); this.panelBody.append(node);
    }
    this.renderRecord();
  }

  close(returnHome = true) {
    const last = this.focus; this.focus = null; this.tween = null; if (last) this.sound.close();
    this.panel.classList.add('hidden'); this.panel.dataset.state = 'closed';
    for (const { node, marker, hidden } of this.moved) {
      node.classList.remove('room-mounted'); if (marker.parentNode) marker.replaceWith(node); else node.remove();
      node.classList.toggle('hidden', node.id === 'design' ? true : hidden);
    }
    this.moved = []; this.panelBody.replaceChildren(); document.body.classList.remove('instrument-focused');
    if (returnHome) this.moveCamera(this.views.home, this.views.look);
    this.targets.find((t) => t.key === last)?.button.focus({ preventScroll: true });
  }

  layoutPanel() {
    // Camera completion runs before the renderer's matrix update. Project the
    // final pose now so the HTML plane meets the physical screen exactly.
    this.scene.updateMatrixWorld(); this.camera.updateMatrixWorld();
    if (!this.focusMesh || innerWidth <= 760) {
      Object.assign(this.panel.style, { left: '4vw', top: '8vh', width: '92vw', height: '84vh' }); return;
    }
    const box = this.project(this.focusMesh, this.focusMesh.geometry.parameters.width, this.focusMesh.geometry.parameters.height);
    Object.assign(this.panel.style, { left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px` });
  }

  project(mesh, w, h) {
    const points = [[-w / 2, h / 2], [w / 2, h / 2], [w / 2, -h / 2], [-w / 2, -h / 2]].map(([x, y]) => {
      const p = mesh.localToWorld(new THREE.Vector3(x, y, 0)).project(this.camera);
      return { x: (p.x / 2 + 0.5) * innerWidth, y: (-p.y / 2 + 0.5) * innerHeight, z: p.z };
    });
    const x = Math.min(...points.map((p) => p.x)), y = Math.min(...points.map((p) => p.y));
    return { x, y, w: Math.max(...points.map((p) => p.x)) - x, h: Math.max(...points.map((p) => p.y)) - y, points };
  }

  setDashboard(on) {
    if (this.focus) this.close(false);
    this.dashboardMode = on; document.body.classList.toggle('dashboard-mode', on); document.body.classList.toggle('immersive-mode', !on);
    if (PRE_FLIGHT.has(this.state?.phase)) $('#design').classList.toggle('hidden', !on);
    dispatchEvent(new Event('resize'));
  }

  // Activity: displays surge, buttons chase, and the room answers with sound.
  react(kind) {
    const now = performance.now(); this.ambient.burst = 1; this.chaseUntil = now + 1400;
    if (kind === 'command') this.sound.chatter();
    else if (kind === 'uplink') { this.sound.quindar(true); this.sound.chatter(); }
    else if (kind === 'downlink') { this.sound.quindar(false); this.sound.beep(1900, 0.08, 0.02); }
    else if (kind === 'alarm') { this.sound.alarm(); this.ambient.alert = 1; }
    else if (kind === 'alert') { this.sound.alarm(); this.ambient.alert = 1; }
    else if (kind === 'phase') { this.sound.rumble(); this.sound.chatter(); }
  }

  update(s) {
    const p = this.state;
    if (p) {
      if (p.phase !== s.phase) this.react('phase');
      if ((s.comms?.in_flight?.length || 0) > (p.comms?.in_flight?.length || 0)) this.react('uplink');
      const inbox = (x) => (this.ship ? x.ship?.inbox : x.control?.inbox)?.length || 0;
      if (inbox(s) > inbox(p)) this.react('downlink');
      if ((s.ship?.alarms?.length || 0) > (p.ship?.alarms?.length || 0)) this.react('alarm');
      if ((s.control?.space_weather?.length || 0) > (p.control?.space_weather?.length || 0)) this.react('alert');
      if (s.ship?.shelter !== p.ship?.shelter && p.ship) this.react('command');
    }
    if (this.state?.phase !== s.phase && this.focus) this.close();
    this.state = s; this.receivedAt = performance.now();
    if (s.design) this.design = s.design;
    this.clock.textContent = this.burnState ? `${this.burnState.title} · ${this.burnState.phase === 'burn' ? 'T-' + this.burnState.n : 'cutoff'}`
      : `${s.utc.replace('T', ' ').replace('Z', ' UTC')} · ${PRE_FLIGHT.has(s.phase) ? 'Design review' : 'T+ ' + num(s.geometry?.mission_day, 1) + ' d'}`;
    const age = s.control?.age_s;
    this.caption.textContent = `${s.seats?.[this.seat]?.title || this.seat} / ${this.ship ? 'Live aboard' : age == null ? 'Awaiting telemetry' : 'as of ' + dur(age) + ' ago'} · MODEL interior`;
    this.paint(); this.renderRecord();
    this.guideToDesign(s);
    // Phase 5 mood, from the snapshot only: the conjunction silence and the flood.
    const dark = !!(s.comms?.link?.blackout || s.geometry?.blackout);
    const floodN = (s.ship?.letters || []).filter((L) => L.flood).length;
    if (this.ship && p && floodN > (this.floodN ?? floodN) && !dark) this.warmUntil = performance.now() + 45000;
    this.floodN = floodN; this.dark = dark; this.sound.quiet?.(this.ship && dark);
    queueMicrotask(() => {
      if (!this.dashboardMode && PRE_FLIGHT.has(s.phase) && this.focus !== 'design') $('#design').classList.add('hidden');
      const objective = (s.director?.objectives || []).find((o) => !o.done && (o.side === (this.ship ? 'ship' : 'control') || o.side === 'both'));
      const panels = { comm_check: 'radio', storm_warning: 'radio', family_answered: 'radio', control_letter: 'radio', readback: 'radio',
        hga_point: 'systems', pump_fixed: 'systems', scrubber_fixed: 'systems', shelter: 'commander', letters_played: 'commander',
        policy_signed: this.ship ? 'tern' : 'autonomy', answer_lila: 'commander', explain_lila: 'commander', goodnight_live: 'commander' };
      this.taskPanel = PRE_FLIGHT.has(s.phase) ? 'design' : panels[objective?.id] || (this.ship ? 'systems' : 'radio');
      this.task.textContent = PRE_FLIGHT.has(s.phase) ? 'Pre-flight · review the mission design on your workstation' : $('#coach .what')?.textContent || 'Mission instruments ready';
    });
  }

  // First-time path: before launch the only job is the design, so outline that
  // screen (steady, no pulse), say so, and after a short establishing look open it (at once under
  // reduced motion). Only once per page, and never if the player already moved.
  guideToDesign(s) {
    const pre = PRE_FLIGHT.has(s.phase) && !this.dashboardMode;
    // Earth has a Mission design monitor; the cockpit opens the design from its task button.
    const t = this.targets.find((x) => x.key === 'design');
    (t ? t.button : this.task).classList.toggle(t ? 'room-hotspot-cue' : 'room-task-cue', pre && this.focus !== 'design');
    if (!pre && /to start$/.test(this.hint.textContent)) this.hint.textContent = 'Click a screen to work · drag to look around';
    if (!pre || this.designGuided) return;
    this.designGuided = true;
    if (!this.focus) this.hint.textContent = t ? 'Click the Mission design screen to start' : 'Click the highlighted mission design task to start';
    let touched = false;
    const mark = () => { touched = true; };
    addEventListener('pointerdown', mark, { once: true }); addEventListener('keydown', mark, { once: true });
    setTimeout(() => {
      removeEventListener('pointerdown', mark); removeEventListener('keydown', mark);
      if (!touched && !this.focus && !this.dashboardMode && PRE_FLIGHT.has(this.state?.phase)) this.open('design', t?.mesh);
    }, this.reduced ? 0 : 1500);
  }

  renderRecord() {
    if (this.focus !== 'design' || PRE_FLIGHT.has(this.state?.phase)) return;
    const d = this.state?.vehicle_design; if (!d) { this.record.textContent = 'Flight configuration unavailable'; return; }
    const name = (list, id) => this.catalog?.[list]?.find((x) => x.id === id)?.name || id;
    this.record.replaceChildren(el('h2', {}, 'As-flown configuration'), el('p', { class: 'dim' }, 'MODEL · design locked at launch'),
      el('table', { class: 'kv' }, ...[
        ['Crew', d.crew], ['Net habitable volume', `${num(d.habitat_m3, 0)} m³`], ['Launch vehicle', name('vehicles', d.vehicle)],
        ['Solar array', name('solar_arrays', d.array)], ['Battery', name('batteries', d.battery)], ['Radio allocation', d.comms_plan],
        ['Shelter', `${d.shelter_gcm2} g/cm²`], ['Hull liner', `${d.hull_liner_gcm2} g/cm²`],
        ['Instruments', (d.payload || []).map((id) => name('instruments', id)).join(', ')],
      ].map(([k, v]) => el('tr', {}, el('td', {}, k), el('td', { class: 'v' }, String(v ?? 'Unavailable'))))));
  }

  // Screen textures are painted from the same role snapshot as the HTML panel.
  drawFeed(f) {
    const g = f.ctx, W = f.canvas.width, H = f.canvas.height, s = this.state, c = s?.control, sh = s?.ship;
    if (f.kind === 'clock') { this.drawClocks(f); f.texture.needsUpdate = true; return; }
    if (f.kind === 'window') {
      g.fillStyle = '#010203'; g.fillRect(0, 0, W, H);
      if (s?.geometry?.viewport) this.optical(g, s, W, H, true);
      f.texture.needsUpdate = true; return;
    }
    const titles = { trajectory: 'Flight dynamics', dsn: 'Deep Space Network', weather: 'Space weather / replay', telemetry: 'Vehicle systems', radio: 'Communications', science: 'Science return', crew: 'Crew operations', autonomy: 'Autonomy / policy', design: 'Mission design', systems: 'Engineering', commander: 'Crew / radiation', tern: 'TERN / mission computer', optical: 'External optical view' };
    g.fillStyle = BG; g.fillRect(0, 0, W, H); g.fillStyle = META; g.font = '25px B612'; g.fillText(titles[f.kind] || f.kind, 28, 43);
    g.fillStyle = ACCENT; g.fillRect(28, 62, 36, 3);
    g.strokeStyle = LINE; g.lineWidth = 2; g.beginPath(); g.moveTo(70, 63.5); g.lineTo(W - 28, 63.5); g.stroke(); g.lineWidth = 1;
    const text = (t, y, color = INK, size = 25) => { g.fillStyle = color; g.font = `${size}px B612`; g.fillText(String(t), 30, y, W - 60); };
    const row = (label, value, y, color = INK) => {
      g.fillStyle = META; g.font = '22px B612'; let caption = String(label);
      while (g.measureText(caption).width > W * 0.53 && caption.length > 4) caption = caption.slice(0, -2).trimEnd() + '…';
      g.fillText(caption, 30, y); g.fillStyle = color; g.font = '27px "B612 Mono"'; g.textAlign = 'right'; g.fillText(String(value), W - 32, y, W * 0.4); g.textAlign = 'left';
      g.strokeStyle = LINE; g.beginPath(); g.moveTo(30, y + 14); g.lineTo(W - 30, y + 14); g.stroke();
    };
    const foot = (message) => { g.fillStyle = META; g.font = '17px "B612 Mono"'; g.fillText(message, 30, H - 24, W - 60); };
    if (!s) { text('Awaiting mission data', 130, META); f.texture.needsUpdate = true; return; }
    if (this.burnState && this.drawBurn(f, g, W, H)) { f.texture.needsUpdate = true; return; }   // Phase 6: the TMI burn
    const t = this.ship ? sh?.telemetry : c?.telemetry;
    if (f.kind === 'design') {
      const d = s.design || this.design;
      text(PRE_FLIGHT.has(s.phase) ? 'PRE-FLIGHT REVIEW' : 'AS-FLOWN CONFIGURATION', 120, ACCENT, 29);
      if (d) { row('Mass / allocation', `${num(d.mass?.imleo_t, 1)} / ${num(d.mass?.budget_t, 1)} t`, 184); row('Power margin', `${num(d.budgets?.power?.margin * 100, 0)}%`, 242); row('Design state', !PRE_FLIGHT.has(s.phase) ? 'Configuration locked' : d.valid ? 'Ready for GO votes' : 'Review required', 300, d.valid ? '#a9c9a0' : '#e6c18a'); }
      else { const v = s.vehicle_design; row('Crew', v?.crew ?? '–', 190); row('Array', v?.array ?? '–', 250); row('Radio plan', v?.comms_plan ?? '–', 310); }
      foot(PRE_FLIGHT.has(s.phase) ? 'SELECT THIS SCREEN TO DESIGN / MODEL' : 'DESIGN LOCKED AT LAUNCH / MODEL');
    } else if (!s.geometry) {
      text('DESIGN REVIEW IN PROGRESS', 140, ACCENT, 27); text('Mission instruments available after launch', 204, META, 22);
      if (f.kind === 'trajectory') text('Earth → Mars / 2020 window', 298, INK, 29);
      foot('STANDBY / NO MISSION TELEMETRY');
    } else if (f.kind === 'trajectory') {
      if (!this.plotFeed) { this.plotFeed = el('canvas', { class: 'room-feed-canvas', 'aria-hidden': 'true' }); this.root.append(this.plotFeed); this.plotFeed.getContext('2d', { alpha: false }); }
      drawOrrery(this.plotFeed, s, this.trajectory, this.ship ? 'ship' : 'control', performance.now(), this.receivedAt);
      const dw = Math.min(W, 768 * 1.12), dh = dw * 360 / 768, dx = (W - dw) / 2;
      g.drawImage(this.plotFeed, dx, 70, dw, Math.min(dh, H - 110));
      if (!this.ship && s.comms?.link?.blackout) {
        g.fillStyle = 'rgba(6,8,10,0.82)'; g.fillRect(0, H / 2 - 70, W, 140); g.textAlign = 'center';
        g.fillStyle = '#ef9a8c'; g.font = '52px "B612 Mono"'; g.fillText('NO SIGNAL', W / 2, H / 2 - 6);
        g.fillStyle = META; g.font = '26px B612'; g.fillText('SOLAR CONJUNCTION', W / 2, H / 2 + 40); g.textAlign = 'left';
      }
      if (W > 1000) {
        const G = s.geometry, side = (x, items) => items.forEach(([label, value], i) => {
          g.textAlign = x > W / 2 ? 'right' : 'left'; g.fillStyle = META; g.font = '20px B612'; g.fillText(label, x, 128 + i * 82);
          g.fillStyle = INK; g.font = '30px "B612 Mono"'; g.fillText(value, x, 162 + i * 82); g.textAlign = 'left';
        });
        side(30, [['One-way light time', dur(G.lt_up_s)], ['Earth to ship', `${num(G.dist_km / 1e6, 1)} million km`], ['Mission day', num(G.mission_day, 1)]]);
        side(W - 30, [['Ship to Sun', `${num(G.r_ship_au, 3)} AU`], ['Sun–Earth–ship angle', `${num(G.sep_deg, 1)}°`], ['Signals in flight', String(s.comms?.in_flight?.length ?? '–')]]);
      }
      foot(`OWLT ${dur(s.geometry.lt_up_s)} / ${s.ephemeris?.label || 'EPHEMERIS'} / MODEL TRANSFER`);
    } else if (f.kind === 'dsn') {
      const L = s.comms.link || {}; text(L.blackout ? 'SOLAR CONJUNCTION' : L.pass_now || 'NO SCHEDULED CONTACT', 126, L.blackout ? '#ef9a8c' : ACCENT, 32);
      row('Uplink', L.uplink_open == null ? 'Unknown' : L.uplink_open ? 'Available' : 'Held', 196); row('Downlink', L.downlink_open == null ? 'Unknown' : L.downlink_open ? 'Available' : 'Held', 252);
      row('Data rate', L.data_rate_bps == null ? '–' : `${num(L.data_rate_bps / 1000, 1)} kbit/s`, 308); row('One-way light time', dur(s.geometry.lt_up_s), 364);
      foot('CONTACT PLAN / MODEL');
    } else if (f.kind === 'weather') {
      const events = c?.space_weather || [];
      if (!events.length) { text('No events received', 138, META, 30); text('Historical solar-event replay', 211, INK, 23); }
      events.slice(-3).reverse().forEach((event, i) => { text(etIso(event.et) + ' UTC', 116 + i * 94, META, 19); text(event.text, 154 + i * 94, INK, 23); });
      const real = s.reality?.shown?.at(-1);       // Phase 6: "Meanwhile, in reality", never scored
      if (real) {
        g.font = '22px B612'; const lines = [], words = real.text.split(' ');
        for (const w of words) { const L = lines.at(-1); if (L != null && g.measureText(L + ' ' + w).width < W - 64) lines[lines.length - 1] = L + ' ' + w; else lines.push(w); }
        const n = Math.min(3, lines.length), top = H - 74 - 30 * n;
        g.fillStyle = 'rgba(11,13,14,0.94)'; g.fillRect(0, top - 34, W, 34 + 30 * n + 10);
        text('Meanwhile, in reality · ' + real.date_label, top - 8, ACCENT, 19);
        lines.slice(0, n).forEach((L, i) => text(L, top + 26 + i * 30, INK, 22));
      }
      foot('EARTH OBSERVATIONS / MODEL REPLAY');
    } else if (f.kind === 'telemetry' || f.kind === 'systems') {
      row('Battery', `${num(t?.batt_soc, 0)} %`, 129); row('Solar array', `${num(t?.solar_kw, 1)} kW`, 193); row('Main bus', `${num(t?.bus_v, 1)} V`, 257); row('Cabin CO₂', `${num(t?.ppco2, 2)} mmHg`, 321); row('Coolant A / B', `${num(t?.loop_a_c, 1)} / ${num(t?.loop_b_c, 1)} °C`, 385);
      foot(this.ship ? 'LOCAL SENSORS / LIVE / MODEL' : c?.age_s == null ? 'NO TELEMETRY RECEIVED' : `AS OF ${dur(c.age_s)} AGO / MODEL`);
    } else if (f.kind === 'radio') {
      text(s.comms.link?.blackout ? 'LINK BLOCKED / BUNDLES HELD' : 'STORE AND FORWARD', 128, s.comms.link?.blackout ? '#ef9a8c' : ACCENT, 30);
      row('One-way light time', dur(s.geometry.lt_up_s), 204); row('Queued locally', s.comms.queue_counts?.[this.ship ? 'ship' : 'control'] ?? '–', 270); row('Bundles in flight', s.comms.in_flight?.length ?? '–', 336);
      foot('SELECT TO COMPOSE / ALL MESSAGES CROSS THE GAP');
    } else if (f.kind === 'science') {
      const summary = s.science?.as_of?.summary, e = s.science?.earth;
      text(s.science?.objective?.name || 'Science return', 122, ACCENT, 27);
      row('Generated aboard', `${num(summary?.gen, 2)} Gbit`, 197); row('Received on Earth', `${num(e?.delivered_gbit, 2)} Gbit`, 263); row('On recorder', `${num(summary?.rec, 2)} Gbit`, 329);
      foot(s.science?.as_of?.age_s == null ? 'NO SCIENCE TELEMETRY RECEIVED' : `ABOARD AS OF ${dur(s.science.as_of.age_s)} AGO / MODEL`);
    } else if (f.kind === 'commander') {
      row('Cabin dose rate', `${num(sh?.telemetry?.dose_rate, 1)} µSv/h`, 135); row('Cumulative dose', `${num(sh?.dose?.total_msv, 1)} mSv`, 208); row('Shelter', sh?.shelter ? 'Occupied' : 'Not occupied', 281); row('Crew aboard', s.crew?.members?.length ?? '–', 354); foot('CREW SAFETY / LIVE ABOARD / MODEL');
    } else if (f.kind === 'crew') {
      const members = this.ship ? s.crew?.members : Object.entries(s.crew?.names || {}).map(([id, name]) => ({ name: name.name, task: { label: s.crew?.as_of?.summary?.[id]?.task || 'No report' } }));
      (members || []).slice(0, 4).forEach((m, i) => row(m.name, m.task?.label || 'Off duty', 133 + i * 69));
      foot(this.ship ? 'CREW / LIVE ABOARD / MODEL' : s.crew?.as_of?.age_s == null ? 'NO CREW TELEMETRY RECEIVED' : `AS OF ${dur(s.crew.as_of.age_s)} AGO / MODEL`);
    } else if (f.kind === 'autonomy' || f.kind === 'tern') {
      const p = this.ship ? sh?.policy : c?.policy;
      text(this.ship ? 'TERN / ONBOARD AUTONOMY' : 'AUTONOMY / POLICY REGISTER', 122, ACCENT, 28);
      (p?.rules || []).slice(0, 4).forEach((rule, i) => row(rule.label, rule.permission, 190 + i * 57)); foot(this.ship ? 'LOCAL RULES / LIVE / MODEL' : 'GROUND POLICY REGISTER / MODEL');
    } else if (f.kind === 'optical') {
      this.optical(g, s, W, H); foot('EARTH-FACING PORT / EPHEMERIS GEOMETRY');
    }
    f.texture.needsUpdate = true;
  }

  // Front-wall clock strip: every value from the role snapshot.
  drawClocks(f) {
    const g = f.ctx, W = f.canvas.width, H = f.canvas.height, s = this.state;
    g.fillStyle = '#050606'; g.fillRect(0, 0, W, H);
    const cells = s ? [
      ['UTC', s.utc.slice(0, 10) + '  ' + s.utc.slice(11, 19)],
      ['Mission elapsed', PRE_FLIGHT.has(s.phase) ? 'Pre-flight' : `T+ ${num(s.geometry?.mission_day, 2)} d`],
      ['One-way light time', s.geometry ? dur(s.geometry.lt_up_s) : '–'],
      ['Telemetry age', s.control?.age_s == null ? 'No telemetry' : dur(s.control.age_s)],
    ] : [['Mission clocks', 'Connecting']];
    const cw = W / cells.length;
    cells.forEach(([label, value], i) => {
      const x = i * cw + 24;
      g.fillStyle = '#7d8285'; g.font = `${Math.round(H * 0.3)}px B612`; g.textBaseline = 'middle'; g.fillText(label, x, H * 0.5, cw * 0.36);
      g.fillStyle = i === 3 ? ACCENT : INK; g.font = `${Math.round(H * 0.5)}px "B612 Mono"`; g.fillText(value, x + cw * 0.38, H * 0.52, cw * 0.58);
      if (i) { g.fillStyle = '#1d2022'; g.fillRect(i * cw, H * 0.15, 2, H * 0.7); }
    });
    g.textBaseline = 'alphabetic';
  }

  optical(g, s, W, H, bare = false) {
    if (bare) { // the cabin window: no text, the whole canvas is sky
      const vp = s.geometry.viewport, forward = new THREE.Vector3().fromArray(vp.earth.dir).normalize();
      const up = new THREE.Vector3(0, 0, 1), right = new THREE.Vector3().crossVectors(forward, up).normalize(); up.crossVectors(right, forward).normalize();
      const tangent = Math.tan(THREE.MathUtils.degToRad(46 / 2));
      for (const [key, color] of [['sun', '#fff6e2'], ['mars', '#c98d64'], ['earth', '#a9c6dc']]) {
        const body = vp[key], v = new THREE.Vector3().fromArray(body.dir), depth = v.dot(forward); if (depth <= 0) continue;
        const x = W / 2 + v.dot(right) / depth / tangent * W / 2, y = H / 2 - v.dot(up) / depth / tangent * H / 2;
        if (x < -40 || x > W + 40 || y < -40 || y > H + 40) continue;
        const radius = Math.max(1.6, Math.tan(THREE.MathUtils.degToRad(body.ang_diam_deg / 2)) / tangent * W / 2);
        g.fillStyle = color; g.beginPath(); g.arc(x, y, radius, 0, Math.PI * 2); g.fill();
      }
      return;
    }
    g.fillStyle = '#020509'; g.fillRect(12, 78, W - 24, H - 120);
    const vp = s.geometry.viewport, forward = new THREE.Vector3().fromArray(vp.earth.dir).normalize();
    const up = new THREE.Vector3(0, 0, 1), right = new THREE.Vector3().crossVectors(forward, up).normalize(); up.crossVectors(right, forward).normalize();
    const tangent = Math.tan(THREE.MathUtils.degToRad(38 / 2));
    for (const [key, color] of [['earth', '#b4c5cd'], ['mars', '#c4b19e'], ['sun', '#f2e5c9']]) {
      const body = vp[key], v = new THREE.Vector3().fromArray(body.dir), depth = v.dot(forward); if (depth <= 0) continue;
      const x = W / 2 + v.dot(right) / depth / tangent * (H - 120) / 2, y = H / 2 - v.dot(up) / depth / tangent * (H - 120) / 2;
      if (x < 15 || x > W - 15 || y < 85 || y > H - 50) continue;
      const radius = Math.max(1, Math.tan(THREE.MathUtils.degToRad(body.ang_diam_deg / 2)) / tangent * (H - 120) / 2);
      g.fillStyle = color; g.beginPath(); g.arc(x, y, radius, 0, Math.PI * 2); g.fill();
    }
  }

  paint() {
    for (const feed of this.feeds.values()) this.drawFeed(feed);
    const s = this.state, t = s?.ship?.telemetry;
    for (const gauge of this.gauges) {
      const ctx = gauge.canvas.getContext('2d'), ch = gauge.channel;
      const limit = ch === 'batt_soc' ? 100 : ch === 'dose' ? s?.ship?.dose?.career_limit_msv : this.meta?.limits?.[ch]?.emergency;
      const value = ch === 'dose' ? s?.ship?.dose?.total_msv : t?.[ch];
      ctx.fillStyle = '#0e1011'; ctx.fillRect(0, 0, 256, 256);
      ctx.fillStyle = '#9a9fa2'; ctx.font = '17px B612'; ctx.textAlign = 'center'; ctx.fillText(gauge.title, 128, 186, 196);
      const lim = this.meta?.limits?.[ch];
      if (limit != null && lim) { // red line: from the warning threshold to the scale end
        const low = ch === 'batt_soc', from = low ? 0 : lim.warning / limit, to = low ? lim.warning / limit : 1;
        ctx.strokeStyle = '#c8473c'; ctx.lineWidth = 7; ctx.beginPath();
        for (let k = 0; k <= 20; k++) { const fr = from + (to - from) * k / 20, a = Math.PI * 1.5 * (0.5 - fr), px = 128 - Math.sin(a) * 104, py = 122 - Math.cos(a) * 104; k ? ctx.lineTo(px, py) : ctx.moveTo(px, py); }
        ctx.stroke(); ctx.lineWidth = 1;
      }
      if (limit != null) for (let i = 0; i <= 10; i++) {
        const a = Math.PI * 1.5 * (0.5 - i / 10), x = -Math.sin(a), y = -Math.cos(a);
        ctx.strokeStyle = '#d9d6cc'; ctx.lineWidth = i % 5 === 0 ? 3 : 1; ctx.beginPath(); ctx.moveTo(128 + x * 89, 122 + y * 89); ctx.lineTo(128 + x * 101, 122 + y * 101); ctx.stroke();
        if (i % 5 === 0) { ctx.font = '16px "B612 Mono"'; ctx.fillText(num(limit * i / 10, 0), 128 + x * 72, 128 + y * 72); }
      }
      ctx.fillStyle = '#ebe8e1'; ctx.font = '26px "B612 Mono"'; ctx.fillText(num(value, ch === 'ppco2' ? 2 : ch === 'batt_soc' ? 0 : 1), 128, 218);
      const target = this.targets.find((item) => item.key === `gauge-${ch}`);
      if (target) { const reading = num(value, ch === 'ppco2' ? 2 : ch === 'batt_soc' ? 0 : 1); target.button.dataset.reading = reading; target.button.setAttribute('aria-label', `${gauge.title}: ${reading}`); }
      gauge.pivot.visible = value != null && limit != null;
      if (gauge.pivot.visible) gauge.pivot.rotation.z = Math.PI * 1.5 * (0.5 - Math.min(1, Math.max(0, value / limit)));
      gauge.texture.needsUpdate = true;
    }
    this.paintLamps();
  }

  // Annunciators and master caution follow the ship's own alarm list.
  paintLamps() {
    if (!this.lamps?.length && !this.master) return;
    const sh = this.state?.ship, alarms = sh?.alarms || [];
    const colour = (level) => level === 'caution' ? 0xd99a2b : 0xd8473b;
    for (const lamp of this.lamps || []) {
      let c = 0x202325;
      if (lamp.channel === 'shelter') c = sh?.shelter ? 0xd9d4c6 : c;
      else { const a = alarms.find((x) => x.channel === lamp.channel); if (a) c = colour(a.level); }
      lamp.mesh.material.color.setHex(c);
    }
    this.alarmSig = alarms.map((a) => a.channel + a.level).sort().join('|');
    if (this.ackSig && this.ackSig !== this.alarmSig) this.ackSig = null;
    this.masterLit = !!this.alarmSig && this.ackSig !== this.alarmSig;
    this.masterSevere = alarms.some((a) => a.level !== 'caution');
  }

  // Effects: "full" (bloom, shadows, sharp) or "low" (none of those, slower ambient redraws).
  setEffects(mode, remember) {
    const low = mode === 'low';
    this.fxLow = low;
    this.fx = low ? null : this.fxComposer;
    this.renderer.setPixelRatio(low ? 1 : Math.min(1.5, devicePixelRatio || 1));
    if (this.renderer.shadowMap.enabled === low) {
      this.renderer.shadowMap.enabled = !low;
      this.scene.traverse((o) => { if (o.material) [].concat(o.material).forEach((m) => { m.needsUpdate = true; }); });
    }
    this.ambient.every = low ? 400 : 80;
    if (remember) { try { localStorage.setItem('lightlag.fx', mode); } catch { /* storage blocked */ } this.measure = null; }
    const b = $('#room-fx'); if (b) b.textContent = `Effects: ${low ? 'low' : 'full'}`;
    this.resize?.();
  }

  // First visit: watch the real frame interval for 3 s once the room is on screen.
  measureFrame(now) {
    const m = this.measure;
    if (!m || document.hidden || this.dashboardMode) return;
    if (this.fxSlowDebug) { const t = performance.now(); while (performance.now() - t < 32) { /* simulate a slow GPU */ } }
    if (m.last != null) m.dts.push(now - m.last);
    m.last = now; m.start = m.start ?? now;
    if (now - m.start < 3000) return;
    const d = m.dts.sort((a, b) => a - b), median = d[Math.floor(d.length / 2)] || 0;
    this.measure = null;
    this.fxMeasured = Math.round(median);
    if (median > 28) {
      this.setEffects('low', true);
      import('./ll.js').then(({ toast }) => toast(`This computer draws the room slowly (${Math.round(median)} ms a frame), so effects are set to low. Change it under Effects.`, 'info', 7000));
    } else { try { localStorage.setItem('lightlag.fx', 'full'); } catch { /* storage blocked */ } }
  }

  soundToggle() {
    this.sound.set(!this.sound.on); this.soundPref = this.sound.on;
    $('#room-sound').textContent = this.sound.on ? 'Sound on' : 'Sound off'; $('#audio')?.click();
  }

  clickSound() { this.sound.press(); }

  // Lamps, chases, blinkers and alert light: animated every frame.
  animateLights(now) {
    const hash = (i, t) => { const x = Math.sin(i * 91.7 + t * 12.3) * 43758.5; return x - Math.floor(x); };
    if (this.master) {
      const blink = this.masterLit && Math.floor(now / 450) % 2 === 0;
      this.master.mesh.material.color.setHex(this.masterLit ? (blink ? (this.masterSevere ? 0xff4a3a : 0xf0a030) : 0x5a1a14) : 0x3a1210);
    }
    const chasing = this.chaseUntil > now, step = Math.floor(now / 80);
    (this.chase || []).forEach((lamp, i) => {
      const idle = [0, 3, 5].includes(i) ? 0x3f8a52 : 0x182024;
      lamp.material.color.setHex(chasing ? (step % this.chase.length === i ? 0xffc070 : idle) : idle);
    });
    const busy = this.ambient.burst > 0.05, bucket = Math.floor(now / (busy ? 90 : 1600));
    (this.blinkers || []).forEach((b, i) => {
      const r = hash(i, bucket);
      b.mesh.material.color.setHex(busy ? (r > 0.66 ? b.alt : r > 0.33 ? b.on : 0x1b1d1f) : (r > 0.97 ? b.alt : b.on));
    });
    const alarming = this.ship ? this.masterLit : this.ambient.alert > 0.02;
    for (const light of this.alertLights || []) {
      const target = alarming ? (this.ship ? 1.6 : 9 * this.ambient.alert) * (0.55 + 0.45 * Math.sin(now / 380)) : 0;
      light.intensity += (target - light.intensity) * 0.15;
    }
  }

  frame(now) {
    this.measureFrame(now);
    if (document.hidden || this.dashboardMode || now - this.lastFrame < 32) return;
    this.lastFrame = now;
    if (this.tween) {
      const t = this.tween, f = Math.min(1, (now - t.time) / 720), ease = 1 - (1 - f) ** 3;
      this.camera.position.lerpVectors(t.start, t.to, ease); this.look.lerpVectors(t.look, t.target, ease); this.camera.lookAt(this.look);
      if (f === 1) { this.tween = null; t.done?.(); }
    }
    for (const c of this.controls) if (c.pivot) {
      const modes = this.state?.ship?.modes;
      const state = c.key === 'science' ? modes?.science_on : c.key === 'pump_switch' ? modes?.active_loop === 'B' : c.key === 'shelter' ? this.state?.ship?.shelter : false;
      c.pivot.rotation.x = c.pressedUntil > now ? -0.3 : state ? -0.42 : 0.35;
      if (c.lamp) c.lamp.material.color.setHex(state ? (c.key === 'pump_switch' ? 0xd9d4c6 : 0x79c57f) : 0x1b1d1f);
      if (c.guard) { // the guard lifts as you reach for it and stays up while the switch is on
        const open = state || c.reachedUntil > now || this.hoverKey === `control-${c.key}` || document.activeElement?.dataset?.roomTarget === `control-${c.key}`;
        c.guard.rotation.x += ((open ? 1.75 : 0) - c.guard.rotation.x) * (this.reduced ? 1 : 0.3);
      }
    }
    this.scene.updateMatrixWorld(); this.camera.updateMatrixWorld();
    for (const target of this.targets) {
      const b = this.project(target.mesh, target.w, target.h);
      const center = target.mesh.getWorldPosition(new THREE.Vector3()), normal = new THREE.Vector3(0, 0, 1).applyQuaternion(target.mesh.getWorldQuaternion(new THREE.Quaternion()));
      const visible = !this.focus && b.points.every((p) => p.z > -1 && p.z < 1) && normal.dot(this.camera.position.clone().sub(center)) > 0 && b.w > 12 && b.h > 8 && b.x < innerWidth && b.y < innerHeight && b.x + b.w > 0 && b.y + b.h > 0;
      target.button.hidden = !visible;
      if (visible) Object.assign(target.button.style, { left: `${b.x}px`, top: `${b.y}px`, width: `${b.w}px`, height: `${b.h}px`, clipPath: `polygon(${b.points.map((p) => `${(p.x - b.x) / b.w * 100}% ${(p.y - b.y) / b.h * 100}%`).join(',')})` });
    }
    if (now - this.lastPaint > 250) { this.paint(); this.lastPaint = now; }
    this.ambient.update(now, this.state);
    this.sky?.update(this.state?.geometry?.viewport, this.camera.position);
    this.animateLights(now);
    this.applyMood(now);
    const shake = this.burnFx(now);              // Phase 6: TMI burn shake, glow, flicker
    if (shake) this.camera.position.add(shake);
    if (this.fx) this.fx.render(); else this.renderer.render(this.scene, this.camera);
    if (shake) this.camera.position.sub(shake);
  }
}

// Conjunction: the Earth room dims to its minimum and the cockpit displays drop
// in brightness (the cabin hum stops in sound). The flood: a slight warm light
// aboard while the held letters arrive. Eased, or instant under reduced motion.
ImmersiveRoom.prototype.applyMood = function applyMood(now) {
  if (!this.moodLight) { this.moodLight = new THREE.AmbientLight(0xffb36b, 0); this.scene.add(this.moodLight); this.baseExposure = this.renderer.toneMappingExposure; }
  const k = this.reduced ? 1 : 0.04, ease = (a, b) => a + (b - a) * k;
  const exposure = this.baseExposure * (this.dark ? (this.ship ? 0.8 : 0.42) : 1);
  this.renderer.toneMappingExposure = ease(this.renderer.toneMappingExposure, exposure);
  const screen = this.dark && this.ship ? 0.55 : 1, cur = this.screenLevel ?? 1, next = ease(cur, screen);
  if (Math.abs(next - cur) > 0.002 || this.screenLevel == null) { this.screenLevel = next; for (const f of this.feeds.values()) f.material.color.setScalar(next); }
  this.moodLight.intensity = ease(this.moodLight.intensity, this.ship && (this.warmUntil || 0) > now ? 0.35 : 0);
};

// Phase 6: the trans-Mars injection burn (clients/shared/launch.js drives burnState).
// Display screens: T-10 on the crew display, BURN on TERN, the delta-v counter on
// engineering; TMI BURN on the Earth room's flight-dynamics tile.
ImmersiveRoom.prototype.drawBurn = function drawBurn(f, g, W, H) {
  const b = this.burnState, cut = b.phase !== 'burn';
  const kinds = this.ship ? ['systems', 'commander', 'tern'] : ['trajectory'];
  if (!kinds.includes(f.kind)) return false;
  g.fillStyle = BG; g.fillRect(0, 70, W, H - 70);
  const centre = (t, y, color, size, font = '"B612 Mono"') => { g.fillStyle = color; g.font = `${size}px ${font}`; g.textAlign = 'center'; g.fillText(String(t), W / 2, y, W - 60); g.textAlign = 'left'; };
  const foot = (m) => { g.fillStyle = META; g.font = '17px "B612 Mono"'; g.fillText(m, 30, H - 24, W - 60); };
  const counter = (y, size) => centre(`${num(b.dv, 2)} km/s`, y, cut ? INK : '#f0c27a', size);
  const bar = (y) => { g.fillStyle = LINE; g.fillRect(40, y, W - 80, 10); g.fillStyle = cut ? NOMINAL : CAUTION; g.fillRect(40, y, (W - 80) * b.k, 10); };
  if (f.kind === 'commander') {
    centre(cut ? 'TIME TO CUTOFF' : 'TIME TO ENGINE CUTOFF', 128, META, 24, 'B612');
    centre(cut ? b.cockpit.cutoff_word : `T-${b.n}`, H * 0.62, cut ? NOMINAL : INK, cut ? Math.round(H * 0.17) : Math.round(H * 0.36));
    foot('TRANS-MARS INJECTION / FINAL SECONDS');
  } else if (f.kind === 'tern') {
    centre(cut ? b.cockpit.cutoff_word : b.cockpit.burn_word, H * 0.5, cut ? NOMINAL : CAUTION, Math.round(H * (cut ? 0.15 : 0.3)));
    centre(cut ? 'Main engine shut down on time' : `Main engine firing · T-${b.n}`, H * 0.5 + 64, META, 24, 'B612');
    foot('TERN / BURN MONITOR');
  } else if (f.kind === 'systems') {
    centre('DELTA-V, TRANS-MARS INJECTION', 128, META, 24, 'B612');
    counter(H * 0.56, Math.round(H * 0.2)); bar(H * 0.66);
    foot('MODEL / IMPULSIVE FROM A 300 KM PARKING ORBIT / FROM YOUR C3');
  } else {
    centre(cut ? b.earth.line.toUpperCase() : b.earth.tile, H * 0.36, cut ? NOMINAL : '#f0c27a', Math.round(H * 0.13));
    counter(H * 0.6, Math.round(H * 0.15)); bar(H * 0.68);
    centre(cut ? 'Meridian-1 is on its way to Mars' : `Cutoff in ${b.n} s`, H * 0.8, META, 24, 'B612');
    foot('MERIDIAN-1 / TMI / DELTA-V MODEL FROM C3');
  }
  return true;
};

// Shake rises with the burn and settles after cutoff; the canopy warms with engine
// glow; annunciators flicker. None of it under reduced motion or low effects.
ImmersiveRoom.prototype.burnFx = function burnFx(now) {
  const b = this.burnState, calm = this.reduced || this.fxLow;
  if (!this.ship) return null;
  if (!this.burnLight) {
    this.burnLight = new THREE.PointLight(0xffa35a, 0, 9, 1.4); this.burnLight.position.set(0, 2.3, -1.4); this.scene.add(this.burnLight);
    this.baseBackground = this.scene.background?.clone?.() || null;
  }
  const burning = b && b.phase === 'burn' && !calm;
  const k = burning ? b.k : 0;
  this.burnLight.intensity = burning ? 1.5 + 10 * k ** 1.3 : 0;            // cutoff: off at once
  if (this.baseBackground && this.scene.background?.isColor) {
    if (burning) this.scene.background.setRGB(0.012 + 0.11 * k, 0.006 + 0.05 * k, 0.002 + 0.016 * k); else this.scene.background.copy(this.baseBackground);
  }
  if (burning) {
    for (const lamp of this.lamps || []) { const r = Math.sin(lamp.mesh.id * 12.9898 + Math.floor(now / 70) * 78.233) * 43758.5453 % 1; lamp.mesh.material.color.setHex(Math.abs(r) < 0.18 + 0.4 * k ? 0xd99a2b : 0x202325); }
  } else if (this.lampsFlickered) this.paintLamps();
  this.lampsFlickered = burning;
  if (!b || calm) return null;
  let amp = 0;
  if (b.phase === 'burn') amp = 0.0025 + 0.016 * b.k ** 1.5;
  else if (b.cutAt) amp = 0.012 * Math.max(0, 1 - (now - b.cutAt) / 450);    // the stars settle
  if (amp <= 0) return null;
  return new THREE.Vector3((Math.random() - 0.5) * amp, (Math.random() - 0.5) * amp, (Math.random() - 0.5) * amp * 0.4);
};

export function createRoom(options) {
  try { return new ImmersiveRoom(options); }
  catch (e) { console.warn('3D room unavailable; using dashboard view', e); $('#room-root')?.remove(); document.body.classList.remove('immersive-mode'); return null; }
}
