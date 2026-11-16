// Illustrative MODEL interiors. Geometry describes the game space, not a sourced
// reconstruction of a NASA facility or a flight-qualified pressure vessel.
//
// Shared kit: materials, procedural canvas textures and small builders used by
// room-earth.js (mission operations) and room-cabin.js (Meridian flight deck).
// Everything is generated in code: no downloaded models, images or HDRIs.
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { RectAreaLightUniformsLib } from 'three/addons/lights/RectAreaLightUniformsLib.js';
import { buildEarth } from './room-earth.js';
import { buildCabin } from './room-cabin.js';

let rectLib = false;

// --- Procedural textures -------------------------------------------------
function canvasTexture(w, h, paint, repeat) {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  paint(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(...repeat); }
  t.anisotropy = 4; return t;
}

// Deterministic noise so captures are reproducible.
function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

function speckle(g, w, h, base, spread, count, seed = 7) {
  g.fillStyle = base; g.fillRect(0, 0, w, h); const r = rng(seed);
  for (let i = 0; i < count; i++) {
    const v = (r() - 0.5) * spread; g.fillStyle = v > 0 ? `rgba(255,255,255,${v})` : `rgba(0,0,0,${-v})`;
    g.fillRect(r() * w, r() * h, 1 + r() * 2, 1 + r() * 2);
  }
}

export const textures = {
  carpet: () => canvasTexture(256, 256, (g, w, h) => speckle(g, w, h, '#4a4e52', 0.22, 9000, 11), [10, 10]),
  fabric: () => canvasTexture(256, 512, (g, w, h) => {
    speckle(g, w, h, '#5a5d60', 0.12, 6000, 3);
    g.fillStyle = 'rgba(0,0,0,0.55)'; g.fillRect(0, 0, 3, h); g.fillRect(0, h - 3, w, 3); // panel seams
  }, [12, 2]),
  brushed: () => canvasTexture(256, 64, (g, w, h) => {
    g.fillStyle = '#b4b4b4'; g.fillRect(0, 0, w, h); const r = rng(5);
    for (let i = 0; i < 260; i++) { g.fillStyle = `rgba(${r() > 0.5 ? '255,255,255' : '0,0,0'},${r() * 0.12})`; g.fillRect(0, r() * h, w, 1); }
  }, [2, 1]),
  padded: () => canvasTexture(512, 512, (g, w, h) => {
    speckle(g, w, h, '#e8e5de', 0.06, 5000, 21);
    // Quilted Nomex-style panels: seams, stitching and a few velcro patches.
    g.strokeStyle = 'rgba(70,66,60,0.55)'; g.lineWidth = 4; g.strokeRect(2, 2, w - 4, h - 4);
    g.strokeStyle = 'rgba(70,66,60,0.25)'; g.lineWidth = 1; g.setLineDash([6, 6]); g.strokeRect(18, 18, w - 36, h - 36); g.setLineDash([]);
  }, [1, 1]),
  rack: () => canvasTexture(256, 256, (g, w, h) => {
    g.fillStyle = '#6b7074'; g.fillRect(0, 0, w, h);
    g.strokeStyle = 'rgba(0,0,0,0.45)'; g.lineWidth = 3; g.strokeRect(1, 1, w - 2, h - 2);
    g.fillStyle = 'rgba(0,0,0,0.5)'; for (const [x, y] of [[14, 14], [w - 14, 14], [14, h - 14], [w - 14, h - 14]]) { g.beginPath(); g.arc(x, y, 5, 0, 7); g.fill(); }
    g.fillStyle = 'rgba(255,255,255,0.12)'; g.fillRect(24, h - 52, w - 48, 22);
  }, [1, 1]),
};

// Soft contact shadow: a radial falloff under objects so nothing floats.
let shadowTexture;
function contactTexture() {
  return shadowTexture ||= canvasTexture(128, 128, (g, w, h) => {
    const grad = g.createRadialGradient(w / 2, h / 2, 4, w / 2, h / 2, w / 2);
    grad.addColorStop(0, 'rgba(0,0,0,0.85)'); grad.addColorStop(0.55, 'rgba(0,0,0,0.35)'); grad.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = grad; g.fillRect(0, 0, w, h);
  });
}

const std = (color, roughness = 0.8, metalness = 0, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness, metalness, ...extra });

export class RoomBuilder {
  constructor(scene, screen, control, gauge, renderer, extras = {}) {
    this.scene = scene; this.screen = screen; this.control = control; this.gauge = gauge; this.renderer = renderer;
    this.extras = extras; // { ambient(type) -> material }
    this.lamps = []; this.blinkers = []; this.alertLights = []; this.chase = [];
    this.m = {
      // Shared hardware.
      bezel: std(0x0d0f10, 0.55), plastic: std(0x26292c, 0.6), rubber: std(0x151719, 0.95),
      keycap: std(0x2c3034, 0.55), keycapLight: std(0x8d9092, 0.6), metal: std(0x9a9c9e, 0.35, 0.85),
      steel: std(0x5d6164, 0.4, 0.8), paper: std(0xd8d2c4, 0.95), black: std(0x060708, 0.9),
      cable: std(0x101213, 0.7), glass: new THREE.MeshPhysicalMaterial({ color: 0x0a0d10, roughness: 0.05, metalness: 0, transmission: 0, clearcoat: 1 }),
      // Earth: the dark room.
      fabric: std(0x2a2c2f, 0.97, 0, { map: textures.fabric() }), carpet: std(0x2f3336, 1, 0, { map: textures.carpet() }),
      ceiling: std(0x101112, 0.95), graphite: std(0x2c3034, 0.55, 0.15), worktop: std(0x141617, 0.42, 0.05),
      trim: std(0xa08b6c, 0.32, 0.85, { map: textures.brushed() }), chair: std(0x22262a, 0.92), chairFrame: std(0x3b3f43, 0.4, 0.7),
      // Ship: lived-in pressure vessel.
      padded: std(0xe9e6df, 0.92, 0, { map: textures.padded() }), hull: std(0xd6d3cc, 0.85), rack: std(0x8a8f93, 0.7, 0.2, { map: textures.rack() }),
      handrail: std(0x5f7686, 0.45, 0.35), bag: std(0xdcd8cd, 0.98), strap: std(0x2e3236, 0.9), couch: std(0x34404a, 0.85),
      panelGray: std(0x34393d, 0.6, 0.15), panelBlack: std(0x16191b, 0.7),
    };
    if (renderer) {
      const pmrem = new THREE.PMREMGenerator(renderer);
      scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture; pmrem.dispose();
    }
    if (!rectLib) { RectAreaLightUniformsLib.init(); rectLib = true; }
  }

  earth(seat) { return buildEarth(this, seat); }
  cabin(seat) { return buildCabin(this, seat); }

  // --- Primitive helpers ---------------------------------------------------
  group(x = 0, y = 0, z = 0, parent = this.scene, ry = 0, rx = 0) {
    const g = new THREE.Group(); g.position.set(x, y, z); g.rotation.set(rx, ry, 0); parent.add(g); return g;
  }

  // Rounded edges everywhere: manufactured parts catch light on their chamfers.
  box(w, h, d, m, x = 0, y = 0, z = 0, parent = this.scene, radius) {
    const r = radius ?? Math.min(0.03, w / 6, h / 6, d / 6);
    const geometry = r > 0.004 && Math.max(w, h, d) < 14 ? new RoundedBoxGeometry(w, h, d, 2, r) : new THREE.BoxGeometry(w, h, d);
    const mesh = new THREE.Mesh(geometry, m); mesh.position.set(x, y, z);
    mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh); return mesh;
  }

  slab(w, h, d, m, x = 0, y = 0, z = 0, parent = this.scene) { return this.box(w, h, d, m, x, y, z, parent, 0); }

  cylinder(r, h, m, x = 0, y = 0, z = 0, parent = this.scene, segments = 24, rTop = r) {
    const mesh = new THREE.Mesh(new THREE.CylinderGeometry(rTop, r, h, segments), m); mesh.position.set(x, y, z);
    mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh); return mesh;
  }

  tube(points, radius, m, parent = this.scene, segments = 48) {
    const curve = new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p)));
    const mesh = new THREE.Mesh(new THREE.TubeGeometry(curve, segments, radius, 8, false), m);
    mesh.castShadow = true; parent.add(mesh); return mesh;
  }

  contact(w, d, x, y, z, opacity = 0.55, parent = this.scene) {
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshBasicMaterial({ map: contactTexture(), transparent: true, opacity, depthWrite: false }));
    mesh.rotation.x = -Math.PI / 2; mesh.position.set(x, y + 0.004, z); mesh.renderOrder = 1; parent.add(mesh); return mesh;
  }

  // Engraved placard: sentence-case label on a plate.
  plate(text, w, h, x, y, z, { color = '#d6d4cc', background = '#1f2225', parent = this.scene, size = 30, align = 'center', font = 'B612' } = {}) {
    const c = document.createElement('canvas'); c.width = 768; c.height = Math.max(32, Math.round(768 * h / w));
    const g = c.getContext('2d'); g.fillStyle = background; g.fillRect(0, 0, c.width, c.height);
    g.fillStyle = color; g.font = `400 ${size}px ${font}, sans-serif`; g.textAlign = align; g.textBaseline = 'middle';
    g.fillText(text, align === 'center' ? c.width / 2 : 18, c.height / 2, c.width - 28);
    const texture = new THREE.CanvasTexture(c); texture.colorSpace = THREE.SRGBColorSpace; texture.anisotropy = 4;
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshStandardMaterial({ map: texture, roughness: 0.8 }));
    mesh.position.set(x, y, z); parent.add(mesh); return mesh;
  }

  // Emissive lamp (annunciator, indicator, LED strip). Not a light source.
  lamp(w, h, color, x, y, z, parent = this.scene, intensity = 1) {
    const material = new THREE.MeshBasicMaterial({ color: new THREE.Color(color).multiplyScalar(intensity), toneMapped: false });
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), material); mesh.position.set(x, y, z); parent.add(mesh); return mesh;
  }

  // Screen light spilling onto nearby surfaces.
  spill(w, h, color, intensity, position, lookAt) {
    const light = new THREE.RectAreaLight(color, intensity, w, h);
    light.position.set(...position); light.lookAt(...lookAt); this.scene.add(light); return light;
  }

  spot(color, intensity, position, target, { angle = 0.5, penumbra = 0.7, shadow = false } = {}) {
    const s = new THREE.SpotLight(color, intensity, 0, angle, penumbra, 2);
    s.position.set(...position); s.target.position.set(...target); this.scene.add(s, s.target);
    if (shadow) { s.castShadow = true; s.shadow.mapSize.set(1024, 1024); s.shadow.bias = -0.0004; s.shadow.normalBias = 0.02; s.shadow.radius = 4; }
    return s;
  }

  // Keyboard with a realistic row layout, instanced keycaps.
  keyboard(x, y, z, parent = this.scene, ry = 0) {
    const group = this.group(x, y, z, parent, ry);
    const U = 0.0185, gap = 0.0025;
    const rows = [
      [1, 0.5, 1, 1, 1, 1, 0.5, 1, 1, 1, 1, 0.5, 1, 1, 1, 1],
      [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2],
      [1.5, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1.5],
      [1.75, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2.25],
      [2.25, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2.75],
      [1.25, 1.25, 1.25, 6.25, 1.25, 1.25, 1.25, 1.25],
    ];
    const width = 15 * U;
    this.box(width + 0.024, 0.018, rows.length * U + 0.03, this.m.plastic, 0, 0, 0, group, 0.006);
    const geo = new RoundedBoxGeometry(U - gap, 0.009, U - gap, 1, 0.002);
    const count = rows.flat().filter((k) => k !== 0.5).length;
    const keys = new THREE.InstancedMesh(geo, this.m.keycap, count); let i = 0; const matrix = new THREE.Matrix4();
    rows.forEach((row, r) => {
      let cx = -width / 2;
      for (const k of row) {
        if (k === 0.5) { cx += U * 0.5; continue; }
        const scale = new THREE.Matrix4().makeScale(k, 1, 1);
        matrix.makeTranslation(cx + (k * U) / 2, 0.013 - r * 0.0012, (r - 2.5) * U).multiply(scale);
        keys.setMatrixAt(i++, matrix); cx += k * U;
      }
    });
    keys.castShadow = true; group.add(keys);
    return group;
  }

  trackball(x, y, z, parent = this.scene) {
    const g = this.group(x, y, z, parent);
    this.box(0.11, 0.03, 0.13, this.m.plastic, 0, 0.0, 0, g, 0.012);
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.024, 24, 16), std(0x7f2f2a, 0.25)); ball.position.set(0, 0.026, -0.01); g.add(ball);
    return g;
  }

  mug(x, y, z, parent = this.scene) {
    const g = this.group(x, y, z, parent);
    this.cylinder(0.04, 0.1, std(0xd9d6cf, 0.5), 0, 0.05, 0, g, 24, 0.042);
    const handle = new THREE.Mesh(new THREE.TorusGeometry(0.025, 0.007, 8, 16, Math.PI), std(0xd9d6cf, 0.5));
    handle.rotation.z = -Math.PI / 2; handle.position.set(0.045, 0.05, 0); g.add(handle);
    const coffee = new THREE.Mesh(new THREE.CircleGeometry(0.036, 24), std(0x2a170e, 0.2)); coffee.rotation.x = -Math.PI / 2; coffee.position.y = 0.092; g.add(coffee);
    return g;
  }

  headset(x, y, z, parent = this.scene, ry = 0) {
    const g = this.group(x, y, z, parent, ry);
    const band = new THREE.Mesh(new THREE.TorusGeometry(0.075, 0.008, 8, 24, Math.PI), this.m.rubber); g.add(band);
    for (const s of [-1, 1]) { const cup = this.cylinder(0.034, 0.03, this.m.rubber, s * 0.075, -0.005, 0, g, 20); cup.rotation.z = Math.PI / 2; }
    this.tube([[0.075, -0.02, 0], [0.04, -0.07, 0.03], [0.01, -0.09, 0.05]], 0.003, this.m.rubber, g, 12);
    return g;
  }

  // Decorative "mock cluster" display: animated traces with no numeric readouts.
  ambient(type, w, h, parent, x, y, z) {
    const material = this.extras.ambient ? this.extras.ambient(type) : this.m.black;
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), material); mesh.position.set(x, y, z); parent.add(mesh); return mesh;
  }

  // A row of decorative toggles with a few status lamps; lamps join the burst blinkers.
  mockSwitches(parent, n, x0, y, dx, z) {
    const r = (i) => (Math.sin(i * 12.9898 + y * 78.233 + x0 * 3.1) * 43758.5453) % 1;
    const nut = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.012, 0.012, 0.01, 6), this.m.metal, n);
    const lever = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.004, 0.003, 0.034, 8), this.m.steel, n);
    const m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler();
    for (let i = 0; i < n; i++) {
      const x = x0 + i * dx, up = Math.abs(r(i)) > 0.45;
      m.compose(new THREE.Vector3(x, y, z + 0.005), q.setFromEuler(e.set(Math.PI / 2, 0, 0)), new THREE.Vector3(1, 1, 1)); nut.setMatrixAt(i, m);
      m.compose(new THREE.Vector3(x, y + (up ? 0.006 : -0.006), z + 0.02), q.setFromEuler(e.set(Math.PI / 2 + (up ? -0.45 : 0.45), 0, 0)), new THREE.Vector3(1, 1, 1)); lever.setMatrixAt(i, m);
      if (i % 3 === 1) this.blinkers.push({ mesh: this.lamp(0.012, 0.012, 0x1b1d1f, x + dx * 0.4, y + 0.03, z + 0.002, parent, 1), on: Math.abs(r(i + 7)) > 0.4 ? 0x5fae6a : 0x1b1d1f, alt: 0xe0a43a });
    }
    nut.castShadow = lever.castShadow = true; parent.add(nut, lever);
    this.plate('', n * dx, 0.012, x0 + (n - 1) * dx / 2, y - 0.04, z + 0.001, { parent, background: '#2a2e31' });
  }

  // Square lit pushbuttons (decorative); they flash during activity bursts.
  mockButtons(parent, n, x0, y, dx, z) {
    const colours = [0x5fae6a, 0xe0a43a, 0x7fb3c2, 0xd9d4c6];
    for (let i = 0; i < n; i++) {
      const x = x0 + i * dx;
      this.box(0.05, 0.04, 0.02, this.m.panelGray, x, y, z, parent, 0.005);
      this.blinkers.push({ mesh: this.lamp(0.04, 0.03, 0x1b1d1f, x, y, z + 0.0105, parent, 1), on: i % 2 ? colours[i % 4] : 0x1b1d1f, alt: colours[(i + 1) % 4] });
    }
  }

  binder(x, y, z, parent = this.scene, ry = 0) {
    const g = this.group(x, y, z, parent, ry);
    this.box(0.24, 0.035, 0.31, std(0x3d4a56, 0.8), 0, 0.017, 0, g, 0.004);
    this.box(0.225, 0.028, 0.295, this.m.paper, 0.006, 0.018, 0, g, 0.002);
    return g;
  }
}
