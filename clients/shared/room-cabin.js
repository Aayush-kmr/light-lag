// Meridian flight deck: the forward cockpit of the transit vehicle. A wrap-around
// canopy looks out on the real sky (room-fx.js Sky: Yale Bright Star Catalog plus
// the Sun, Earth and Mars in their ephemeris directions), with the canopy facing
// Mars, the destination. Below it: a glareshield with master caution and
// annunciators, three display units with edge keys, backup gauges and the
// vehicle switch panel, side consoles and an overhead panel.
// Illustrative MODEL interior, not a flight-qualified design.
import * as THREE from 'three';

const CANOPY = { r: 1.9, cz: 0.3, sill: 1.62, head: 2.36, half: THREE.MathUtils.degToRad(100) };

export function buildCabin(b, seat) {
  const M = b.m, S = b.scene;
  S.background = new THREE.Color(0x000000);
  S.environmentIntensity = 0.16;
  S.add(new THREE.HemisphereLight(0x6f8796, 0x1a1714, 0.16));
  // Sunlight through the canopy; Sky.update aims it along the real Sun vector.
  const sun = new THREE.DirectionalLight(0xfff1dc, 3.2);
  sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0005; sun.shadow.normalBias = 0.02;
  Object.assign(sun.shadow.camera, { left: -4, right: 4, top: 4, bottom: -4, near: 0.5, far: 30 });
  S.add(sun, sun.target); b.sunLight = sun;

  shell(b);
  noseOutside(b);
  canopy(b);
  forwardPanel(b);
  for (const s of [-1, 1]) sideConsole(b, s);
  overhead(b);
  for (const x of [-0.42, 0.42]) couch(b, x);
  rear(b);

  // Cockpit light: warm panel flood under the glareshield, blue footwell strips,
  // display spill, and red alert lights that room.js drives from the alarm list.
  b.spill(2.4, 0.08, 0xffc68a, 3.0, [0, 1.54, -0.98], [0, 0.8, -0.2]);
  b.spill(2.5, 0.4, 0xbcd0dc, 2.2, [0, 1.3, -0.93], [0, 1.1, 0.6]);
  for (const s of [-1, 1]) {
    b.lamp(0.02, 1.4, 0x3d6f9a, s * 1.05, 0.06, -0.3, S, 1.4).rotation.set(-Math.PI / 2, 0, 0);
  }
  const red = new THREE.PointLight(0xff2a1a, 0, 6, 2); red.position.set(0, 2.3, -0.2); S.add(red);
  b.alertLights = [red];

  const x = seat === 'engineer' ? -0.42 : 0.42;
  return {
    home: [x, 1.42, 0.45], look: [x * 0.25, 1.5, -3.6],
    overview: [0.0, 1.95, 1.35], overviewLook: [0, 1.55, -2.2],
  };
}

function shell(b) {
  const M = b.m, { r, cz, sill, head, half } = CANOPY;
  const wall = new THREE.MeshStandardMaterial({ color: 0x2a2e33, roughness: 0.85, metalness: 0.1, side: THREE.BackSide });
  const segment = (y0, y1) => {
    const geo = new THREE.CylinderGeometry(r, r, y1 - y0, 48, 1, true, Math.PI - half, half * 2);
    const m = new THREE.Mesh(geo, wall); m.position.set(0, (y0 + y1) / 2, cz); m.receiveShadow = true; m.castShadow = true; b.scene.add(m); return m;
  };
  segment(0, sill); segment(head, 2.75);
  // Straight side walls from the canopy ends back to the rear bulkhead.
  const ex = r * Math.sin(half), ez = cz - r * Math.cos(half);
  for (const s of [-1, 1]) b.slab(0.06, 2.75, 1.65 - ez, M.panelGray, s * (ex + 0.03), 1.375, (ez + 1.65) / 2);
  b.slab(4.4, 0.05, 4.2, new THREE.MeshStandardMaterial({ color: 0x1d2024, roughness: 0.8, metalness: 0.2 }), 0, 0, -0.35);
  b.slab(4.4, 0.08, 4.2, M.panelGray, 0, 2.76, -0.35);
  // Floor grating lines.
  for (let z = -1.5; z < 1.6; z += 0.3) b.slab(2.6, 0.006, 0.012, M.black, 0, 0.03, z);
}

// Hull skin outside the canopy so the cockpit sits inside a vehicle.
function noseOutside(b) {
  const skin = new THREE.MeshStandardMaterial({ color: 0xc9c6bf, roughness: 0.55, metalness: 0.35 });
  const nose = new THREE.Mesh(new THREE.SphereGeometry(3.2, 48, 24, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2), skin);
  nose.scale.set(1.15, 0.5, 1.4); nose.position.set(0, CANOPY.sill - 0.06, -0.4); nose.rotation.x = Math.PI; nose.receiveShadow = true;
  b.scene.add(nose);
  // Panel lines and RCS thruster quads on the skin.
  for (const s of [-1, 1]) {
    const quad = b.group(s * 2.4, CANOPY.sill - 0.25, -2.6);
    b.box(0.28, 0.14, 0.28, new THREE.MeshStandardMaterial({ color: 0x44474b, roughness: 0.5, metalness: 0.6 }), 0, 0, 0, quad, 0.02);
    for (const a of [0, 1, 2]) { const n = b.cylinder(0.035, 0.08, b.m.steel, 0, 0.02, 0, quad, 12, 0.05); n.rotation.z = (a - 1) * Math.PI / 2; n.position.x = (a - 1) * 0.12; }
  }
}

function canopy(b) {
  const M = b.m, { r, cz, sill, head, half } = CANOPY, frame = new THREE.MeshStandardMaterial({ color: 0x1b1e21, roughness: 0.5, metalness: 0.5 });
  const arc = (y, rr = r - 0.02) => {
    const pts = []; for (let i = 0; i <= 40; i++) { const a = -half + (2 * half * i) / 40; pts.push([Math.sin(a) * rr, y, cz - Math.cos(a) * rr]); }
    return pts;
  };
  b.tube(arc(sill), 0.05, frame, b.scene, 80); b.tube(arc(head), 0.06, frame, b.scene, 80);
  b.tube(arc(sill + 0.01, r - 0.12), 0.02, M.metal, b.scene, 80);
  // Mullions between the window panes.
  for (let i = 0; i <= 7; i++) {
    const a = -half + (2 * half * i) / 7, x = Math.sin(a) * (r - 0.03), z = cz - Math.cos(a) * (r - 0.03);
    const post = b.box(0.07, head - sill, 0.09, frame, x, (sill + head) / 2, z, b.scene, 0.02); post.rotation.y = -a;
  }
  // Faint glass so the panes read as glass, not holes.
  const glass = new THREE.Mesh(new THREE.CylinderGeometry(r + 0.01, r + 0.01, head - sill, 64, 1, true, Math.PI - half, half * 2),
    new THREE.MeshBasicMaterial({ color: 0x6f8fa8, transparent: true, opacity: 0.025, side: THREE.BackSide, depthWrite: false }));
  glass.position.set(0, (sill + head) / 2, cz); b.scene.add(glass);
}

function forwardPanel(b) {
  const M = b.m, S = b.scene;
  // Main panel body with a glareshield hood across the top.
  b.box(2.75, 1.12, 0.36, M.panelBlack, 0, 0.98, -1.28, S, 0.03);
  b.box(2.85, 0.1, 0.42, M.panelGray, 0, 1.58, -1.08, S, 0.03);
  b.box(2.85, 0.03, 0.02, M.steel, 0, 1.53, -0.875, S, 0.008);

  // Glareshield face: master caution, annunciators, lit mode buttons.
  const gs = b.group(0, 1.6, -0.86, S, 0, -0.35);
  const mc = b.group(-0.88, 0.0, 0.0, gs);
  b.box(0.16, 0.1, 0.03, M.panelBlack, 0, 0, 0, mc, 0.008);
  const lens = b.lamp(0.12, 0.065, 0x3a1210, 0, 0.004, 0.0165, mc, 1);
  const cap = caption('Master caution', 'rgba(255,230,225,0.9)'); cap.position.set(0, 0.004, 0.0175); cap.scale.setScalar(1.04); mc.add(cap);
  b.master = { mesh: lens, title: 'Master caution' };
  const caps = [['ppco2', 'CO₂'], ['batt_soc', 'Battery'], ['link_db', 'Comm link'], ['loop_a_c', 'Coolant A'], ['loop_b_c', 'Coolant B'], ['shelter', 'Shelter']];
  caps.forEach(([channel, label], i) => {
    const x = 0.5 + (i % 3) * 0.13, y = 0.022 - Math.floor(i / 3) * 0.05;
    const light = b.lamp(0.118, 0.042, 0x202325, x, y, 0.004, gs, 1);
    const text = caption(label); text.position.set(x, y, 0.005); text.scale.set(1, 0.6, 1); gs.add(text);
    b.lamps.push({ channel, mesh: light, label });
  });
  // Mode pushbuttons (decorative): light up in sequence when the crew acts.
  b.chase = [];
  for (let i = 0; i < 8; i++) {
    const x = -0.56 + i * 0.13;
    b.box(0.1, 0.05, 0.02, M.panelBlack, x, 0, 0, gs, 0.006);
    b.chase.push(b.lamp(0.085, 0.034, 0x182024, x, 0, 0.0105, gs, 1));
  }

  // Three display units with edge keys.
  const units = [['systems', 'ENGINEERING', -0.82, 0.1], ['commander', 'CREW / RADIATION', 0, 0], ['tern', 'MISSION COMPUTER / TERN', 0.82, -0.1]];
  for (const [kind, title, x, ry] of units) displayUnit(b, kind, title, x, ry);

  // Pedestal: backup gauges (left) and vehicle switches (right).
  const ped = b.group(0, 0.84, -0.8, S, 0, -0.78);
  b.box(2.3, 0.3, 0.5, M.panelBlack, 0, 0.66, -0.98, S, 0.02); // pedestal support
  b.box(2.2, 0.44, 0.05, M.panelBlack, 0, 0, 0, ped, 0.015);
  b.box(2.26, 0.48, 0.03, M.panelGray, 0, 0, -0.03, ped, 0.015);
  [['batt_soc', 'Battery %'], ['ppco2', 'Cabin CO₂ mmHg'], ['cabin_c', 'Cabin °C'], ['dose', 'Mission dose mSv']]
    .forEach(([channel, label], i) => b.gauge(channel, label, -0.9 + i * 0.22, -0.01, 0.027, ped, 0.088));
  [['science', 'Science power'], ['pump_switch', 'Coolant A / B'], ['shelter', 'Storm shelter'], ['hga_lock', 'Lock antenna']]
    .forEach(([key, title], i) => toggle(b, ped, key, title, 0.2 + i * 0.19, key === 'shelter'));
  // Backlit legends along the pedestal top.
  b.lamp(2.0, 0.006, 0xffb25e, 0, 0.215, 0.026, ped, 0.6);
}

function displayUnit(b, kind, title, x, ry) {
  const M = b.m, w = 0.6, h = 0.375;
  const du = b.group(x, 1.3, -1.0, b.scene, ry, -0.16);
  b.box(w + 0.19, h + 0.13, 0.07, M.panelGray, 0, -0.01, -0.02, du, 0.018);
  b.screen(kind, title, w, h, du, 0, 0, 0.0165, true);
  const key = new THREE.BoxGeometry(0.034, 0.024, 0.012);
  for (const s of [-1, 1]) for (let i = 0; i < 6; i++) {
    const k = new THREE.Mesh(key, M.keycapLight); k.position.set(s * (w / 2 + 0.048), h / 2 - 0.035 - i * 0.061, 0.02); du.add(k);
  }
  for (let i = 0; i < 7; i++) { const k = new THREE.Mesh(key, M.keycapLight); k.position.set(-0.26 + i * 0.075, -h / 2 - 0.042, 0.02); du.add(k); }
  const knob = b.cylinder(0.022, 0.03, M.rubber, w / 2 + 0.048, -h / 2 - 0.042, 0.025, du, 16); knob.rotation.x = Math.PI / 2;
}

// Side consoles: two ambient displays each, rows of guarded and plain toggles,
// rotary selectors and lit pushbuttons (decorative).
function sideConsole(b, s) {
  const M = b.m, g = b.group(s * 1.5, 0, -0.42, b.scene, -s * 0.8);
  b.box(0.9, 1.0, 0.5, M.panelBlack, 0, 0.5, 0, g, 0.03);
  const face = b.group(0, 1.08, 0.12, g, 0, -0.5);
  b.box(0.86, 0.5, 0.04, M.panelGray, 0, 0, 0, face, 0.012);
  const types = s < 0 ? ['scope', 'matrix'] : ['spectrum', 'adi'];
  types.forEach((t, i) => b.ambient(t, 0.34, 0.21, face, -0.2 + i * 0.4, 0.1, 0.022));
  b.mockSwitches(face, 8, -0.38, -0.11, 0.095, 0.022);
  const knobs = b.group(0, 0.74, 0.27, g, 0, -1.1);
  b.box(0.86, 0.28, 0.03, M.panelBlack, 0, 0, 0, knobs, 0.01);
  for (let i = 0; i < 4; i++) { const k = b.cylinder(0.03, 0.03, M.rubber, -0.3 + i * 0.2, 0.03, 0.03, knobs, 16); k.rotation.x = Math.PI / 2; b.slab(0.006, 0.026, 0.004, M.metal, -0.3 + i * 0.2, 0.045, 0.046, knobs); }
  b.mockButtons(knobs, 6, -0.3, -0.07, 0.12, 0.02);
}

function overhead(b) {
  const M = b.m, panel = b.group(0, 2.5, -0.35, b.scene, 0, 0.32);
  b.box(1.9, 0.08, 1.0, M.panelBlack, 0, 0, 0, panel, 0.03);
  const face = b.group(0, -0.045, 0, panel, 0, Math.PI / 2);
  for (let r = 0; r < 5; r++) b.mockSwitches(face, 12, -0.8, 0.38 - r * 0.18, 0.145, 0.0);
  b.lamp(1.7, 0.01, 0xffb25e, 0, 0.47, 0.001, face, 0.5);
  // Circuit-breaker rows along the side headers.
  for (const s of [-1, 1]) {
    const cb = b.group(s * 1.05, 2.28, 0.6, b.scene, -s * Math.PI / 2, 0.2);
    b.box(1.0, 0.24, 0.04, M.panelBlack, 0, 0, 0, cb, 0.01);
    const geo = new THREE.CylinderGeometry(0.011, 0.011, 0.025, 10), mat = new THREE.MeshStandardMaterial({ color: 0x1a1a1a, roughness: 0.4 });
    const inst = new THREE.InstancedMesh(geo, mat, 40), m = new THREE.Matrix4(), q = new THREE.Quaternion().setFromEuler(new THREE.Euler(Math.PI / 2, 0, 0));
    for (let i = 0; i < 40; i++) { m.compose(new THREE.Vector3(-0.45 + (i % 20) * 0.047, 0.05 - Math.floor(i / 20) * 0.09, 0.03), q, new THREE.Vector3(1, 1, 1)); inst.setMatrixAt(i, m); }
    cb.add(inst);
    b.lamp(0.92, 0.008, 0xffb25e, 0, 0.1, 0.021, cb, 0.45);
  }
}

// Annunciator / caption text.
function caption(label, color = 'rgba(214,210,200,0.9)') {
  const c = document.createElement('canvas'); c.width = 256; c.height = 156;
  const g = c.getContext('2d'); g.fillStyle = color; g.font = '44px B612'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(label, 128, 80, 236);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  return new THREE.Mesh(new THREE.PlaneGeometry(0.115, 0.07), new THREE.MeshBasicMaterial({ map: t, transparent: true, toneMapped: false }));
}

// Bound vehicle switch: placard, hex bushing, bat handle, state lamp, and for
// the storm shelter a hinged guard that lifts as you reach for it.
function toggle(b, ped, key, title, x, guarded) {
  const M = b.m;
  b.plate(title, 0.18, 0.034, x, 0.12, 0.026, { parent: ped, size: 84, background: '#16191b', color: '#e6d3b0' });
  const nut = b.cylinder(0.018, 0.012, M.metal, x, 0.0, 0.031, ped, 6); nut.rotation.x = Math.PI / 2;
  const pivot = b.group(x, 0.0, 0.035, ped);
  const lever = b.cylinder(0.0055, 0.06, M.metal, 0, 0.0, 0.03, pivot, 12, 0.004); lever.rotation.x = Math.PI / 2;
  const tip = new THREE.Mesh(new THREE.SphereGeometry(0.008, 12, 8), M.metal); tip.position.z = 0.06; pivot.add(tip);
  const lamp = b.lamp(0.02, 0.02, 0x1b1d1f, x, -0.085, 0.0265, ped, 1);
  let guard = null;
  if (guarded) {
    guard = b.group(x, -0.045, 0.03, ped);
    for (const s of [-1, 1]) b.box(0.006, 0.09, 0.07, M.metal, s * 0.032, 0.045, 0.035, guard, 0.002);
    b.box(0.07, 0.008, 0.07, new THREE.MeshStandardMaterial({ color: 0xb02a20, roughness: 0.4, metalness: 0.3 }), 0, 0.09, 0.035, guard, 0.003);
  }
  const target = new THREE.Mesh(new THREE.PlaneGeometry(0.16, 0.2), new THREE.MeshBasicMaterial({ visible: false }));
  target.position.set(x, 0.02, 0.07); ped.add(target);
  b.control(key, title.toUpperCase(), target, pivot, { guard, lamp });
}

function couch(b, x) {
  const M = b.m, p = b.group(x, 0, 0.75);
  b.box(0.06, 0.5, 0.06, M.steel, 0, 0.25, -0.05, p, 0.01);
  b.box(0.62, 0.08, 0.56, M.steel, 0, 0.5, -0.05, p, 0.02);
  b.box(0.56, 0.1, 0.52, M.couch, 0, 0.58, -0.05, p, 0.04);
  const back = b.box(0.56, 0.8, 0.1, M.couch, 0, 0.98, 0.25, p, 0.04); back.rotation.x = 0.22;
  const head = b.box(0.34, 0.2, 0.1, M.couch, 0, 1.42, 0.36, p, 0.04); head.rotation.x = 0.22;
  for (const s of [-1, 1]) {
    b.box(0.08, 0.06, 0.46, M.couch, s * 0.33, 0.78, -0.05, p, 0.02);
    const grip = b.cylinder(0.02, 0.1, M.rubber, s * 0.33, 0.86, -0.22, p, 12); grip.rotation.x = -0.3;
  }
  for (const s of [-1, 1]) { const st = b.box(0.05, 0.62, 0.008, M.strap, s * 0.13, 1.0, 0.19, p, 0.003); st.rotation.x = 0.22; }
  b.contact(0.9, 0.9, 0, 0.04, -0.05, 0.6, p);
}

function rear(b) {
  const M = b.m;
  b.slab(4.4, 2.75, 0.08, M.panelGray, 0, 1.375, 1.68);
  const hatch = b.group(0, 1.2, 1.63);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.5, 0.05, 12, 48), M.steel); hatch.add(ring);
  const disc = b.cylinder(0.48, 0.05, M.panelBlack, 0, 0, 0, hatch, 48); disc.rotation.x = Math.PI / 2;
  b.box(0.36, 0.05, 0.06, M.metal, 0, 0, -0.06, hatch, 0.015);
  b.lamp(0.06, 0.03, 0x79c57f, 0.62, 0.4, -0.05, hatch, 1).rotation.y = Math.PI;
  // Laptop strapped to the side wall: the air-to-ground message queue.
  const lap = b.group(-1.78, 1.25, 0.9, b.scene, Math.PI / 2, -0.05);
  b.box(0.36, 0.25, 0.016, M.panelBlack, 0, 0, 0, lap, 0.008);
  b.screen('radio', 'AIR-TO-GROUND', 0.32, 0.2, lap, 0, 0.005, 0.009, true);
  b.slab(0.4, 0.03, 0.02, M.strap, 0, -0.16, 0.0, lap);
  // Stowage and an ambient status screen on the right wall.
  for (const [s, z, y, size] of [[1, 1.2, 0.6, 0.6], [1, 0.9, 1.15, 0.45], [-1, 1.35, 0.55, 0.55]]) {
    const g = b.group(s * 1.7, y, z, b.scene, s * -Math.PI / 2);
    b.box(size, size * 0.62, 0.3, M.bag, 0, 0, 0, g, 0.06);
    for (const dx of [-0.3, 0.3]) b.slab(0.03, size * 0.66, 0.315, M.strap, dx * size, 0, 0, g);
  }
  const amb = b.group(1.78, 1.55, 1.0, b.scene, -Math.PI / 2);
  b.box(0.44, 0.3, 0.03, M.panelBlack, 0, 0, -0.01, amb, 0.01);
  b.ambient('log', 0.4, 0.25, amb, 0, 0, 0.008);
}
