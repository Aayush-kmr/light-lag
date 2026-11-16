// MODEL geometry, deliberately schematic. Net cabin volume, array rating,
// areal shielding and selected instruments come from the mission/catalog.
// Visual proportions are not a flight-qualified pressure vessel or area model.
import * as THREE from 'three';

export function buildSpacecraft(d, catalog) {
  const ship = new THREE.Group();
  const material = (color) => new THREE.MeshStandardMaterial({ color, roughness: 1, metalness: 0, flatShading: true });
  const hull = material(0xb5b7b1), service = material(0x8f8b7c), frame = material(0x444a4c);
  const shielding = material(0x929b93), cell = material(0x26333e), sensor = material(0x373b3a);
  const add = (geo, mat, x = 0, y = 0, z = 0, parent = ship) => {
    const mesh = new THREE.Mesh(geo, mat); mesh.position.set(x, y, z);
    mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh); return mesh;
  };
  const cylinder = (r1, r2, length, segments = 16) => new THREE.CylinderGeometry(r1, r2, length, segments).rotateX(Math.PI / 2);
  // A fixed 3:1 cylinder convention encodes the actual MODEL habitable volume.
  const radius = Math.cbrt(d.habitat_m3 / (3 * Math.PI)) * 0.24;
  const length = 3 * radius;
  add(cylinder(radius, radius, length), hull);
  add(cylinder(radius * 0.48, radius, radius * 0.48), hull, 0, 0, length / 2 + radius * 0.24);
  add(new THREE.TorusGeometry(radius * 0.48, 0.04, 8, 24), frame, 0, 0, length / 2 + radius * 0.5);
  const rear = -length / 2 - 0.55;
  add(cylinder(radius * 0.9, radius * 0.9, 1), service, 0, 0, rear);
  for (const side of [-1, 1]) add(new THREE.SphereGeometry(radius * 0.5, 12, 8), hull, side * radius * 0.7, 0, rear - 0.85);
  add(cylinder(0.14, 0.36, 0.64), frame, 0, 0, rear - 1.5);
  // Rings encode areal density. They do not claim a material thickness.
  if (d.shelter_gcm2 > 0) {
    const layer = radius + 0.02 + Math.sqrt(d.shelter_gcm2) * 0.025;
    add(cylinder(layer, layer, length * 0.32), shielding, 0, 0, -length * 0.13);
    for (const z of [-length * 0.29, length * 0.03]) add(new THREE.TorusGeometry(layer, 0.025, 6, 24), frame, 0, 0, z);
  }
  if (d.hull_liner_gcm2 > 0) {
    const liner = radius + 0.02 + Math.sqrt(d.hull_liner_gcm2) * 0.018;
    for (const z of [-length * 0.4, length * 0.4]) add(new THREE.TorusGeometry(liner, 0.04, 6, 24), shielding, 0, 0, z);
  }
  for (let i = 0; i < d.crew; i++) {
    const angle = 2 * Math.PI * i / d.crew;
    const w = add(new THREE.BoxGeometry(0.16, 0.025, 0.2), sensor, Math.sin(angle) * radius, Math.cos(angle) * radius, length * 0.3);
    w.rotation.z = -angle;
  }
  for (const side of [-1, 1]) add(new THREE.BoxGeometry(0.025, 1.3, 0.75), hull, side * (radius + 0.08), side * 0.5, 0);
  const array = catalog.solar_arrays.find((a) => a.id === d.array);
  const wings = [];
  if (array) {
    // Rating, not an invented square-metre area, controls the schematic span.
    const span = Math.sqrt(array.kw_at_1au) * 0.48;
    add(new THREE.BoxGeometry(2 * (radius + span), 0.06, 0.06), frame, 0, 0, rear);
    for (const side of [-1, 1]) {
      const pivot = new THREE.Group(); pivot.position.set(side * (radius + 0.2), 0, rear); ship.add(pivot);
      for (let panel = 0; panel < 4; panel++) {
        const x = side * (0.12 + span * (panel + 0.5) / 4);
        add(new THREE.BoxGeometry(span / 4 - 0.025, 0.025, 1.15), cell, x, 0, 0, pivot);
        for (let line = 1; line < 5; line++) add(new THREE.BoxGeometry(span / 4 - 0.04, 0.028, 0.008), frame, x, 0, line * 0.2 - 0.6, pivot);
      }
      wings.push(pivot);
    }
  }
  // Radio plans allocate antenna time, not dish diameter. Keep the dish fixed.
  add(new THREE.CylinderGeometry(0.025, 0.025, 0.7, 8), frame, 0, radius + 0.25, length * 0.25);
  const points = Array.from({ length: 13 }, (_, i) => { const r = i / 12 * 0.6; return new THREE.Vector2(r, r * r * 0.55); });
  const dishMaterial = material(0xbcc0b9); dishMaterial.side = THREE.DoubleSide;
  const dish = add(new THREE.LatheGeometry(points, 24), dishMaterial, 0, radius + 0.6, length * 0.35);
  dish.rotation.x = -Math.PI / 2;
  const instruments = (d.payload || []).filter((id) => catalog.instruments.some((i) => i.id === id));
  if (instruments.length) {
    const boomLength = 0.7 + instruments.length * 0.32;
    add(new THREE.BoxGeometry(boomLength, 0.035, 0.035), frame, radius + boomLength / 2, -radius * 0.65, length * 0.15);
    instruments.forEach((id, i) => {
      const x = radius + 0.5 + i * 0.32, y = -radius * 0.65, z = length * 0.15;
      if (/ctx|camera/.test(id)) {
        add(new THREE.BoxGeometry(0.2, 0.2, 0.28), hull, x, y, z);
        add(cylinder(0.065, 0.065, 0.08, 12), sensor, x, y, z + 0.18);
      } else add(cylinder(0.09, 0.09, /grs/.test(id) ? 0.36 : 0.2, 8), hull, x, y, z);
    });
  }
  if (d.solar_monitor) add(new THREE.BoxGeometry(0.14, 0.1, 0.12), sensor, -radius - 0.1, 0, length * 0.3);
  // nozzle: the engine bell's exit, for the TMI burn glow on the spectator (Phase 6)
  ship.userData = { wings, dishPos: new THREE.Vector3(0, radius + 0.6, length * 0.35 + 0.2), nozzle: new THREE.Vector3(0, 0, rear - 1.9) };
  return ship;
}
