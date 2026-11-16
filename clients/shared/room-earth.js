// Lantern mission operations: a dim, tiered flight control room facing a
// full-width video wall. Illustrative MODEL interior, not a reconstruction of
// a specific NASA facility. Screens and controls bind through room.js.
import * as THREE from 'three';

const WARM = 0xffd2a1;   // ~2700 K downlights
const SPILL = 0xb9c6cf;  // light thrown by mostly-dark display screens

// Tiers step up toward the back of the room, as in an operations room where
// the flight director's row looks over the front consoles to the wall.
const TIERS = [
  { y: 0.0, z0: -13, z1: -4.2 },
  { y: 0.38, z0: -4.2, z1: 0.6 },
  { y: 0.75, z0: 0.6, z1: 8 },
];

export function buildEarth(b, seat) {
  const M = b.m, S = b.scene, top = TIERS[2].y, front = 2.6;
  S.background = new THREE.Color(0x0a0b0c);
  S.environmentIntensity = 0.18;
  S.add(new THREE.HemisphereLight(0x3a3c40, 0x0c0d0e, 0.25));

  shell(b);
  videoWall(b);

  // Front rows: occupied consoles represented by equipment and empty chairs;
  // no invented NPC decisions or telemetry are added to the mission.
  const background = [['telemetry', 'amb:scope', 'trajectory', 'amb:matrix'], ['amb:strip', 'science', 'amb:spectrum', 'weather'], ['dsn', 'amb:log', 'trajectory', 'amb:bars']];
  for (const [row, z, y] of [[0, -5.8, TIERS[0].y], [1, -1.2, TIERS[1].y]]) {
    [-6.7, 0, 6.7].forEach((x, i) => {
      opsConsole(b, { x, y, z, width: 5.4, monitors: background[(i + row) % 3].map((kind) => ({ kind, title: '' })), interactive: false });
      chair(b, x - 1.1, y, z + 0.75, 0.15); chair(b, x + 1.0, y, z + 0.8, -0.1);
      b.spot(WARM, 40, [x, 6.8, z - 0.2], [x, y + 0.75, z - 0.4], { angle: 0.42, penumbra: 0.75 });
      downlight(b, x, z - 0.2, y);
    });
  }
  sideWalls(b);
  // Centre of the top console: shared mock cluster between the two stations.
  const mid = b.group(0, top, front);
  monitor(b, mid, 'amb:ring', '', -0.47, 0.05, false);
  monitor(b, mid, 'amb:log', '', 0.47, -0.05, false);
  b.mockButtons(b.group(0, top + 0.775, front - 0.62, b.scene, 0, -Math.PI / 2 + 0.3), 8, -0.42, 0, 0.12, 0);
  // Amber alert wash near the wall; room.js drives it from Earth-side alerts.
  const alert = new THREE.PointLight(0xffa040, 0, 18, 1.6); alert.position.set(0, 6.2, -10.5); S.add(alert); b.alertLights = [alert];

  // The two player stations share one long console on the top tier.
  const stations = [
    { x: -2.55, name: 'Flight director', list: [['radio', 'COMMUNICATIONS'], ['telemetry', 'VEHICLE SYSTEMS'], ['design', 'MISSION DESIGN']] },
    { x: 2.55, name: 'Mission scientist', list: [['science', 'SCIENCE RETURN'], ['crew', 'CREW & OPERATIONS'], ['autonomy', 'AUTONOMY / POLICY']] },
  ];
  opsConsole(b, { x: 0, y: top, z: front, width: 10.6, monitors: [], interactive: true });
  for (const st of stations) {
    const unit = b.group(st.x, top, front);
    st.list.forEach(([kind, title], i) => {
      const angle = (1 - i) * 0.16;
      monitor(b, unit, kind, title, (i - 1) * 0.93, angle, true);
    });
    deskKit(b, unit, st, seat);
    b.spot(WARM, 52, [st.x, 7.2, front + 0.6], [st.x, top + 0.75, front - 0.3], { angle: 0.4, penumbra: 0.8, shadow: true });
    downlight(b, st.x, front + 0.6, top, false);
    b.spill(2.9, 0.55, SPILL, 1.3, [st.x, top + 1.1, front - 0.85], [st.x, top + 0.75, front + 1.2]);
    // The player's own chair stands behind the camera (they are sitting in it), so it
    // never hides their monitors; the other station's chair is pushed back from its desk.
    const mine = (seat === 'scientist') === (st.x > 0);
    if (mine) chair(b, st.x * 0.9, top, front + 2.15, 0);
    else chair(b, st.x + (st.x < 0 ? 0.8 : -0.8), top, front + 0.75, st.x < 0 ? 0.55 : -0.55);
  }
  // Station name placards on the console edge.
  for (const st of stations) b.plate(st.name, 0.7, 0.045, st.x, top + 0.62, front - 0.13, { size: 44, background: '#1c1f22', color: '#cdb894' });

  const fd = seat !== 'scientist';
  const sx = fd ? -2.55 : 2.55;
  return {
    home: [sx * 0.9, top + 1.55, front + 1.75], look: [sx * 0.55, top + 1.0, -9],
    overview: [0, 5.2, 7.4], overviewLook: [0, 2.6, -8],
  };
}

function shell(b) {
  const M = b.m;
  // Tiered carpet floors with lit step edges.
  for (const [i, t] of TIERS.entries()) {
    const len = t.z1 - t.z0, cz = (t.z0 + t.z1) / 2;
    b.slab(24, 0.2 + t.y, len, M.carpet, 0, (t.y - 0.2) / 2, cz);
    if (i > 0) {
      b.slab(24, t.y - TIERS[i - 1].y, 0.04, M.graphite, 0, (t.y + TIERS[i - 1].y) / 2, t.z0 - 0.02);
      b.lamp(23.6, 0.02, 0x7a6444, 0, t.y + 0.004, t.z0 + 0.05).rotation.x = -Math.PI / 2; // step nosing light
    }
  }
  M.carpet.map.repeat.set(12, 10);
  // Acoustic fabric walls, dark ceiling with a soffit around the video wall.
  b.slab(24, 7.4, 0.2, M.fabric, 0, 3.6, -13.1);
  b.slab(0.2, 7.4, 21.4, M.fabric, -12.1, 3.6, -2.4); b.slab(0.2, 7.4, 21.4, M.fabric, 12.1, 3.6, -2.4);
  b.slab(24, 7.4, 0.2, M.fabric, 0, 3.6, 8.1);
  b.slab(24.4, 0.2, 21.6, M.ceiling, 0, 7.3, -2.4);
  b.slab(24, 0.5, 1.2, M.ceiling, 0, 6.95, -12.4);
  // Ceiling grid lines.
  for (let x = -10; x <= 10; x += 2.5) b.slab(0.03, 0.02, 21, M.graphite, x, 7.19, -2.4);
  // Viewing gallery glass in the back wall (dark).
  b.slab(14, 1.6, 0.06, M.glass, 0, 4.9, 7.98);
  b.slab(14.2, 0.06, 0.12, M.trim, 0, 4.1, 7.95); b.slab(14.2, 0.06, 0.12, M.trim, 0, 5.7, 7.95);
  // Wall-mounted equipment racks with status LEDs.
  for (const x of [-11.45, 11.45]) for (const z of [-9.5, -6.5, -3.5]) {
    const y0 = z < -4.2 ? 0 : 0.38;
    b.box(1.0, 2.3, 0.9, M.graphite, x, y0 + 1.15, z, b.scene, 0.02);
    for (let n = 0; n < 7; n++) {
      b.slab(0.06, 0.24, 0.78, M.bezel, x - Math.sign(x) * 0.51, y0 + 0.35 + n * 0.29, z);
      const led = b.lamp(0.012, 0.012, n % 3 ? 0x5fae6a : 0xcf9a4a, x - Math.sign(x) * 0.545, y0 + 0.35 + n * 0.29, z + 0.3, b.scene, 0.9);
      led.rotation.y = -Math.sign(x) * Math.PI / 2;
      b.blinkers.push({ mesh: led, on: n % 3 ? 0x5fae6a : 0xcf9a4a, alt: 0xe0a43a });
    }
    b.contact(1.4, 1.3, x - Math.sign(x) * 0.2, y0, z, 0.5);
  }
}

function videoWall(b) {
  const M = b.m, y = 3.95, z = -12.9, h = 4.2;
  b.slab(21.9, h + 0.5, 0.25, M.black, 0, y, z - 0.08);
  const layout = [['dsn', 'Deep Space Network', -7.77, 5.94], ['trajectory', 'Flight dynamics', 0, 9.4], ['weather', 'Space weather / replay', 7.77, 5.94]];
  for (const [kind, title, x, w] of layout) {
    const g = b.group(x, y, z + 0.06);
    b.screen(kind, title.toUpperCase(), w, h, g, 0, 0, 0.001, true);
    // LCD tile seams: real video walls are built from tiled panels.
    const cols = Math.round(w / 2.4), rows = 2;
    for (let c = 1; c < cols; c++) b.slab(0.014, h, 0.01, M.black, -w / 2 + (w / cols) * c, 0, 0.008, g);
    for (let r = 1; r < rows; r++) b.slab(w, 0.014, 0.01, M.black, 0, -h / 2 + (h / rows) * r, 0.008, g);
    b.plate(title, Math.min(3.2, w * 0.5), 0.17, 0, -h / 2 - 0.2, 0.02, { parent: g, size: 34, background: '#0a0b0c', color: '#8e9295' });
  }
  // Mullions between the three walls.
  for (const x of [-4.73, 4.73]) b.slab(0.06, h, 0.04, M.black, x, y, z + 0.08);
  // Clock strip: UTC, mission elapsed time, one-way light time, data age.
  const clock = b.group(0, 6.42, z + 0.06);
  b.slab(14.2, 0.56, 0.08, M.black, 0, 0, -0.03, clock);
  b.screen('clock', 'MISSION CLOCKS', 14, 0.5, clock, 0, 0, 0.02, false);
  // The wall lights the room.
  b.spill(21, h, 0x9fb2c0, 0.32, [0, y, z + 0.2], [0, y - 0.6, 4]);
}

// Ops console: kick cabinet, worktop with brushed trim, sloped monitor bridge.
// Local origin: worktop front edge (operator side), floor level; operator at +z.
function opsConsole(b, { x, y, z, width, monitors, interactive }) {
  const M = b.m, g = b.group(x, y, z);
  b.box(width - 0.2, 0.68, 0.72, M.graphite, 0, 0.34, -0.5, g, 0.02);
  b.box(width, 0.05, 1.0, M.worktop, 0, 0.74, -0.5, g, 0.012);
  b.box(width + 0.02, 0.035, 0.03, M.trim, 0, 0.735, 0.0, g, 0.008);
  b.slab(width - 0.3, 0.06, 0.05, M.rubber, 0, 0.05, -0.12, g); // kick strip
  // Bridge housing the monitors, raked back.
  const bridge = b.group(0, 0.77, -1.0, g, 0, -0.17);
  b.box(width, 0.66, 0.34, M.graphite, 0, 0.33, -0.12, bridge, 0.025);
  b.box(width + 0.02, 0.028, 0.36, M.trim, 0, 0.67, -0.12, bridge, 0.008);
  for (const s of [-1, 1]) b.box(0.06, 1.48, 1.42, M.graphite, s * (width / 2 + 0.03), 0.74, -0.62, g, 0.02);
  monitors.forEach((m, i) => monitor(b, g, m.kind, m.title, (i - (monitors.length - 1) / 2) * 0.95, (((monitors.length - 1) / 2) - i) * 0.12, interactive));
  b.contact(width + 1.0, 2.0, 0, 0, -0.5, 0.65, g);
  return g;
}

// A monitor set into the bridge face, raked to match it.
function monitor(b, parent, kind, title, mx, ry, interactive) {
  const M = b.m, w = 0.84, h = 0.525;
  const m = b.group(mx, 1.12, -0.93, parent, ry, -0.17);
  b.box(w + 0.07, h + 0.07, 0.05, M.bezel, 0, 0, -0.01, m, 0.012);
  if (kind.startsWith('amb:')) b.ambient(kind.slice(4), w, h, m, 0, 0, 0.0165);
  else b.screen(kind, title, w, h, m, 0, 0, 0.0165, interactive);
  b.lamp(0.008, 0.008, 0x6fbf7a, w / 2 - 0.004, -h / 2 - 0.022, 0.0166, m, 0.8);
  return m;
}

function deskKit(b, unit, st, seat) {
  const M = b.m, top = 0.77;
  b.keyboard(-0.12, top, -0.42, unit);
  b.trackball(0.32, top, -0.4, unit);
  voiceKeyset(b, unit, 0.78, top, -0.48);
  b.binder(-0.85, top, -0.45, unit, 0.18);
  b.mug(0.7, top, -0.18, unit);
  b.headset(-1.2, top + 0.03, -0.62, unit, 0.4).rotation.x = -Math.PI / 2;
  // Clipboard with the station's working checklist.
  const clip = b.group(-0.62, top + 0.004, -0.2, unit, -0.22);
  b.slab(0.22, 0.006, 0.3, b.m.paper, 0, 0, 0, clip);
  const text = b.plate(st.name === 'Flight director' ? 'Check link · queue · wait for receipt' : 'Science first or letters first?', 0.2, 0.06, 0, 0.005, -0.06,
    { parent: clip, size: 36, color: '#2f3336', background: '#d8d2c4' });
  text.rotation.x = -Math.PI / 2;
  b.slab(0.07, 0.012, 0.03, M.metal, 0, 0.006, -0.14, clip);
}

// Voice-loop keyset. The air-to-ground key opens Communications; the other
// keys are lit loop labels only (no invented loop behaviour).
function voiceKeyset(b, unit, x, y, z) {
  const M = b.m, panel = b.group(x, y + 0.05, z, unit, -0.2, -0.55);
  b.box(0.36, 0.2, 0.05, M.panelBlack, 0, 0, 0, panel, 0.01);
  const labels = ['A/G', 'Flight', 'Science', 'DSN', 'TERN', 'Crew', 'Conf', 'Spare'];
  const c = document.createElement('canvas'); c.width = 512; c.height = 280;
  const g = c.getContext('2d'); g.fillStyle = '#16191b'; g.fillRect(0, 0, 512, 280);
  g.font = '26px B612'; g.textAlign = 'center'; g.textBaseline = 'middle';
  labels.forEach((l, i) => {
    const cx = 64 + (i % 4) * 128, cy = 80 + Math.floor(i / 4) * 120;
    g.fillStyle = i === 0 ? '#e1ad5a' : i < 4 ? '#c9c7bf' : '#6c6f72';
    g.fillText(l, cx, cy + 46);
  });
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  const face = new THREE.Mesh(new THREE.PlaneGeometry(0.34, 0.186), new THREE.MeshStandardMaterial({ map: tex, roughness: 0.8 }));
  face.position.z = 0.026; panel.add(face);
  labels.forEach((l, i) => {
    const kx = -0.127 + (i % 4) * 0.085, ky = 0.045 - Math.floor(i / 4) * 0.08;
    b.box(0.056, 0.034, 0.016, M.keycapLight, kx, ky, 0.034, panel, 0.004);
    b.lamp(0.044, 0.008, i === 0 ? 0xe1a24a : i < 4 ? 0x9fb1a3 : 0x2a2d30, kx, ky + 0.006, 0.0425, panel, i === 0 ? 1.1 : 0.6);
    if (i === 0) {
      const target = new THREE.Mesh(new THREE.PlaneGeometry(0.07, 0.05), new THREE.MeshBasicMaterial({ visible: false }));
      target.position.set(kx, ky, 0.045); panel.add(target);
      b.control('radio', 'Air-to-ground loop · open communications', target, null);
    }
  });
}

let coneMaterial;
function downlight(b, x, z, floor = 0, shaft = true) {
  b.cylinder(0.16, 0.06, b.m.bezel, x, 7.18, z, b.scene, 24);
  const disc = b.lamp(0.22, 0.22, 0xffe2bd, x, 7.145, z, b.scene, 1.2); disc.rotation.x = Math.PI / 2;
  disc.geometry = new THREE.CircleGeometry(0.11, 24);
  // A faint shaft of light through the room's haze.
  if (!shaft) return;
  coneMaterial ||= new THREE.MeshBasicMaterial({ color: 0xffd6a6, transparent: true, opacity: 0.004, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide });
  const h = 7.1 - floor - 0.8, cone = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.85, h, 32, 1, true), coneMaterial);
  cone.position.set(x, 7.1 - h / 2, z); cone.renderOrder = 2; b.scene.add(cone);
}

// Side-wall displays: the room continues beyond the consoles.
function sideWalls(b) {
  const M = b.m;
  for (const [s, types] of [[-1, ['ring', 'strip']], [1, ['spectrum', 'matrix']]]) {
    types.forEach((t, i) => {
      const g = b.group(s * 11.93, 3.4, -9.6 + i * 3.6, b.scene, -s * Math.PI / 2);
      b.box(3.1, 1.95, 0.08, M.black, 0, 0, -0.03, g, 0.02);
      b.ambient(t, 3.0, 1.85, g, 0, 0, 0.012);
    });
  }
}

// High-backed operations chair on casters.
function chair(b, x, y, z, rot = 0) {
  const M = b.m, p = b.group(x, y, z, b.scene, rot);
  b.box(0.56, 0.1, 0.54, M.chair, 0, 0.5, 0, p, 0.04);
  const back = b.box(0.52, 0.72, 0.09, M.chair, 0, 0.95, 0.27, p, 0.04); back.rotation.x = 0.12;
  const head = b.box(0.3, 0.16, 0.08, M.chair, 0, 1.38, 0.33, p, 0.03); head.rotation.x = 0.12;
  b.cylinder(0.03, 0.34, M.chairFrame, 0, 0.27, 0, p, 12);
  for (let i = 0; i < 5; i++) {
    const a = i * Math.PI * 2 / 5, leg = b.box(0.04, 0.03, 0.32, M.chairFrame, Math.sin(a) * 0.15, 0.07, Math.cos(a) * 0.15, p, 0.008);
    leg.rotation.y = a;
    const wheel = b.cylinder(0.025, 0.02, M.rubber, Math.sin(a) * 0.3, 0.025, Math.cos(a) * 0.3, p, 12); wheel.rotation.z = Math.PI / 2;
  }
  for (const s of [-1, 1]) {
    b.box(0.03, 0.2, 0.03, M.chairFrame, s * 0.28, 0.63, 0.04, p, 0.006);
    b.box(0.07, 0.035, 0.3, M.rubber, s * 0.28, 0.74, 0.02, p, 0.012);
  }
  b.contact(0.9, 0.9, 0, 0, 0, 0.5, p);
}
