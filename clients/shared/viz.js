// 2D views drawn on <canvas>: the light ribbon (the gap between Earth and the
// ship, to scale in light-minutes), the orrery, the spacetime diagram and
// the porkchop plot. All colours come from the same palette as style.css.
import { KIND_COLORS, KIND_NAMES, dur } from './ll.js';

export function fit(cv) {
  palette(cv);
  const r = cv.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = Math.max(10, Math.round(r.width * dpr)), h = Math.max(10, Math.round(r.height * dpr));
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return [ctx, r.width, r.height];
}
export const C = {};
function palette(cv) {
  const css = getComputedStyle(cv), get = (v) => css.getPropertyValue(v).trim();
  Object.assign(C, { ink: get('--phosphor'), ink2: get('--haze'), ink3: get('--dim'), rule: get('--rule'), rule2: get('--rule-2'),
    signal: get('--signal'), lantern: get('--lantern'), alarm: get('--alarm'), go: get('--go'), earth: get('--haze'),
    mars: get('--phosphor'), sun: get('--haze'), night: get('--screen') });
}
const UI = (px) => `400 ${Math.max(11, px)}px B612, system-ui, sans-serif`;
const MONO = (px) => `400 ${Math.max(11, px)}px "B612 Mono", ui-monospace, monospace`;
const lerp = (a, b, t) => a + (b - a) * t;

// ------------------------------------------------------------ light ribbon
// Earth on the left, the ship on the right, one tick per light-minute.
// Messages crawl across at the speed of light; uplinks above, downlinks below.
export function drawRibbon(cv, s, now = performance.now(), recvAt = now) {
  const [ctx, W, H] = fit(cv);
  ctx.clearRect(0, 0, W, H);
  const g = s?.geometry; if (!g) return;
  const lt = g.lt_up_s, L = s.comms?.link || {};
  const x0 = 70, x1 = W - 92, y = Math.round(H * 0.45);
  // ticks
  const unit = lt < 120 ? 10 : lt < 1500 ? 60 : 300;
  const n = Math.floor(lt / unit);
  ctx.font = MONO(10.5); ctx.textAlign = 'center';
  const every = Math.max(1, Math.ceil(n / Math.max(4, Math.floor((x1 - x0) / 70))));
  for (let k = 1; k <= n; k++) {
    const x = lerp(x0, x1, (k * unit) / lt);
    ctx.fillStyle = k % every ? C.rule2 : C.ink3;
    ctx.fillRect(Math.round(x), y - 4, 1, 8);
    if (k % every === 0 && x < x1 - 30) ctx.fillText(unit === 10 ? `${k * 10} s` : `${(k * unit) / 60} min`, x, H - 3);
  }
  // the path itself: solid when an antenna is listening, dashed when messages must wait
  const open = L.uplink_open || L.downlink_open;
  ctx.strokeStyle = L.blackout ? C.alarm : open ? C.signal : C.lantern;
  ctx.globalAlpha = L.blackout ? 0.5 : open ? 0.55 : 0.6; ctx.lineWidth = 1.5;
  ctx.setLineDash(open && !L.blackout ? [] : [5, 6]);
  ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1; ctx.lineWidth = 1;
  if (L.blackout) {
    ctx.fillStyle = C.alarm; ctx.fillRect((x0 + x1) / 2 - 2, y - 8, 4, 16);
  }
  // ends
  const end = (x, col, label, align) => {
    ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x, y, 5, 0, 7); ctx.fill();
    ctx.font = UI(13, 500); ctx.fillStyle = C.ink; ctx.textAlign = align; ctx.fillText(label, x + (align === 'right' ? -16 : 16), y + 4.5);
  };
  end(x0, C.earth, 'Houston', 'right'); end(x1, C.signal, 'Meridian-1', 'left');
  // messages in flight
  const dt = Math.max(0, Math.min(0.5, (now - recvAt) / 1000)) * (s.rate || 0);
  const labels = [];
  for (const f of s.comms?.in_flight || []) {
    const frac = Math.min(1, f.frac + dt / Math.max(f.arrive_et - f.tx_et, 1));
    const up = f.direction === 'up', x = up ? lerp(x0, x1, frac) : lerp(x1, x0, frac);
    const quiet = f.kind === 'telemetry' || f.kind === 'status' || f.kind === 'tern_log';
    const yy = y + (up ? -12 : 12), col = KIND_COLORS[f.kind] || C.ink;
    ctx.fillStyle = col; ctx.globalAlpha = quiet ? 0.5 : 1;
    ctx.beginPath(); ctx.arc(x, yy, quiet ? 1.8 : 3.6, 0, 7); ctx.fill();
    if (!quiet) labels.push([x, yy + (up ? -10 : 19), KIND_NAMES[f.kind] || f.kind, col]);
    ctx.globalAlpha = 1;
  }
  ctx.font = UI(11); ctx.textAlign = 'center';
  let lastX = -1e9;
  for (const [x, yy, t, col] of labels.sort((a, b) => a[0] - b[0])) { if (x - lastX < 60) continue; lastX = x; ctx.fillStyle = col; ctx.fillText(t, x, yy); }
  ctx.textAlign = 'left';
}

// ------------------------------------------------------------------ orrery
// Top-down on the ecliptic from SPICE positions. `view` picks whose knowledge
// to draw: Lantern sees the ship where its light left it, the ship sees Earth
// the same way. The truth is drawn faintly underneath.
export function drawOrrery(cv, s, traj, view = 'truth', now = performance.now(), recvAt = now) {
  const [ctx, W, H] = fit(cv);
  ctx.clearRect(0, 0, W, H);
  if (!s?.geometry) { ctx.fillStyle = C.ink3; ctx.font = UI(13); ctx.fillText('The orrery appears after launch.', 14, 24); return; }
  const g = s.geometry, P = g.pos;
  const R = 1.72, sc = Math.min(W, H) / (2 * R), cx = W / 2, cy = H / 2;
  const X = (p) => cx + p[0] * sc, Y = (p) => cy - p[1] * sc;
  // 1 AU scale ring
  ctx.strokeStyle = C.rule; ctx.setLineDash([2, 5]); ctx.beginPath(); ctx.arc(cx, cy, sc, 0, 7); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = C.ink3; ctx.font = UI(10.5); ctx.textAlign = 'center'; ctx.fillText('1 AU from the Sun', cx, cy + sc + 14); ctx.textAlign = 'left';
  const path = (pts, col, a, dash) => {
    if (!pts?.length) return;
    ctx.strokeStyle = col; ctx.globalAlpha = a; ctx.setLineDash(dash || []); ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(X(p), Y(p)) : ctx.moveTo(X(p), Y(p)))); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
  };
  path(traj?.earth, C.earth, 0.28); path(traj?.mars, C.mars, 0.24);
  ctx.lineWidth = 1.5; path(traj?.ship, C.signal, 0.8, [4, 4]); ctx.lineWidth = 1;
  // The Sun is a crisp marker; a blocked link is shown from the contact plan.
  ctx.fillStyle = C.sun; ctx.beginPath(); ctx.arc(cx, cy, 4, 0, 7); ctx.fill();
  ctx.font = UI(11); ctx.fillStyle = C.ink3; ctx.fillText('Sun', cx + 10, cy + 4);
  // the radio path
  const L = s.comms?.link || {};
  ctx.strokeStyle = L.blackout ? C.alarm : C.rule2;
  ctx.setLineDash(L.blackout ? [4, 4] : []);
  ctx.beginPath(); ctx.moveTo(X(P.EARTH), Y(P.EARTH)); ctx.lineTo(X(P.SHIP), Y(P.SHIP)); ctx.stroke(); ctx.setLineDash([]);
  // A bundle's real transmission fraction, linearly projected onto its path.
  const dt = Math.max(0, Math.min(0.5, (now - recvAt) / 1000)) * (s.rate || 0);
  for (const packet of (s.comms?.in_flight || []).slice(0, 64)) {
    const fraction = Math.max(0, Math.min(1, packet.frac + dt / Math.max(1, packet.arrive_et - packet.tx_et)));
    const t = packet.direction === 'up' ? fraction : 1 - fraction;
    const x = lerp(X(P.EARTH), X(P.SHIP), t), y = lerp(Y(P.EARTH), Y(P.SHIP), t);
    ctx.fillStyle = packet.kind === 'alert' ? C.lantern : C.signal;
    ctx.beginPath(); ctx.arc(x, y, packet.kind === 'telemetry' ? 1.5 : 3, 0, Math.PI * 2); ctx.fill();
  }
  if (L.blackout) {
    ctx.strokeStyle = C.alarm; ctx.beginPath(); ctx.arc(cx, cy, 12, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = C.alarm; ctx.font = UI(11); ctx.fillText('Conjunction · radio blocked', 12, 20);
  }
  const body = (p, col, r) => { ctx.fillStyle = col; ctx.beginPath(); ctx.arc(X(p), Y(p), r, 0, 7); ctx.fill(); };
  const ghost = (p, col, r) => { ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(X(p), Y(p), r, 0, 7); ctx.stroke(); ctx.lineWidth = 1; };
  const labels = [];   // [x, y, title, colour, note]: placed at the end so they never overlap
  body(P.MARS, C.mars, 4.5); labels.push([X(P.MARS), Y(P.MARS), 'Mars', C.mars]);
  if (view === 'ship') {
    ctx.globalAlpha = 0.35; body(P.EARTH, C.earth, 3); ctx.globalAlpha = 1;
    ghost(g.apparent_earth, C.earth, 6); labels.push([X(g.apparent_earth), Y(g.apparent_earth), 'Earth', C.earth, g.lt_down_s > 30 ? `as its light left, ${dur(g.lt_down_s)} ago` : '']);
  } else { body(P.EARTH, C.earth, 4.5); labels.push([X(P.EARTH), Y(P.EARTH), 'Earth', C.earth]); }
  if (view === 'control') {
    ctx.globalAlpha = 0.35; body(P.SHIP, C.signal, 2.5); ctx.globalAlpha = 1;
    ghost(g.apparent_ship, C.signal, 6); labels.push([X(g.apparent_ship), Y(g.apparent_ship), 'Meridian-1', C.signal, g.lt_up_s > 30 ? `as its light left, ${dur(g.lt_up_s)} ago` : '']);
  } else { body(P.SHIP, C.signal, 3.5); labels.push([X(P.SHIP), Y(P.SHIP), 'Meridian-1', C.signal]); }
  placeLabels(ctx, labels, W, H, [[cx, cy, 10], [cx - 30, cy + sc + 8, 8], [cx + 30, cy + sc + 8, 8], [cx, cy + sc + 8, 8]]);
  if (W > 420) { ctx.font = UI(10.5); ctx.fillStyle = C.ink3; ctx.fillText(`Seen from above the ecliptic. Positions from ${s.ephemeris?.label || 'the ephemeris'}.`, 12, H - 10); }
}

// Put each label beside its dot, trying the four corners until it collides with nothing.
function placeLabels(ctx, items, W, H, avoid = []) {
  const placed = avoid.map(([x, y, r]) => [x - r, y - r, x + r, y + r]);
  for (const [x, y] of items) placed.push([x - 6, y - 6, x + 6, y + 6]);
  const hit = (b) => placed.some((p) => b[0] < p[2] && b[2] > p[0] && b[1] < p[3] && b[3] > p[1]);
  for (const [x, y, title, col, note] of items) {
    const displayNote = W >= 280 ? note : '';
    ctx.font = UI(12, 500); const w1 = ctx.measureText(title).width;
    ctx.font = MONO(10.5); const w2 = displayNote ? ctx.measureText(displayNote).width : 0;
    const w = Math.max(w1, w2), h = displayNote ? 28 : 14;
    const tries = [12, 28, 44, 60, 80].flatMap((gap) => [[gap, -h - gap], [gap, gap], [-w - gap, -h - gap], [-w - gap, gap], [-w / 2, -h - gap], [-w / 2, gap]]);
    let box = null;
    for (const [dx, dy] of tries) {
      const bx = Math.max(8, Math.min(W - w - 8, x + dx)), by = Math.max(8, Math.min(H - h - 28, y + dy));
      const b = [bx, by, bx + w, by + h];
      if (!hit(b)) { box = b; break; }
    }
    if (!box) box = [8, H - h - 28, 8 + w, H - 28];
    placed.push(box);
    if (Math.abs(box[0] - x) > 24 || Math.abs(box[1] - y) > 40) {
      ctx.strokeStyle = C.rule2; ctx.beginPath(); ctx.moveTo(x, y);
      ctx.lineTo(Math.max(box[0], Math.min(box[2], x)), box[1] + h / 2); ctx.stroke();
    }
    ctx.font = UI(12, 500); ctx.fillStyle = col; ctx.textAlign = 'left'; ctx.fillText(title, box[0], box[1] + 11);
    if (displayNote) { ctx.font = MONO(10.5); ctx.fillStyle = C.ink2; ctx.fillText(displayNote, box[0], box[1] + 25); }
  }
}

// --------------------------------------------- spacetime ("who can know what")
// x = distance from Earth (0) to the ship (1); y = time, now at the top.
export function drawSpacetime(cv, s, history) {
  const [ctx, W, H] = fit(cv);
  ctx.clearRect(0, 0, W, H);
  const g = s?.geometry; if (!g) return;
  const span = Math.max(g.lt_up_s * 3, 30), pl = 64, pr = 76, pt = 22, pb = 18;
  const tx = (f) => pl + f * (W - pl - pr), ty = (ago) => pt + (ago / span) * (H - pt - pb);
  // the region the ship can know about (inside its past light cone) is shaded
  ctx.fillStyle = 'rgba(134,208,245,.06)';
  ctx.beginPath(); ctx.moveTo(tx(1), ty(0)); ctx.lineTo(tx(0), ty(g.lt_up_s)); ctx.lineTo(tx(0), ty(span)); ctx.lineTo(tx(1), ty(span)); ctx.closePath(); ctx.fill();
  ctx.strokeStyle = C.rule2; ctx.beginPath(); ctx.moveTo(tx(0), pt); ctx.lineTo(tx(0), H - pb); ctx.moveTo(tx(1), pt); ctx.lineTo(tx(1), H - pb); ctx.stroke();
  ctx.font = UI(12, 500); ctx.textAlign = 'center';
  ctx.fillStyle = C.earth; ctx.fillText('Houston', tx(0), 14); ctx.fillStyle = C.signal; ctx.fillText('Meridian-1', tx(1), 14);
  ctx.textAlign = 'right'; ctx.font = MONO(10.5); ctx.fillStyle = C.ink3;
  ctx.fillText('now', pl - 8, ty(0) + 4); ctx.fillText(`${dur(span)} ago`, pl - 8, H - pb);
  ctx.textAlign = 'left';
  ctx.strokeStyle = C.signal; ctx.globalAlpha = 0.6; ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(tx(1), ty(0)); ctx.lineTo(tx(0), ty(g.lt_up_s)); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
  ctx.font = UI(11); ctx.fillStyle = C.signal;
  ctx.fillText('Below this line: what the ship can know', tx(0.06), ty(g.lt_up_s) + 16);
  for (const b of history || []) {
    const ago0 = s.et - b.tx_et, ago1 = s.et - Math.min(b.arrive_et, s.et);
    if (ago0 > span) continue;
    const up = b.direction === 'up', frac = Math.min(1, (s.et - b.tx_et) / Math.max(b.arrive_et - b.tx_et, 1e-6));
    ctx.strokeStyle = KIND_COLORS[b.kind] || C.ink; ctx.globalAlpha = b.kind === 'telemetry' ? 0.35 : 0.9;
    ctx.beginPath(); ctx.moveTo(tx(up ? 0 : 1), ty(ago0)); ctx.lineTo(tx(up ? frac : 1 - frac), ty(ago1)); ctx.stroke();
    ctx.globalAlpha = 1;
  }
}

// ---------------------------------------------------------------- porkchop
// Each cell is one Lambert transfer. Bright = cheap. Grey = too heavy to launch.
const RAMP = [[0, [35, 43, 43]], [0.35, [69, 91, 94]], [0.7, [137, 165, 170]], [1, [227, 236, 230]]];
function rampRGB(t) {
  for (let k = 1; k < RAMP.length; k++) if (t <= RAMP[k][0]) {
    const [a, ca] = RAMP[k - 1], [b, cb] = RAMP[k], u = (t - a) / (b - a);
    return ca.map((c, i) => Math.round(lerp(c, cb[i], u)));
  }
  return [226, 244, 250];
}
const ramp = (t) => `rgb(${rampRGB(t).join(',')})`;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const mlabel = (iso) => `${MONTHS[+iso.slice(5, 7) - 1]} ${+iso.slice(8, 10)}`;
export function drawPorkchop(cv, pc, sel, budgetFn, marks = []) {
  const [ctx, W, H] = fit(cv);
  ctx.clearRect(0, 0, W, H);
  if (!pc) { ctx.fillStyle = C.ink3; ctx.font = UI(13); ctx.fillText('Solving a few thousand transfers…', 14, 24); return null; }
  const nL = pc.launch_et.length, nA = pc.arrival_et.length, pad = { l: 66, b: 40, t: 8, r: 8 };
  const cw = (W - pad.l - pad.r) / nL, ch = (H - pad.t - pad.b) / nA;
  const vals = pc.dv_total.flat().filter((v) => v !== null), lo = Math.min(...vals), hi = Math.min(lo + 6, Math.max(...vals));
  ctx.fillStyle = C.night; ctx.fillRect(pad.l, pad.t, W - pad.l - pad.r, H - pad.t - pad.b);
  const okGrid = pc.dv_total.map((row, i) => row.map((v, j) => v !== null && (budgetFn ? budgetFn(i, j) : true)));
  const X = (j) => pad.l + j * cw, Yb = (i) => H - pad.b - i * ch;
  // paint one pixel per transfer off-screen, then scale it up smoothly (no seams between cells)
  const off = (drawPorkchop.off ||= document.createElement('canvas')); off.width = nL; off.height = nA;
  const octx = off.getContext('2d'), img = octx.createImageData(nL, nA);
  for (let i = 0; i < nA; i++) for (let j = 0; j < nL; j++) {
    const v = pc.dv_total[i][j], k = ((nA - 1 - i) * nL + j) * 4;
    if (v === null) { img.data.set([12, 20, 31, 255], k); continue; }
    const t = 1 - Math.sqrt(Math.min(1, (v - lo) / (hi - lo)));
    let c = rampRGB(t); if (!okGrid[i][j]) c = c.map((x, n) => Math.round(x * 0.3 + [10, 16, 25][n] * 0.7));
    img.data.set([...c, 255], k);
  }
  octx.putImageData(img, 0, 0);
  ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(off, X(0), Yb(nA), nL * cw, nA * ch);
  // outline the trips the rocket can actually lift
  ctx.strokeStyle = C.signal; ctx.lineWidth = 1.5; ctx.beginPath();
  for (let i = 0; i < nA; i++) for (let j = 0; j < nL; j++) {
    if (!okGrid[i][j]) continue;
    if (!okGrid[i + 1]?.[j]) { ctx.moveTo(X(j), Yb(i + 1)); ctx.lineTo(X(j + 1), Yb(i + 1)); }
    if (!okGrid[i - 1]?.[j]) { ctx.moveTo(X(j), Yb(i)); ctx.lineTo(X(j + 1), Yb(i)); }
    if (!okGrid[i][j - 1]) { ctx.moveTo(X(j), Yb(i)); ctx.lineTo(X(j), Yb(i + 1)); }
    if (!okGrid[i][j + 1]) { ctx.moveTo(X(j + 1), Yb(i)); ctx.lineTo(X(j + 1), Yb(i + 1)); }
  }
  ctx.stroke(); ctx.lineWidth = 1;
  ctx.font = MONO(10.5); ctx.fillStyle = C.ink2; ctx.textAlign = 'center';
  for (let j = 0; j < nL; j += Math.ceil(nL / 6)) ctx.fillText(mlabel(pc.launch_iso[j]), pad.l + (j + 0.5) * cw, H - pad.b + 15);
  ctx.textAlign = 'right';
  for (let i = 0; i < nA; i += Math.ceil(nA / 7)) ctx.fillText(mlabel(pc.arrival_iso[i]), pad.l - 8, H - pad.b - (i + 0.5) * ch + 4);
  ctx.font = UI(11.5); ctx.fillStyle = C.ink3; ctx.textAlign = 'center';
  ctx.fillText(`Launch date, ${pc.launch_iso[0].slice(0, 4)}`, pad.l + (W - pad.l - pad.r) / 2, H - 6);
  ctx.save(); ctx.translate(13, pad.t + (H - pad.t - pad.b) / 2); ctx.rotate(-Math.PI / 2); ctx.fillText('Arrival at Mars', 0, 0); ctx.restore();
  ctx.textAlign = 'left';
  const cell = (c) => [pad.l + (c.j + 0.5) * cw, H - pad.b - (c.i + 0.5) * ch];
  for (const m of marks) {          // reference missions, e.g. Perseverance
    const [x, y] = cell(m.cell);
    ctx.font = UI(11.5, 500); const tw = ctx.measureText(m.label).width;
    ctx.fillStyle = 'rgba(10,16,25,.85)'; ctx.beginPath(); ctx.roundRect(x + 13, y - 18, tw + 12, 18, 4); ctx.fill();
    ctx.fillStyle = C.lantern; ctx.fillText(m.label, x + 19, y - 5);
    ctx.strokeStyle = C.signal; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x + 4, y - 4); ctx.lineTo(x + 13, y - 10); ctx.stroke();
  }
  if (sel) {
    const [x, y] = cell(sel);
    ctx.strokeStyle = C.night; ctx.lineWidth = 5; ctx.beginPath(); ctx.arc(x, y, 7, 0, 7); ctx.stroke();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, 7, 0, 7); ctx.stroke(); ctx.lineWidth = 1;
    ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(x, y, 2, 0, 7); ctx.fill();
  }
  return { pick: (x, y) => ({ j: Math.max(0, Math.min(nL - 1, Math.floor((x - pad.l) / cw))), i: Math.max(0, Math.min(nA - 1, Math.floor((H - pad.b - y) / ch))) }),
    range: [lo, hi], ramp };
}
export function rampCss() { return `linear-gradient(90deg, ${[...RAMP].reverse().map(([t, c]) => `rgb(${c.join(',')}) ${(1 - t) * 100}%`).join(', ')})`; }

export function nearestCell(pc, launchEt, arrivalEt) {
  const idx = (arr, v) => arr.reduce((b, x, k) => (Math.abs(x - v) < Math.abs(arr[b] - v) ? k : b), 0);
  return { j: idx(pc.launch_et, launchEt), i: idx(pc.arrival_et, arrivalEt) };
}
