// Mike's pets: a tiny pixel-art engine plus the character catalog.
// Each pet is a few shapes on a 32×32 grid; the engine adds outline, highlight and
// shade automatically, then draws the face (eyes, mouth, cheeks) for the current mood.

export const GRID = 32;      // sprite size in pixels
export const CANVAS = 40;    // canvas grid: room around the sprite for effects (zzz, ?, …)
const OX = 4, OY = 6;        // sprite offset inside the canvas grid

// ---------------------------------------------------------------- catalog
export const PETS = {
  mike: {
    name: "Mike", tagline: "Your voice, on your desk.",
    parts: [
      { t: "rect", x: 12, y: 28, w: 3, h: 2, c: "#2a2d44" },
      { t: "rect", x: 17, y: 28, w: 3, h: 2, c: "#2a2d44" },
      { t: "poly", pts: [[10, 21], [22, 21], [19.5, 28.5], [12.5, 28.5]], c: "#3d4263" },
      { t: "rect", x: 9, y: 19, w: 14, h: 3, c: "#ef6a3a" },
      { t: "circle", cx: 16, cy: 11, r: 9.2, c: "#c3cbe2" },
    ],
    details: [
      // microphone grille: a dotted mesh across the top of the head
      { c: "#8d96b5", px: [[11, 4], [13, 4], [15, 4], [17, 4], [19, 4], [21, 4], [10, 5], [12, 5], [14, 5], [16, 5], [18, 5], [20, 5], [22, 5],
                          [9, 6], [11, 6], [13, 6], [15, 6], [17, 6], [19, 6], [21, 6], [23, 6], [8, 8], [24, 8], [8, 10], [24, 10]] },
      { c: "#ffd2bf", px: [[11, 20], [20, 20]] },
    ],
    eyes: [[12, 10], [18, 10]], mouth: [15, 15], cheeks: [[9, 13], [22, 13]],
  },
  mochi: {
    name: "Mochi", tagline: "Soft, squishy, always listening.",
    parts: [
      { t: "ellipse", cx: 16, cy: 22, rx: 12.5, ry: 8.5, c: "#ff9ebb", k: "body" },
      { t: "ellipse", cx: 16, cy: 16, rx: 8.5, ry: 8, c: "#ff9ebb", k: "body" },
    ],
    details: [{ c: "#ffe3ec", px: [[11, 11], [12, 10], [11, 12], [13, 10]] }],
    eyes: [[12, 18], [18, 18]], mouth: [15, 23], cheeks: [[9, 21], [22, 21]],
  },
  tapey: {
    name: "Tapey", tagline: "Mixtapes and memos, on repeat.",
    parts: [
      { t: "rect", x: 9, y: 26, w: 2, h: 3, c: "#4a3b2e" },
      { t: "rect", x: 21, y: 26, w: 2, h: 3, c: "#4a3b2e" },
      { t: "rect", x: 7, y: 29, w: 5, h: 1, c: "#4a3b2e" },
      { t: "rect", x: 20, y: 29, w: 5, h: 1, c: "#4a3b2e" },
      { t: "rrect", x: 3, y: 8, w: 26, h: 19, r: 3, c: "#f2a541" },
      { t: "rrect", x: 6, y: 11, w: 20, h: 10, r: 1, c: "#fff1d0", flat: true },
      { t: "poly", pts: [[8, 22], [24, 22], [22, 27], [10, 27]], c: "#d98a2b" },
    ],
    details: [
      { c: "#7a4b16", px: [[5, 10], [26, 10], [5, 25], [26, 25]] },
      { c: "#e7c98f", px: [[8, 20], [9, 20], [10, 20], [21, 20], [22, 20], [23, 20]] },
      { c: "#5a3a1a", px: [[12, 24], [19, 24]] },
    ],
    eyes: [[11, 13], [19, 13]], mouth: [15, 17], cheeks: [[8, 16], [23, 16]],
  },
  bolt: {
    name: "Bolt", tagline: "Fully charged for long days.",
    parts: [
      { t: "rect", x: 10, y: 28, w: 4, h: 2, c: "#2b6b47" },
      { t: "rect", x: 18, y: 28, w: 4, h: 2, c: "#2b6b47" },
      { t: "rect", x: 5, y: 15, w: 4, h: 3, c: "#4fd08a" },
      { t: "rect", x: 23, y: 15, w: 4, h: 3, c: "#4fd08a" },
      { t: "rect", x: 13, y: 3, w: 6, h: 4, c: "#cfd6de" },
      { t: "rrect", x: 9, y: 6, w: 14, h: 22, r: 2, c: "#4fd08a" },
      { t: "rect", x: 9, y: 21, w: 14, h: 4, c: "#2f9a63", flat: true },
    ],
    details: [{ c: "#ffe066", px: [[16, 21], [15, 22], [16, 22], [17, 22], [16, 23], [15, 24]] }],
    eyes: [[12, 11], [18, 11]], mouth: [15, 16], cheeks: [[10, 14], [21, 14]],
  },
  boo: {
    name: "Boo", tagline: "Quiet company for late nights.",
    floaty: true,
    parts: [
      { t: "ellipse", cx: 16, cy: 13, rx: 10, ry: 10, c: "#eef0ff", k: "body" },
      { t: "rect", x: 6, y: 13, w: 20, h: 12, c: "#eef0ff", k: "body" },
      ...[6, 10, 14, 18, 22].map((x) => ({ t: "poly", pts: [[x, 24.5], [x + 4, 24.5], [x + 2, 29]], c: "#eef0ff", k: "body" })),
    ],
    details: [{ c: "#ffffff", px: [[10, 6], [11, 5], [12, 5]] }],
    eyes: [[12, 12], [18, 12]], mouth: [15, 17], cheeks: [[9, 15], [22, 15]],
  },
  kit: {
    name: "Kit", tagline: "Curious about everything you do.",
    parts: [
      { t: "poly", pts: [[23, 25], [28, 17], [30, 18], [26, 27]], c: "#ff9f43" },
      { t: "ellipse", cx: 16, cy: 24, rx: 8, ry: 6, c: "#ff9f43" },
      { t: "ellipse", cx: 16, cy: 25.5, rx: 4, ry: 3.6, c: "#ffe2c2", flat: true },
      { t: "rect", x: 11, y: 29, w: 3, h: 1, c: "#d8771c" },
      { t: "rect", x: 18, y: 29, w: 3, h: 1, c: "#d8771c" },
      { t: "poly", pts: [[6, 2.5], [13.5, 8], [6, 11]], c: "#ff9f43", k: "head" },
      { t: "poly", pts: [[26, 2.5], [18.5, 8], [26, 11]], c: "#ff9f43", k: "head" },
      { t: "ellipse", cx: 16, cy: 13, rx: 10.5, ry: 7.8, c: "#ff9f43", k: "head" },
    ],
    details: [
      { c: "#ffb3c1", px: [[7, 6], [7, 7], [24, 6], [24, 7]] },
      { c: "#d8771c", px: [[15, 6], [16, 6], [16, 7], [12, 7], [20, 7], [27, 19], [28, 20]] },
    ],
    eyes: [[12, 12], [18, 12]], mouth: [15, 16], cheeks: [[8, 14], [23, 14]],
  },
};
export const PET_ORDER = ["none", "mike", "mochi", "tapey", "bolt", "boo", "kit"];
export const NONE = { name: "None", tagline: "Just a quiet floating indicator." };

// ---------------------------------------------------------------- color helpers
function hex(c) { const n = parseInt(c.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function mix(rgb, to, amt) { return rgb.map((v, i) => Math.round(v + (to[i] - v) * amt)); }
const css = (rgb) => `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
const WHITE = [255, 255, 255], INK = [24, 20, 36];

// ---------------------------------------------------------------- rasterizer
function inside(p, x, y) {
  switch (p.t) {
    case "rect": return x >= p.x && x < p.x + p.w && y >= p.y && y < p.y + p.h;
    case "circle": return (x - p.cx) ** 2 + (y - p.cy) ** 2 <= p.r ** 2;
    case "ellipse": return ((x - p.cx) / p.rx) ** 2 + ((y - p.cy) / p.ry) ** 2 <= 1;
    case "rrect": {
      if (!(x >= p.x && x < p.x + p.w && y >= p.y && y < p.y + p.h)) return false;
      const qx = Math.max(p.x + p.r - x, 0, x - (p.x + p.w - p.r));
      const qy = Math.max(p.y + p.r - y, 0, y - (p.y + p.h - p.r));
      return qx * qx + qy * qy <= p.r * p.r;
    }
    case "poly": {
      let inn = false;
      for (let i = 0, j = p.pts.length - 1; i < p.pts.length; j = i++) {
        const [xi, yi] = p.pts[i], [xj, yj] = p.pts[j];
        if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inn = !inn;
      }
      return inn;
    }
  }
  return false;
}

const cache = new Map();
/** Returns a GRID×GRID array of rgb triples (or null) for the pet's body, shaded and outlined. */
export function bake(id) {
  if (cache.has(id)) return cache.get(id);
  const pet = PETS[id];
  const color = Array(GRID * GRID).fill(null), part = Array(GRID * GRID).fill(-1), flat = Array(GRID * GRID).fill(false);
  pet.parts.forEach((p, i) => {
    const key = p.k ?? i;
    for (let y = 0; y < GRID; y++) for (let x = 0; x < GRID; x++) {
      if (inside(p, x + 0.5, y + 0.5)) { color[y * GRID + x] = hex(p.c); part[y * GRID + x] = key; flat[y * GRID + x] = !!p.flat; }
    }
  });
  const same = (x, y, k) => x >= 0 && y >= 0 && x < GRID && y < GRID && part[y * GRID + x] === k;
  const out = color.map((c) => c && [...c]);
  for (let y = 0; y < GRID; y++) for (let x = 0; x < GRID; x++) {
    const i = y * GRID + x, k = part[i];
    if (k === -1 || flat[i]) continue;
    const lit = !same(x, y - 1, k) || !same(x - 1, y, k);
    const dark = !same(x, y + 1, k) || !same(x + 1, y, k);
    if (dark) out[i] = mix(color[i], INK, 0.22);
    else if (!same(x, y + 2, k)) out[i] = mix(color[i], INK, 0.1);
    else if (lit) out[i] = mix(color[i], WHITE, 0.28);
  }
  for (const d of pet.details || []) for (const [x, y] of d.px) out[y * GRID + x] = hex(d.c);
  // outline: empty pixels touching the body get a dark version of their neighbour
  const final = out.map((c) => c && [...c]);
  for (let y = 0; y < GRID; y++) for (let x = 0; x < GRID; x++) {
    if (out[y * GRID + x]) continue;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = x + dx, ny = y + dy;
      if (nx >= 0 && ny >= 0 && nx < GRID && ny < GRID && color[ny * GRID + nx]) {
        final[y * GRID + x] = mix(color[ny * GRID + nx], INK, 0.72);
        break;
      }
    }
  }
  cache.set(id, final);
  return final;
}

// ---------------------------------------------------------------- tiny glyphs (effects)
const GLYPHS = {
  z: ["111", "001", "010", "100", "111"],
  q: ["111", "001", "011", "000", "010"],
  dot: ["1"],
  spark: ["010", "111", "010"],
  arc: ["10", "01", "01", "10"],
  heart: ["1010", "1111", "0110"],
};
function glyph(ctx, name, x, y, fill) {
  ctx.fillStyle = fill;
  GLYPHS[name].forEach((row, j) => [...row].forEach((b, i) => { if (b === "1") ctx.fillRect(x + i, y + j, 1, 1); }));
}

// ---------------------------------------------------------------- drawing
/**
 * Draw a pet into a canvas 2D context whose transform maps 1 unit = 1 pixel of the CANVAS grid.
 * mood: idle | listen | think | talk | ask | happy | sleep | off
 * o: { t (seconds), look: [dx,dy] in -1..1, blink: bool, mouthOpen: bool }
 */
export function drawPet(ctx, id, mood, o = {}) {
  const t = o.t || 0;
  ctx.clearRect(0, 0, CANVAS, CANVAS);
  const pet = PETS[id];
  if (!pet) return;
  const floaty = pet.floaty;
  const bobAmp = mood === "sleep" ? 0.5 : floaty ? 1.6 : 1;
  const bobSpeed = mood === "sleep" ? 1.2 : mood === "think" ? 4 : 2.4;
  let bob = Math.round(Math.sin(t * bobSpeed) * bobAmp);
  if (mood === "happy") bob = -Math.round(Math.abs(Math.sin(t * 9)) * 2);
  const by = OY + bob;

  // ground shadow
  ctx.fillStyle = "rgba(20,16,30,0.2)";
  const sw = floaty ? 10 - bob : 12;
  ctx.fillRect(OX + 16 - sw / 2, OY + 31, sw, 1);
  ctx.fillRect(OX + 16 - sw / 2 + 2, OY + 32, sw - 4, 1);

  const px = bake(id);
  const off = mood === "off";
  for (let y = 0; y < GRID; y++) for (let x = 0; x < GRID; x++) {
    let c = px[y * GRID + x];
    if (!c) continue;
    if (off) { const g = Math.round(c[0] * 0.3 + c[1] * 0.59 + c[2] * 0.11); c = [g, g, g]; }
    ctx.fillStyle = css(c);
    ctx.fillRect(OX + x, by + y, 1, 1);
  }
  drawFace(ctx, pet, mood, o, by);
  drawEffects(ctx, mood, t, by);
}

function drawFace(ctx, pet, mood, o, by) {
  const ink = "#1c1828";
  const look = o.look || [0, 0];
  let lx = Math.round(look[0]), ly = Math.round(look[1]);
  if (mood === "think") { lx = 1; ly = -1; }
  const closed = mood === "sleep" || mood === "off" || (o.blink && mood !== "happy");
  for (const [ex0, ey0] of pet.eyes) {
    const ex = OX + ex0 + (closed ? 0 : lx), ey = by + ey0 + (closed ? 0 : ly);
    ctx.fillStyle = ink;
    if (mood === "happy") {                // ^ ^
      ctx.fillRect(ex - 1, ey + 2, 1, 1); ctx.fillRect(ex, ey + 1, 2, 1); ctx.fillRect(ex + 2, ey + 2, 1, 1);
    } else if (closed) {
      ctx.fillRect(ex, ey + 2, 2, 1);
    } else {
      ctx.fillRect(ex, ey, 2, 3);
      ctx.fillStyle = "#ffffff"; ctx.fillRect(ex, ey, 1, 1);
      if (mood === "listen" || mood === "ask") ctx.fillRect(ex + 1, ey + 2, 1, 1);
    }
  }
  if (mood !== "off") {
    ctx.fillStyle = "rgba(255,110,140,0.55)";
    for (const [cx, cy] of pet.cheeks) ctx.fillRect(OX + cx, by + cy, 2, 1);
  }
  const mx = OX + pet.mouth[0], my = by + pet.mouth[1];
  ctx.fillStyle = ink;
  if ((mood === "talk" && o.mouthOpen) || mood === "listen" || mood === "ask") {
    ctx.fillRect(mx, my, 2, 2); ctx.fillStyle = "#ff7a8a"; ctx.fillRect(mx, my + 1, 2, 1);
  } else if (mood === "happy" || mood === "idle") {
    ctx.fillRect(mx - 1, my, 1, 1); ctx.fillRect(mx, my + 1, 2, 1); ctx.fillRect(mx + 2, my, 1, 1);
  } else if (mood === "sleep") {
    ctx.fillRect(mx, my + 1, 2, 1);
  } else {
    ctx.fillRect(mx, my, 2, 1);
  }
}

function drawEffects(ctx, mood, t, by) {
  const top = by - 1;
  if (mood === "sleep") {
    for (let i = 0; i < 3; i++) {
      const ph = (t * 0.5 + i / 3) % 1;
      ctx.globalAlpha = Math.sin(ph * Math.PI);
      glyph(ctx, "z", OX + 25 + Math.round(ph * 6), top - Math.round(ph * 8) + 3, "#8f87b8");
    }
    ctx.globalAlpha = 1;
  } else if (mood === "think") {
    for (let i = 0; i < 3; i++) {
      ctx.globalAlpha = Math.floor(t * 3) % 4 > i ? 1 : 0.25;
      glyph(ctx, "dot", OX + 25 + i * 2, top - 1, "#7c6cff");
    }
    ctx.globalAlpha = 1;
  } else if (mood === "ask") {
    glyph(ctx, "q", OX + 26, top - 3 - Math.round(Math.abs(Math.sin(t * 4))), "#f0a020");
  } else if (mood === "listen" || mood === "talk") {
    const col = mood === "listen" ? "#29c48a" : "#7c6cff";
    for (let i = 0; i < 2; i++) {
      ctx.globalAlpha = 0.35 + 0.65 * ((Math.sin(t * 8 - i * 1.4) + 1) / 2);
      glyph(ctx, "arc", OX + 31 + i * 2, by + 9 - i, col);
    }
    ctx.globalAlpha = 1;
  } else if (mood === "happy") {
    const s = Math.floor(t * 6) % 2;
    glyph(ctx, "spark", OX + (s ? 1 : 27), top + (s ? 2 : 0), "#ffcf3f");
    glyph(ctx, "heart", OX + (s ? 27 : 0), top + (s ? 3 : 5), "#ff6f91");
  }
}

/** Draw the "None" pet: a small glass pill with a live waveform (not pixel art). */
export function drawNone(ctx, w, h, mood, t) {
  ctx.clearRect(0, 0, w, h);
  const pw = w * 0.62, ph = h * 0.3, x = (w - pw) / 2, y = (h - ph) / 2;
  ctx.fillStyle = "rgba(30,28,40,0.82)";
  ctx.beginPath(); ctx.roundRect(x, y, pw, ph, ph / 2); ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.14)"; ctx.lineWidth = 1; ctx.stroke();
  const n = 7, gap = pw / (n + 3);
  ctx.fillStyle = mood === "listen" ? "#4be0a0" : mood === "ask" ? "#f0b040" : mood === "off" || mood === "sleep" ? "#777" : "#a99bff";
  const active = mood === "listen" || mood === "talk" || mood === "think";
  for (let i = 0; i < n; i++) {
    const a = active ? 0.25 + 0.75 * Math.abs(Math.sin(t * (mood === "think" ? 3 : 7) + i * 0.9)) : 0.15;
    const bh = Math.max(2, ph * 0.62 * a);
    ctx.beginPath(); ctx.roundRect(x + gap * (i + 2) - 1.5, y + (ph - bh) / 2, 3, bh, 1.5); ctx.fill();
  }
}

/** Animated preview for the picker. Returns a stop() function. */
export function preview(canvas, id, mood = "idle") {
  const ctx = canvas.getContext("2d");
  let raf, stop = false;
  const start = performance.now();
  const loop = (now) => {
    if (stop) return;
    const t = (now - start) / 1000;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (id === "none") drawNone(ctx, canvas.width, canvas.height, "talk", t);
    else {
      const scale = Math.floor(canvas.width / CANVAS);
      ctx.imageSmoothingEnabled = false;
      ctx.setTransform(scale, 0, 0, scale, Math.floor((canvas.width - CANVAS * scale) / 2), Math.floor((canvas.height - CANVAS * scale) / 2));
      drawPet(ctx, id, mood, { t, blink: (t % 4) > 3.85 });
    }
    raf = requestAnimationFrame(loop);
  };
  raf = requestAnimationFrame(loop);
  return () => { stop = true; cancelAnimationFrame(raf); };
}
