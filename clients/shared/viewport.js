// SpaceView: the 3D view shared by the ship, the spectator screen and the
// landing page. Built on three.js r187 (vendored, offline).
//
// Everything placed here is geometrically true for the in-game instant:
//  - scene axes are J2000 (z = celestial north), the frame SPICE uses;
//  - Sun, Earth and Mars sit in their real directions from the ship at their
//    real angular sizes, so Earth shrinks to a point as the gap grows;
//  - Earth turns by the real Earth Rotation Angle, so the night-side city
//    lights (NASA Black Marble) are the cities actually in darkness;
//  - Mars is oriented by the IAU pole and prime meridian;
//  - Meridian-1's high-gain antenna points at Earth and its arrays track the
//    Sun. In storm attitude the ship turns its tanks sunward and the dish
//    loses Earth, which is why the link drops;
//  - messages in flight are drawn as light packets travelling the beam.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { buildSpacecraft } from './spacecraft.js';

const ASSETS = '/shared/assets/';
const D_BODY = 2000, D_SUN = 3200, D_STARS = 20000;
// Blackbody colour for a star's effective temperature (K), as in room-fx.js.
function starColour(k) {
  const t = Math.max(2500, Math.min(30000, k)) / 100;
  const r = t <= 66 ? 255 : 329.7 * (t - 60) ** -0.1332, g = t <= 66 ? 99.47 * Math.log(t) - 161.1 : 288.1 * (t - 60) ** -0.0755;
  const b = t >= 66 ? 255 : t <= 19 ? 0 : 138.5 * Math.log(t - 10) - 305;
  return [r, g, b].map((v) => Math.max(0, Math.min(255, v)) / 255);
}
const R_EARTH = 6378.137, R_MARS = 3396.19;
import { KIND_COLORS } from './ll.js';

// Earth Rotation Angle (IAU 2000) and IAU Mars prime meridian, from ET.
const earthRotation = (et) => 2 * Math.PI * ((0.7790572732640 + 1.00273781191135448 * ((et - 69.184) / 86400)) % 1);
const marsW = (et) => THREE.MathUtils.degToRad((176.630 + 350.89198226 * (et / 86400)) % 360);

// ------------------------------------------------------------------ helpers


const EARTH_VS = `varying vec2 vUv; varying vec3 vN; varying vec3 vW;
void main(){ vUv = uv; vN = normalize(mat3(modelMatrix) * normal); vec4 w = modelMatrix * vec4(position,1.0); vW = w.xyz;
  gl_Position = projectionMatrix * viewMatrix * w; }`;
const EARTH_FS = `uniform sampler2D dayMap; uniform sampler2D nightMap; uniform sampler2D waterMap; uniform vec3 sunDir;
varying vec2 vUv; varying vec3 vN; varying vec3 vW;
void main(){
  vec3 N = normalize(vN), L = normalize(sunDir), V = normalize(cameraPosition - vW);
  float ndl = dot(N, L);
  vec3 day = texture2D(dayMap, vUv).rgb, night = texture2D(nightMap, vUv).rgb;
  float water = texture2D(waterMap, vUv).r;
  float lit = smoothstep(-0.06, 0.22, ndl);
  vec3 col = day * pow(max(ndl, 0.0), 0.8) * 2.3;
  col += night * night * (1.0 - lit) * vec3(1.0, 0.78, 0.48) * 2.2;                       // city lights on the night side
  vec3 H = normalize(L + V);
  col += vec3(1.0, 0.93, 0.8) * (pow(max(dot(N, H), 0.0), 900.0) * 0.5 + pow(max(dot(N, H), 0.0), 60.0) * 0.05) * water * smoothstep(0.0, 0.3, ndl);  // sun glint on oceans
  float fres = pow(1.0 - max(dot(N, V), 0.0), 2.5);
  col = mix(col, vec3(0.32, 0.58, 1.0), fres * smoothstep(-0.25, 0.6, ndl) * 0.55);       // atmospheric scattering at the limb
  gl_FragColor = vec4(col, 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;
// ------------------------------------------------------------------ the view
export class SpaceView {
  constructor(canvas, { mode = 'outside', labels = true, interactive = true, hero = false, shift = null } = {}) {
    this.shift = shift; this.canvas = canvas; this.mode = mode; this.look = 'auto'; this.hero = hero; this.interactive = interactive;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping; this.renderer.toneMappingExposure = 1.0;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x030405);
    this.reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.05, 30000); this.camera.up.set(0, 0, 1);
    this.controls = new OrbitControls(this.camera, canvas);
    this.lastTouch = -1e9; this.controls.addEventListener('start', () => (this.lastTouch = performance.now()));
    Object.assign(this.controls, { enableDamping: true, dampingFactor: 0.06, enablePan: false, minDistance: 6, maxDistance: 70, rotateSpeed: 0.6, enabled: interactive });
    this.sunLight = new THREE.DirectionalLight(0xfff8eb, 3.1);
    this.sunLight.castShadow = true;
    this.sunLight.shadow.mapSize.set(1024, 1024);
    Object.assign(this.sunLight.shadow.camera, { left: -9, right: 9, top: 9, bottom: -9, near: 0.5, far: 180 });
    this.sunLight.shadow.bias = -0.0002;
    this.scene.add(this.sunLight);
    this._stars(); this._sun(); this._earth(); this._mars(); this._beam();
    this.ship = new THREE.Group(); this.ship.userData = { wings: [], dishPos: new THREE.Vector3() };
    this.scene.add(this.ship);
    fetch('/api/catalog').then((r) => r.json()).then((c) => { this.catalog = c; this.configure(); }).catch(() => {});
    this.labelLayer = labels ? Object.assign(document.createElement('div'), { className: 'sv-labels' }) : null;
    if (this.labelLayer) { canvas.parentElement.append(this.labelLayer); this.lab = {}; }
    this.v = hero ? this._heroGeometry() : null;
    this.framed = false;
    new ResizeObserver(() => this.resize()).observe(canvas); this.resize();
    this.t0 = performance.now();
    this.renderer.setAnimationLoop(() => this.frame());
  }

  // The real sky: the Yale Bright Star Catalogue (J2000 unit vectors), the same
  // file the cockpit canopy draws. Scene axes are already J2000, so no rotation.
  // Sizes follow magnitude; crisp points, no glow. The shell rides with the
  // camera at D_STARS, behind the Sun (D_SUN) and inside the far plane.
  _stars() {
    this.starGroup = new THREE.Group(); this.starGroup.renderOrder = -10; this.scene.add(this.starGroup);
    fetch(ASSETS + 'stars_bsc5.json').then((r) => r.json()).then(({ stars }) => {
      const pos = [], col = [], size = [];
      for (const [x, y, z, mag, k] of stars) {
        if (mag > 6.0) continue;
        pos.push(x * D_STARS, y * D_STARS, z * D_STARS);
        const c = starColour(k), b = Math.min(1, 0.25 + 0.75 * 10 ** (-0.4 * (mag - 1.5)));
        col.push(c[0] * b, c[1] * b, c[2] * b); size.push(Math.max(1, 3.4 - mag * 0.45));
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
      geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
      geo.setAttribute('size', new THREE.Float32BufferAttribute(size, 1));
      const mat = new THREE.ShaderMaterial({
        vertexColors: true, depthWrite: false, toneMapped: false,
        vertexShader: 'attribute float size; varying vec3 vC; void main(){ vC = color; gl_PointSize = size * min(2.0, ' + (Math.min(2, devicePixelRatio || 1)).toFixed(2) + '); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
        fragmentShader: 'varying vec3 vC; void main(){ vec2 p = gl_PointCoord - 0.5; if (length(p) > 0.5) discard; gl_FragColor = vec4(vC, 1.0); }',
      });
      const pts = new THREE.Points(geo, mat); pts.frustumCulled = false; pts.renderOrder = -10; this.starGroup.add(pts);
    }).catch(() => {});
  }
  _sun() {
    this.sun = new THREE.Group();
    this.sunCore = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 16), new THREE.MeshBasicMaterial({ color: 0xfff8e5 }));
    this.sun.add(this.sunCore); this.scene.add(this.sun);
  }
  _earth() {
    const tl = new THREE.TextureLoader(), tex = (f, srgb = true) => { const t = tl.load(ASSETS + f); if (srgb) t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8; return t; };
    this.earthU = { dayMap: { value: tex('earth_day.jpg') }, nightMap: { value: tex('earth_night.jpg') }, waterMap: { value: tex('earth_water.jpg', false) }, sunDir: { value: new THREE.Vector3(1, 0, 0) } };
    this.earth = new THREE.Group();
    const body = new THREE.Mesh(new THREE.SphereGeometry(1, 128, 64), new THREE.ShaderMaterial({ vertexShader: EARTH_VS, fragmentShader: EARTH_FS, uniforms: this.earthU }));
    body.rotation.x = Math.PI / 2;                       // texture pole -> +Z; Greenwich -> +X (Earth-fixed frame)
    this.earthSpin = new THREE.Group(); this.earthSpin.add(body); this.earth.add(this.earthSpin);
    this.earthDot = this._dot(0x9cc8ff); this.scene.add(this.earth, this.earthDot);
  }
  _mars() {
    const t = new THREE.TextureLoader().load(ASSETS + 'mars.jpg'); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
    this.mars = new THREE.Group();
    const body = new THREE.Mesh(new THREE.SphereGeometry(1, 128, 64), new THREE.MeshStandardMaterial({ map: t, roughness: 0.95, metalness: 0 }));
    body.rotation.x = Math.PI / 2;
    this.marsSpin = new THREE.Group(); this.marsSpin.add(body);
    // orient by the IAU pole: RA 317.68 deg, Dec 52.89 deg
    const ra = THREE.MathUtils.degToRad(317.68143), de = THREE.MathUtils.degToRad(52.8865);
    const pole = new THREE.Vector3(Math.cos(de) * Math.cos(ra), Math.cos(de) * Math.sin(ra), Math.sin(de));
    this.marsFrame = new THREE.Group(); this.marsFrame.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), pole);
    this.marsFrame.add(this.marsSpin); this.mars.add(this.marsFrame);
    this.marsHaloU = { color: { value: new THREE.Color(0.95, 0.62, 0.42) }, sunDir: { value: new THREE.Vector3(1, 0, 0) }, strength: { value: 0.55 } };
    this.marsDot = this._dot(0xffa477); this.scene.add(this.mars, this.marsDot);
  }
  _dot(color) {
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(3), 3));
    const p = new THREE.Points(g, new THREE.PointsMaterial({ color: new THREE.Color(color).multiplyScalar(2.2), size: 4, sizeAttenuation: false, transparent: true, depthWrite: false }));
    p.frustumCulled = false; return p;
  }
  _beam() {
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(6), 3));
    this.beam = new THREE.Line(g, new THREE.LineBasicMaterial({ color: 0x86d0f5, transparent: true, opacity: 0.22, depthWrite: false }));
    this.beam.frustumCulled = false;
    const pg = new THREE.BufferGeometry(); this.pktPos = new Float32Array(64 * 3); this.pktCol = new Float32Array(64 * 3);
    pg.setAttribute('position', new THREE.BufferAttribute(this.pktPos, 3)); pg.setAttribute('color', new THREE.BufferAttribute(this.pktCol, 3));
    this.packets = new THREE.Points(pg, new THREE.PointsMaterial({ size: 7, sizeAttenuation: false, vertexColors: true, transparent: true, depthWrite: false, blending: THREE.NormalBlending }));
    this.packets.frustumCulled = false; this.scene.add(this.beam, this.packets);
  }
  _heroGeometry() {
    return { et: 0, viewport: { earth: { dir: [0.96, 0.27, 0.0], dist_km: 26000, ang_diam_deg: 28 }, mars: { dir: [-0.2, 0.9, 0.3], dist_km: 3e8, ang_diam_deg: 0.001 },
      sun: { dir: [-0.62, 0.76, 0.18], dist_km: 1.5e8, ang_diam_deg: 0.53 } }, in_flight: [], attitude: false, link: { uplink_open: true } };
  }

  // ---------------------------------------------------------------- input
  update(s) {
    if (s?.vehicle_design) { this.design = s.vehicle_design; this.configure(); }
    if (!s?.geometry || this.hero) return;
    const prev = this.v;
    this.v = { et: s.et, viewport: s.geometry.viewport, phase: s.geometry.ship_phase, lt: s.geometry.lt_up_s, in_flight: s.comms?.in_flight || [],
      attitude: !!(s.ship?.attitude ?? s.control?.attitude), link: s.comms?.link || {}, receivedAt: performance.now(), rate: s.rate || 0 };
    if (!prev || prev.phase !== this.v.phase) this.framed = false;
  }
  configure() {
    const d = this.design, cat = this.catalog;
    if (!d || !cat || !Number.isFinite(d.habitat_m3) || !Number.isFinite(d.crew)) return;
    const key = JSON.stringify([d.array, d.payload, d.shelter_gcm2, d.hull_liner_gcm2, d.solar_monitor, d.crew, d.habitat_m3, d.comms_plan]);
    if (key === this.designKey) return;
    this.designKey = key;
    this.scene.remove(this.ship);
    const materials = new Set();
    this.ship.traverse((node) => { node.geometry?.dispose(); if (node.material) materials.add(node.material); });
    materials.forEach((m) => m.dispose());
    this.ship = buildSpacecraft(d, cat); this.scene.add(this.ship); this.framed = false;
    const array = cat.solar_arrays.find((a) => a.id === d.array);
    const instrumentNames = (d.payload || []).map((id) => cat.instruments.find((i) => i.id === id)?.name).filter(Boolean);
    const text = `${d.crew} crew · ${array?.name || d.array} · ${instrumentNames.length} instruments · shelter ${d.shelter_gcm2} g/cm² · ${d.comms_plan} radio`;
    this.canvas.setAttribute('aria-label', `MODEL spacecraft: ${text}. Schematic geometry, actual mission Sun direction.`);
    this.canvas.dispatchEvent(new CustomEvent('vehicle-configured', { detail: { text, instruments: instrumentNames } }));
  }
  setMode(mode) { this.mode = mode; this.framed = false; }
  /** The slow turning-Earth view used before launch; switch it off to follow the mission. */
  setHero(on) {
    if (on === this.hero) return;
    this.hero = on; this.framed = false; this.glide = null;
    if (on) this.v = this._heroGeometry();
    else { this.v = null; this.ship.visible = true; this.beam.visible = true; this.controls.enabled = this.interactive; }
  }
  setLook(target) { this.look = target; this.framed = false; this.glide = null; }

  // Phase 6, the TMI burn on the spectator: the ship as designed, near Earth (the
  // illustrative pre-launch geometry), with an engine-glow pulse at the nozzle.
  burnPose(vehicle, lt) {
    this.design = vehicle; this.configure();
    this.hero = false; this.framed = false; this.glide = null; this.look = 'auto';
    this.v = { ...this._heroGeometry(), phase: 'tmi', lt: lt || 0, receivedAt: performance.now(), rate: 0 };
    this.ship.visible = true; this.beam.visible = true;
  }
  burnGlow(level) {
    if (!this.glow) {
      const c = document.createElement('canvas'); c.width = c.height = 128; const g = c.getContext('2d');
      const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
      grad.addColorStop(0, 'rgba(255,244,220,1)'); grad.addColorStop(0.2, 'rgba(255,176,90,0.85)'); grad.addColorStop(0.55, 'rgba(255,110,40,0.25)'); grad.addColorStop(1, 'rgba(0,0,0,0)');
      g.fillStyle = grad; g.fillRect(0, 0, 128, 128);
      const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
      this.glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false }));
      this.glowLight = new THREE.PointLight(0xffa35a, 0, 12, 1.6);
    }
    if (this.glow.parent !== this.ship) { this.ship.add(this.glow, this.glowLight); }
    const p = this.ship.userData.nozzle || new THREE.Vector3(0, 0, -4);
    this.glow.position.copy(p); this.glowLight.position.copy(p).add(new THREE.Vector3(0, 0, -0.6));
    const k = Math.max(0, level || 0);
    this.glow.visible = k > 0.01; this.glow.scale.setScalar(0.6 + 2.6 * k); this.glow.material.opacity = Math.min(1, k);
    this.glowLight.intensity = 14 * k;
  }
  resetView() { this.framed = false; }

  resize() {
    const r = this.canvas.getBoundingClientRect(); if (!r.width || !r.height) return;
    this.renderer.setPixelRatio(Math.min(2, devicePixelRatio || 1));
    this.renderer.setSize(r.width, r.height, false);
    this.camera.aspect = r.width / r.height;
    const sh = this.shift?.() || 0;          // slide the picture sideways, e.g. to make room for text
    if (sh) this.camera.setViewOffset(r.width, r.height, -r.width * sh, 0, r.width, r.height); else this.camera.clearViewOffset();
    this.camera.updateProjectionMatrix();
  }

  // ---------------------------------------------------------------- frame
  frame() {
    if (!this.canvas.clientWidth || !this.canvas.clientHeight) return;
    this.starGroup.position.copy(this.camera.position);
    const v = this.v, now = performance.now();
    if (!v) { this.renderer.render(this.scene, this.camera); return; }
    const vp = v.viewport, V3 = (a) => new THREE.Vector3(a[0], a[1], a[2]).normalize();
    // advance the clock smoothly between 4 Hz snapshots (bodies rotate at the universe rate)
    const et = this.hero ? 0.43 * 86400 : v.et + Math.max(0, Math.min(0.5, (now - (v.receivedAt || now)) / 1000)) * (v.rate || 0);
    const sunDir = V3(vp.sun.dir), earthDir = V3(vp.earth.dir), marsDir = V3(vp.mars.dir);
    this.sunLight.position.copy(sunDir).multiplyScalar(100);
    this.earthU.sunDir.value.copy(sunDir); this.marsHaloU.sunDir.value.copy(sunDir);
    // place bodies at true angular size (each on its own distance scale; only directions and sizes matter from here)
    const place = (grp, dot, dir, Rtrue, dist) => {
      const scale = D_BODY / Math.max(dist, 1);
      grp.position.copy(dir).multiplyScalar(D_BODY); grp.scale.setScalar(Math.min(Rtrue * scale, D_BODY * 0.95));
      dot.geometry.attributes.position.array.set([grp.position.x, grp.position.y, grp.position.z]); dot.geometry.attributes.position.needsUpdate = true;
      const px = (Rtrue / dist) * 2 * (this.canvas.clientHeight / (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2))));
      dot.visible = px < 3; dot.material.opacity = Math.min(1, 0.35 + 0.65 * (3 - Math.max(px, 0)) / 3);
      return px;
    };
    this.earthPx = place(this.earth, this.earthDot, earthDir, R_EARTH, vp.earth.dist_km);
    this.marsPx = place(this.mars, this.marsDot, marsDir, R_MARS, vp.mars.dist_km);
    this.earthSpin.rotation.z = earthRotation(et);
    this.marsSpin.rotation.z = marsW(et);
    const sunR = Math.max(D_SUN * Math.tan(THREE.MathUtils.degToRad(vp.sun.ang_diam_deg / 2)), 6);
    this.sun.position.copy(sunDir).multiplyScalar(D_SUN); this.sunCore.scale.setScalar(sunR);
    // ship attitude: dish to Earth, or tanks to the Sun during a storm
    const m = new THREE.Matrix4(), fwd = v.attitude ? sunDir.clone().negate() : earthDir;
    const up = Math.abs(fwd.dot(sunDir)) > 0.95 ? new THREE.Vector3(0, 0, 1) : sunDir;
    m.lookAt(fwd, new THREE.Vector3(), up); const target = new THREE.Quaternion().setFromRotationMatrix(m);   // ship +Z along fwd
    this.ship.quaternion.slerp(target, this.framed && !this.reducedMotion ? 0.02 : 1);
    const sunLocal = sunDir.clone().applyQuaternion(this.ship.quaternion.clone().invert());
    const wingAngle = Math.atan2(sunLocal.z, sunLocal.y);
    for (const w of this.ship.userData.wings) w.rotation.x = wingAngle;
    this.ship.visible = this.mode === 'outside';
    // the beam and the packets on it
    const dish = this.ship.userData.dishPos.clone().applyQuaternion(this.ship.quaternion);
    const earthEdge = earthDir.clone().multiplyScalar(D_BODY - this.earth.scale.x * 1.05);
    const b = this.beam.geometry.attributes.position.array; b.set([dish.x, dish.y, dish.z, earthEdge.x, earthEdge.y, earthEdge.z]); this.beam.geometry.attributes.position.needsUpdate = true;
    const linked = v.link.uplink_open || v.link.downlink_open;
    this.beam.material.color.set(v.link.blackout ? getComputedStyle(this.canvas).getPropertyValue('--alarm').trim() : linked ? KIND_COLORS.command : KIND_COLORS.alert);
    this.beam.material.opacity = v.attitude ? 0.0 : v.link.blackout ? 0.08 : linked ? 0.24 : 0.12;
    let n = 0;
    for (const f of v.in_flight.slice(0, 64)) {
      const frac = Math.min(1, f.frac + Math.max(0, Math.min(0.5, (now - v.receivedAt) / 1000)) * (v.rate || 0) / Math.max(f.arrive_et - f.tx_et, 1));
      const t = f.direction === 'up' ? 1 - frac : frac;           // 0 = at the ship, 1 = at Earth
      const p = dish.clone().lerp(earthEdge, t);    // linear propagation, no visual acceleration
      this.pktPos.set([p.x, p.y, p.z], n * 3);
      const c = new THREE.Color(KIND_COLORS[f.kind] ?? 0xdce7ef).multiplyScalar(1);
      this.pktCol.set([c.r, c.g, c.b], n * 3); n++;
    }
    this.packets.geometry.setDrawRange(0, n);
    this.packets.geometry.attributes.position.needsUpdate = true; this.packets.geometry.attributes.color.needsUpdate = true;
    // camera
    const focus = this.look === 'auto' ? (v.phase === 'mars_orbit' ? 'mars' : 'earth') : this.look;
    const focusDir = focus === 'mars' ? marsDir : focus === 'sun' ? sunDir : earthDir;
    if (this.mode === 'window') {
      this.controls.enabled = false;
      this.camera.position.set(0, 0, 0); this.camera.fov = 38; this.camera.updateProjectionMatrix();
      this.camera.lookAt(focusDir.clone().multiplyScalar(1000));
    } else {
      // the geometry drifts as the mission moves on (and jumps across time skips): if the target body has
      // wandered far from where we framed it, and nobody has touched the camera lately, glide back
      const drifted = this.frameDir && this.frameDir.angleTo(focusDir) > 0.12 && now - this.lastTouch > 20000;
      if (!this.framed || drifted) {
        // Vehicle view faces the actually sunlit side; the light itself never
        // follows the camera. Explicit body views retain the true sightline.
        let perp = sunDir.clone().sub(focusDir.clone().multiplyScalar(sunDir.dot(focusDir)));
        if (perp.length() < 0.1) perp = new THREE.Vector3().crossVectors(focusDir, new THREE.Vector3(0, 0, 1));
        const dir = this.look === 'auto' ? sunDir.clone().add(perp.normalize().multiplyScalar(0.65)).normalize()
          : focusDir.clone().negate().add(perp.normalize().multiplyScalar(0.19)).normalize();
        this.camera.fov = 40; this.camera.updateProjectionMatrix();
        // aim a little past the ship toward the target, so both share the frame
        const tgt = this.look === 'auto' ? new THREE.Vector3() : focusDir.clone().multiplyScalar(3);
        const pos = tgt.clone().addScaledVector(dir, this.look === 'auto' ? 13 : 19);
        if (this.framed && !this.reducedMotion) this.glide = { tgt, pos }; else { this.controls.target.copy(tgt); this.camera.position.copy(pos); }
        this.frameDir = focusDir.clone();
      }
      if (this.glide) {
        if (now - this.lastTouch < 500) this.glide = null;
        else {
          const k = 1 - Math.exp(-Math.min(0.25, (now - (this.lastFrame || now)) / 1000) / 0.7);   // ~0.7 s ease, whatever the frame rate
          this.controls.target.lerp(this.glide.tgt, k); this.camera.position.lerp(this.glide.pos, k);
          if (this.camera.position.distanceTo(this.glide.pos) < 0.05) this.glide = null;
        }
      }
      this.controls.enabled = this.interactive && !this.hero;
      if (this.hero) {
        const a = 0.2;
        this.camera.position.set(Math.cos(a) * 3, Math.sin(a) * 3, 0.6); this.camera.lookAt(earthDir.clone().multiplyScalar(D_BODY));
        this.ship.visible = false; this.beam.visible = false;
      } else this.controls.update();
    }
    this.lastFrame = now;
    this.framed = true;
    this.renderer.render(this.scene, this.camera);
    this._labels(earthDir, marsDir, v);
  }

  _labels(earthDir, marsDir, v) {
    if (!this.labelLayer || this.hero) return;
    const W = this.canvas.clientWidth, H = this.canvas.clientHeight;
    const put = (key, worldPos, html, show) => {
      let el = this.lab[key];
      if (!el) { el = this.lab[key] = document.createElement('div'); el.className = 'sv-label'; this.labelLayer.append(el); }
      const p = worldPos.clone().project(this.camera);
      const vis = show && p.z < 1 && Math.abs(p.x) < 1.05 && Math.abs(p.y) < 1.05;
      el.style.display = vis ? 'block' : 'none';
      if (vis) { el.style.transform = `translate(${(p.x * 0.5 + 0.5) * W + 8}px, ${(-p.y * 0.5 + 0.5) * H - 16}px)`; if (el._h !== html) { el.innerHTML = html; el._h = html; } }
    };
    const lt = v.lt || 0, ltTxt = lt < 120 ? `${lt.toFixed(1)} s` : `${Math.floor(lt / 60)} min ${String(Math.round(lt % 60)).padStart(2, '0')} s`;
    // labels sit just right of each body's disc, whatever its apparent size
    const side = (grp) => { const right = new THREE.Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion); return grp.position.clone().addScaledVector(right, grp.scale.x * 1.15); };
    put('earth', side(this.earth), `Earth <span>${ltTxt} away by light</span>`, true);
    put('mars', side(this.mars), `Mars`, this.marsPx < 600);
  }
}
