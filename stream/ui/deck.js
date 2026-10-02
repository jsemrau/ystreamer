// InfiniTrance stream page, after Switch Angel's screen (deck.html).
// Polls ui.json (written by the engine, served by stream/ui_server.py) and
// draws the live code with a piano roll under every pattern (the notes Tidal
// says it plays, lighting up as they sound) and every knob as a slider; on
// the right the channel's name, a particle cloud that plays with the music
// and the agent at work. The arc's turning points get their moment on screen.
"use strict";

const STAGE_W = 1920, STAGE_H = 1080, POLL_MS = 500;
const PHASE_NAMES = { build: "build", groove: "groove", breakdown: "breakdown", rebuild: "rebuild",
                      groove2: "peak", outro: "outro" };
// What each turning point says on screen.
const MOMENTS = { breakdown: "BREAKDOWN", rebuild: "BUILD UP", groove2: "DROP", outro: "OUTRO" };
const STYLES = {
  lofi:      { accent: "#ffb870", accent2: "#ff7eb6" },
  downtempo: { accent: "#f5a97f", accent2: "#c6a0f6" },
  ambient:   { accent: "#8bd5ca", accent2: "#b7bdf8" },
  piano:     { accent: "#e5c890", accent2: "#a6d189" },
  deephouse: { accent: "#7dc4e4", accent2: "#f5bde6" },
  techno:    { accent: "#8aadf4", accent2: "#ed8796" },
  hardcore:  { accent: "#ff6b6b", accent2: "#ffcc4d" },
  trance:    { accent: "#7cf2ff", accent2: "#c792ff" },
  darkwave:  { accent: "#cfd8ff", accent2: "#8f9bd6" },
};
const ROLE_ENERGY = { beat: "kick", perc: "kick", bass: "bass", chords: "pad", keys: "pad", texture: "pad",
                      lead: "high", arp: "high", acid: "high" };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const params = new URLSearchParams(location.search);
if (params.get("capture")) document.body.classList.add("capture");

let ui = null;
let lastCode = {}, typing = {}, lineRatings = {}, ratedTrack = "";
let lastPhase = null, lastTitle = null, lastBar = -1;
let accent = STYLES.trance.accent, accent2 = STYLES.trance.accent2;

// ------------------------------------------------------------------ layout

function fit() {
  const s = Math.min(innerWidth / STAGE_W, innerHeight / STAGE_H);
  const stage = $("stage");
  stage.style.transform = `scale(${s})`;
  stage.style.left = `${(innerWidth - STAGE_W * s) / 2}px`;
  stage.style.top = `${(innerHeight - STAGE_H * s) / 2}px`;
}
addEventListener("resize", fit);

function cycleNow() {
  const c = ui && ui.clock;
  if (!c || !c.cps) return performance.now() / 2000;
  return (c.ref_cycle || 0) + (Date.now() / 1000 - (c.ref_time || 0)) * c.cps;
}

// ------------------------------------------------------------------ code

const FN = /^(n|s|note|scale|struct|jux|rev|every|off|slow|fast|range|sine|saw|tri|degradeBy|sometimesBy|swingBy|whenmod|echo|silence|pF|pI|xfadeIn|orbit|gain|lpf|hpf|room|size|delay|delaytime|delayfeedback|legato|pan|crush|shape|speed|resonance|setcps)$/;
function highlight(src) {
  let out = "";
  const re = /("[^"]*")|(\bd\d+\b)|(\$|#|\|\*|\|\+|\|<|<~|~>)|(-?\d+(?:\.\d+)?)|([A-Za-z_][\w]*)|(\s+)|(.)/g;
  let m;
  while ((m = re.exec(src))) {
    const [tok, str, ch, op, num, word, ws] = m;
    if (str) out += `<span class="t-str">${esc(str)}</span>`;
    else if (ch) out += `<span class="t-ch">${ch}</span>`;
    else if (op) out += `<span class="t-op">${esc(op)}</span>`;
    else if (num) out += `<span class="t-num">${num}</span>`;
    else if (word) out += FN.test(word) ? `<span class="t-fn">${word}</span>` : esc(word);
    else if (ws) out += ws;
    else out += `<span class="t-syn">${esc(tok)}</span>`;
  }
  return out;
}

// A knob's value in the code becomes a slider (Switch Angel's slider()):
// `# lpf 2400`, `# pF "acid" (slow 16 $ range 0.35 0.75 sine)`.
const KNOB_RE = /((?:#\s+|pF\s+")([a-z]+)"?\s+)(\(slow (\d+) \$ range (-?[\d.]+) (-?[\d.]+) (sine|saw|tri|cosine)\)|-?\d+(?:\.\d+)?)/g;
function renderSrc(text) {
  const ranges = (ui && ui.ranges) || {};
  let out = "", at = 0, m;
  KNOB_RE.lastIndex = 0;
  while ((m = KNOB_RE.exec(text))) {
    const [all, prefix, name, value, rate, lo, hi, shape] = m;
    const range = ranges[name];
    if (!range || name === "gain") continue;
    out += highlight(text.slice(at, m.index) + prefix);
    const data = rate ? `data-rate="${rate}" data-lo="${lo}" data-hi="${hi}" data-shape="${shape}"`
                      : `data-v="${value}"`;
    out += `<span class="sl${rate ? " moving" : ""}" data-min="${range[0]}" data-max="${range[1]}" ${data}>` +
           `<span class="trk"><span class="fill"></span><span class="knob"></span></span><b class="val"></b></span>`;
    at = m.index + all.length;
  }
  return out + highlight(text.slice(at));
}

function sliderValue(el, cycle) {
  if (el.dataset.v !== undefined) return parseFloat(el.dataset.v);
  const lo = parseFloat(el.dataset.lo), hi = parseFloat(el.dataset.hi), rate = parseFloat(el.dataset.rate) || 1;
  const ph = cycle / rate, f = ph - Math.floor(ph);
  const u = { sine: 0.5 + 0.5 * Math.sin(2 * Math.PI * ph), cosine: 0.5 + 0.5 * Math.cos(2 * Math.PI * ph),
              saw: f, tri: f < 0.5 ? f * 2 : 2 - f * 2 }[el.dataset.shape] ?? 0.5;
  return lo + (hi - lo) * u;
}

function updateSliders(cycle) {
  document.querySelectorAll("#code .sl").forEach((el) => {
    const v = sliderValue(el, cycle), min = parseFloat(el.dataset.min), max = parseFloat(el.dataset.max);
    const k = Math.max(0, Math.min(1, (v - min) / (max - min || 1)));
    el.querySelector(".knob").style.left = `${(k * 100).toFixed(1)}%`;
    el.querySelector(".fill").style.width = `${(k * 100).toFixed(1)}%`;
    el.querySelector(".val").textContent = Math.abs(v) >= 100 ? Math.round(v) : v.toFixed(2);
  });
}

function renderCode() {
  const t = ui.track || {};
  const title = t.title || "";
  if (title !== ratedTrack) { ratedTrack = title; lineRatings = {}; }
  const now = performance.now();
  // Just the code: the track, its key and the section are on the right, and
  // the reason for each move in the agent box.
  const rows = [];
  if (ui.tempo_line) rows.push({ cls: "", text: ui.tempo_line });
  let html = rows.map((r) => `<div class="ln ${r.cls}"><span class="no"></span>` +
                             `<span class="src">${r.cls ? esc(r.text) : highlight(r.text)}</span></div>`).join("");
  for (const line of ui.code || []) {
    const prev = lastCode[line.channel];
    if (prev !== undefined && prev !== line.text) typing[line.channel] = { text: line.text, start: now };
    lastCode[line.channel] = line.text;
    const r = lineRatings[line.role];
    const thumbs = line.role && line.role !== "arranger"
      ? `<span class="lrate${r ? " on" : ""}">` +
        `<button data-role="${esc(line.role)}" data-rating="1" class="${r > 0 ? "on" : ""}" title="I like the ${esc(line.role)}">👍</button>` +
        `<button data-role="${esc(line.role)}" data-rating="-1" class="${r < 0 ? "on" : ""}" title="I don't like the ${esc(line.role)}">👎</button></span>` : "";
    const cls = ["ln", line.muted ? "muted" : "", line.edited ? "edited" : "", typing[line.channel] ? "fresh" : ""].join(" ");
    const src = typing[line.channel] ? "" : renderSrc(line.text);
    html += `<div class="${cls}" data-ch="${line.channel}"><span class="no">${line.channel}</span>` +
            `<span class="src">${line.muted ? "-- " : ""}${src}</span>${thumbs}` +
            (line.role !== "arranger" && !line.muted ? `<canvas class="roll" data-ch="${line.channel}"></canvas>` : "") +
            `</div>`;
  }
  $("code").innerHTML = html;
  fitCode();
}

// A full peak doesn't fit at the normal size: shrink the code until it does.
function fitCode() {
  const box = $("code");
  let size = 25;
  box.style.fontSize = `${size}px`;
  while (box.scrollHeight > box.clientHeight + 1 && size > 14) {
    size -= 1;
    box.style.fontSize = `${size}px`;
    box.querySelectorAll(".roll").forEach((c) => { c.style.height = `${Math.round(size * 1.5)}px`; });
  }
}

// A freshly edited line types itself out.
function animateTyping() {
  const now = performance.now();
  for (const ch of Object.keys(typing)) {
    const { text, start } = typing[ch];
    const el = document.querySelector(`.ln[data-ch="${ch}"] .src`);
    const k = Math.min(1, (now - start) / 1400);
    if (!el || k >= 1) { if (el) el.innerHTML = renderSrc(text); delete typing[ch]; continue; }
    el.innerHTML = highlight(text.slice(0, Math.floor(text.length * (0.1 + 0.9 * k)))) + '<span class="caret"></span>';
  }
}

// ------------------------------------------------------------------ piano rolls
// Two bars around now, the playhead in the middle; a note lights up while it
// sounds. Pitched lines by note, drum lines one row per sound.

function drawRolls(cycle) {
  const rolls = ui && ui.rolls;
  document.querySelectorAll("#code canvas.roll").forEach((c) => {
    const W = c.clientWidth, H = c.clientHeight;
    if (!W || !H) return;
    if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
    const g = c.getContext("2d");
    g.clearRect(0, 0, W, H);
    g.fillStyle = "rgba(120, 190, 240, 0.06)"; g.fillRect(0, 0, W, H);
    const events = rolls && rolls.lines && rolls.lines[c.dataset.ch];
    const from = cycle - 1, span = 2;
    // Bar and beat lines.
    for (let b = Math.ceil(from * 4); b <= (from + span) * 4; b++) {
      const x = ((b / 4 - from) / span) * W;
      g.fillStyle = b % 4 === 0 ? "rgba(120,190,240,0.28)" : "rgba(120,190,240,0.09)";
      g.fillRect(Math.round(x), 0, 1, H);
    }
    if (events && events.length) {
      const pitched = events.some((e) => e[2] !== null);
      let lo = Infinity, hi = -Infinity;
      const sounds = [];
      for (const e of events) {
        if (pitched && e[2] !== null) { lo = Math.min(lo, e[2]); hi = Math.max(hi, e[2]); }
        if (!pitched && !sounds.includes(e[3])) sounds.push(e[3]);
      }
      const rows = pitched ? Math.max(1, hi - lo + 1) : Math.max(1, sounds.length);
      const rh = Math.max(4, Math.min((H - 4) / rows + 1, 10));
      for (const e of events) {
        if (e[1] < from || e[0] > from + span) continue;
        // Drums are hits (a 16th wide); notes as long as they sound.
        const len = pitched ? e[1] - e[0] : Math.min(e[1] - e[0], 1 / 16);
        const x = ((e[0] - from) / span) * W, w = Math.max(3, (len / span) * W - 2);
        const row = pitched ? (e[2] === null ? 0 : e[2] - lo) : sounds.indexOf(e[3]);
        const y = H - 2 - ((row + 0.5) / rows) * (H - 4) - rh / 2;
        const on = e[0] <= cycle && cycle < e[1];
        g.fillStyle = on ? "#ffffff" : e[0] < cycle ? hexA(accent, 0.35) : hexA(accent, 0.85);
        if (on) { g.shadowColor = accent; g.shadowBlur = 14; }
        g.fillRect(x, y, w, rh);
        g.shadowBlur = 0;
      }
    }
    // The playhead.
    g.fillStyle = "rgba(255,255,255,0.85)"; g.fillRect(Math.round(W / 2), 0, 2, H);
  });
}

function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`;
}

// ------------------------------------------------------------------ the visual
// A cloud of glowing pixels: it breathes with the bass, jolts on every kick,
// spreads as more lines play, and sparkles with the high parts.

const PARTICLES = Array.from({ length: 900 }, (_, i) => {
  const u = Math.random() * 2 - 1, phi = Math.random() * Math.PI * 2, r = Math.cbrt(Math.random());
  return { x: Math.sqrt(1 - u * u) * Math.cos(phi) * r, y: u * r, z: Math.sqrt(1 - u * u) * Math.sin(phi) * r,
           s: 1 + Math.floor(Math.random() * 3), seed: Math.random() * 100 };
});
let energy = { kick: 0, bass: 0, pad: 0, high: 0 };

function targetEnergy() {
  const e = { kick: 0, bass: 0, pad: 0, high: 0 };
  for (const line of (ui && ui.code) || []) {
    const k = ROLE_ENERGY[line.role];
    if (k && !line.muted) e[k] = Math.min(1, e[k] + (k === "pad" || k === "high" ? 0.5 : 1));
  }
  return e;
}

function drawVisual(cycle, kick) {
  const c = $("vis"), g = c.getContext("2d"), W = c.width, H = c.height;
  const target = targetEnergy();
  for (const k in energy) energy[k] += (target[k] - energy[k]) * 0.04;
  g.globalCompositeOperation = "source-over";
  g.fillStyle = "rgba(2, 4, 7, 0.35)";                  // trails
  g.fillRect(0, 0, W, H);
  g.globalCompositeOperation = "lighter";
  const t = performance.now() / 1000;
  const rot = t * 0.25 + cycle * 0.05, cr = Math.cos(rot), sr = Math.sin(rot);
  const size = H * (0.26 + 0.08 * energy.pad + 0.06 * energy.bass * Math.sin(cycle * Math.PI) + 0.1 * kick * energy.kick);
  for (const p of PARTICLES) {
    const wob = 0.12 * energy.high * Math.sin(t * 6 + p.seed);
    const x = p.x * cr - p.z * sr, z = p.x * sr + p.z * cr;
    const d = 1 / (1.8 + z);
    const px = W / 2 + (x + wob) * size * 1.6 * d * 1.8, py = H / 2 + (p.y + wob * 0.6) * size * d * 1.8;
    const near = 0.35 + 0.65 * (1 - (z + 1) / 2);
    const twinkle = energy.high > 0.1 && Math.sin(t * 9 + p.seed * 7) > 0.96 ? 1 : 0;
    g.fillStyle = twinkle ? "rgba(255,255,255,0.95)" : hexA(p.seed > 80 ? accent2 : accent, 0.22 + 0.55 * near * (0.5 + 0.5 * kick));
    g.fillRect(Math.round(px), Math.round(py), p.s, p.s);
  }
}

// ------------------------------------------------------------------ side panel

function renderSide() {
  const t = ui.track || {};
  $("now").innerHTML = t.title
    ? `<div class="title">${esc(t.title)}</div><div class="meta">${t.subgenre ? esc(t.subgenre) + " · " : ""}${esc(t.key || "")} · ${Math.round(t.bpm || 0)} bpm · ${esc(PHASE_NAMES[ui.phase] || "")}</div>`
    : "";
  const plan = ui.plan || [];
  $("arc").style.gridTemplateColumns = `repeat(${Math.max(1, plan.length)}, 1fr)`;
  $("arc").innerHTML = plan.map((s, i) => `<div class="step ${i < (ui.step || 0) ? "done" : i === (ui.step || 0) ? "now" : ""}"></div>`).join("");

  const st = STYLES[t.style] || STYLES.trance;
  accent = st.accent; accent2 = st.accent2;
  document.documentElement.style.setProperty("--accent", accent);
  document.documentElement.style.setProperty("--accent2", accent2);
  if (ui.channel) {
    document.querySelectorAll(".channel-name").forEach((el) => { el.textContent = ui.channel; el.dataset.text = ui.channel; });
    document.title = `${ui.channel} · live`;
  }
  fitLogo();
}

function updateArcFill(cycle) {
  const step = ui && ui.plan && ui.plan[ui.step || 0], el = document.querySelector("#arc .step.now");
  if (step && el && step.bars) el.style.setProperty("--p", Math.min(1, Math.max(0, cycle - (ui.phase_start || 0)) / step.bars).toFixed(3));
}

// The agents where Switch Angel's camera is: the Director's instruction,
// then the code the Performer wrote for it (or its own move: what it heard,
// what it played, why).
let agentKey = "";
function renderAgent() {
  const events = ui.events || [];
  const last = [...events].reverse().find((e) => e.agent === "performer" || e.agent === "request" || e.agent === "cue");
  const said = [...events].reverse().find((e) => e.agent === "narrator");
  const thinking = Object.entries(ui.thinking || {});
  const model = (ui.models && ui.models.performer) || "built-in";
  $("agent-head").innerHTML = `<span class="rec"></span>DIRECTOR → PERFORMER<span class="model">${esc(model)}</span>`;
  const key = JSON.stringify([last && last.id, said && said.id, thinking.map(([a]) => a)]);
  if (key === agentKey) return;
  agentKey = key;
  let html = "";
  if (last && last.agent === "cue") {
    html += `<div class="row cue"><span class="k">DIRECTOR ›</span> <span class="say">${esc(last.text)}</span></div>`;
    html += `<div class="row"><span class="k">code ›</span> <span class="move">${esc(last.edit || "")}</span></div>`;
    if (last.review) html += `<div class="row"><span class="k">done ›</span> <span class="why">${esc(last.review)}</span></div>`;
  } else if (last) {
    if (last.listening) html += `<div class="row"><span class="k">hears ›</span> <span class="hears">${esc(last.listening)}</span></div>`;
    if (last.agent === "request") html += `<div class="row"><span class="k">asked ›</span> <span class="say">${esc(last.text)}</span></div>`;
    html += `<div class="row"><span class="k">plays ›</span> <span class="move">${esc(last.edit || "")}</span></div>`;
    const why = last.reason || last.review;
    if (why) html += `<div class="row"><span class="k">why ›</span> <span class="why">${esc(why)}</span></div>`;
  }
  if (said) html += `<div class="row"><span class="k">says ›</span> <span class="say">“${esc(said.text)}”</span></div>`;
  for (const [agent] of thinking) html += `<div class="row thinking">${esc(agent)} is thinking<span class="caret"></span></div>`;
  $("agent").innerHTML = html || `<div class="row thinking">warming up<span class="caret"></span></div>`;
}

// ------------------------------------------------------------------ the dancer
// She waves hello as a track builds and goodbye in its outro, dances through
// the groove and the breakdown, and gives it all at the peak. Each loop is
// played at the speed that makes it last a whole number of bars.
const DANCE_FOR = { build: "wave", groove: "dance", breakdown: "dance", rebuild: "dance", groove2: "groove",
                    outro: "wave" };
let dancing = null;

function updateDancer() {
  const clip = DANCE_FOR[ui && ui.phase] || "dance";
  const cps = ui && ui.clock && ui.clock.cps;
  document.querySelectorAll("#dancer video").forEach((v) => {
    const on = v.dataset.clip === clip;
    v.classList.toggle("on", on);
    if (on && cps && v.duration) {
      const bar = 1 / cps, bars = Math.max(1, Math.round(v.duration / bar));
      v.playbackRate = Math.min(1.5, Math.max(0.6, v.duration / (bars * bar)));
    }
    if (on && v.paused) v.play().catch(() => {});
    if (!on && !v.paused) v.pause();
  });
  dancing = clip;
}

// ------------------------------------------------------------------ glyphs at the viewer
// When something unique happens (a surprise) the screen throws its code at
// you: tokens of the live program, a few math signs and the surprise's name
// start far off and fly towards the camera in perspective, growing, drifting,
// turning a little, glowing, and vanish just before they'd hit the glass.

const MATH = ["Σ", "∫", "∂", "λ", "π", "∞", "≈", "√", "Δ", "ƒ", "∑", "θ", "∮"];
let glyphs = [];

function codeTokens() {
  const tokens = [];
  for (const line of (ui && ui.code) || []) {
    for (const t of line.text.split(/\s+/)) {
      const tok = t.replace(/^[("]+|[)",]+$/g, "");
      if (tok && tok.length <= 14) tokens.push(tok);
    }
  }
  return tokens.length ? tokens : ["n", "scale", "|+", "<~", "slow", "sine", "#", "$"];
}

function burst(label) {
  const tokens = codeTokens(), now = performance.now();
  for (let i = 0; i < 70; i++) {
    const r = Math.random();
    const text = r < 0.2 ? MATH[Math.floor(Math.random() * MATH.length)]
               : tokens[Math.floor(Math.random() * tokens.length)];
    glyphs.push({
      text, born: now + Math.random() * 900,               // a stream, not one wall
      x: (Math.random() * 2 - 1) * 1.6, y: (Math.random() * 2 - 1) * 0.9,
      z: 7 + Math.random() * 5, speed: 3.2 + Math.random() * 2.6,
      size: (r < 0.2 ? 120 : 52) + Math.random() * 30, spin: (Math.random() - 0.5) * 0.6,
      tint: Math.random() < 0.7 ? 0 : 1, mono: r >= 0.2,
    });
  }
  if (label) {                                                  // its own name, biggest, straight at you
    glyphs.push({ text: label, born: now + 150, x: 0, y: -0.05, z: 9, speed: 4.2, size: 150, spin: 0, tint: 0,
                  mono: false, title: true });
  }
}

function drawGlyphs(dt) {
  const c = $("fly"), g = c.getContext("2d"), W = c.width, H = c.height, now = performance.now();
  g.clearRect(0, 0, W, H);
  if (!glyphs.length) return;
  g.globalCompositeOperation = "lighter";
  g.textAlign = "center"; g.textBaseline = "middle";
  const focal = 1.6;
  glyphs = glyphs.filter((p) => p.z > 0.35);
  for (const p of glyphs) {
    if (now < p.born) continue;
    p.z -= p.speed * dt;
    p.x += p.x * 0.05 * dt; p.y += p.y * 0.05 * dt;         // spread outwards as they come
    const scale = focal / p.z;
    const sx = W / 2 + p.x * W * 0.5 * scale, sy = H / 2 + p.y * H * 0.5 * scale;
    // Fade in from the distance, out before the glass.
    const alpha = Math.min(1, (12 - p.z) / 2.5) * Math.min(1, (p.z - 0.35) / 1.1) * (p.title ? 1 : 0.9);
    if (alpha <= 0) continue;
    g.save();
    g.translate(sx, sy);
    g.rotate(p.spin * (1 / p.z));
    g.font = `${p.title ? 700 : 400} ${Math.round(p.size * scale)}px ${p.mono ? "JetBrains Mono" : "Silkscreen"}, monospace`;
    const colour = p.tint ? accent2 : accent;
    g.shadowColor = colour; g.shadowBlur = 26 * scale;
    g.fillStyle = hexA(p.title ? "#ffffff" : colour, alpha);
    g.fillText(p.text, 0, 0);
    g.restore();
  }
  g.globalCompositeOperation = "source-over";
}

// ------------------------------------------------------------------ moments

function moment(text, big) {
  const m = $("moment"), span = m.querySelector("span");
  span.textContent = text;
  m.classList.remove("on"); void m.offsetWidth; m.classList.add("on");
  if (big) {
    const f = $("flash"); f.classList.remove("on"); void f.offsetWidth; f.classList.add("on");
    const s = $("stage"); s.classList.remove("shake"); void s.offsetWidth; s.classList.add("shake");
    const d = $("dancer"); d.classList.remove("jump"); void d.offsetWidth; d.classList.add("jump");
    glitchLogo(true);
  }
}

// The name fills its column whatever its length.
function fitLogo() {
  // Measure the text itself (a centred flex box hides overflow on the left
  // from scrollWidth, so a long name was cut instead of shrunk).
  const el = $("logo"), g = document.createElement("canvas").getContext("2d");
  const style = getComputedStyle(el), room = el.clientWidth - 16;
  let size = 52;
  const width = (px) => { g.font = `${style.fontWeight} ${px}px ${style.fontFamily}`; return g.measureText(el.textContent).width; };
  while (width(size) > room && size > 18) size -= 2;
  el.style.fontSize = `${size}px`;
}
// The pixel font may arrive after the first fit: fit again once it has.
if (document.fonts) document.fonts.ready.then(() => fitLogo());

function glitchLogo(hard) {
  const el = $("logo");
  el.classList.remove("glitch", "hard"); void el.offsetWidth;
  el.classList.add("glitch"); if (hard) el.classList.add("hard");
}

// The art director's surprises get their moment when they fire (only the
// ones that happen while the page is open: old ones aren't replayed).
let lastSurprise = null;
const BIG_SURPRISES = ["fake_drop", "kickless_drop", "silent_bar", "key_lift", "stripped_peak"];
function checkSurprises() {
  const events = (ui.events || []).filter((e) => e.agent === "surprise");
  const newest = events.length ? events[events.length - 1] : null;
  if (lastSurprise === null) { lastSurprise = newest ? newest.id : 0; return; }
  if (newest && newest.id > lastSurprise) {
    lastSurprise = newest.id;
    moment(newest.label || "SURPRISE", BIG_SURPRISES.includes(newest.name));
    burst(newest.label);
  }
}

function checkMoments() {
  const title = ui.track && ui.track.title;
  if (lastTitle !== null && title && title !== lastTitle) moment(`NEW TRACK\n${title.toUpperCase()}`, false);
  else if (lastPhase !== null && ui.phase !== lastPhase && MOMENTS[ui.phase]) moment(MOMENTS[ui.phase], ui.phase === "groove2");
  lastTitle = title || lastTitle; lastPhase = ui.phase;
}

// ------------------------------------------------------------------ loop

function frame() {
  const cycle = cycleNow(), beat = cycle * 4;
  const kick = Math.pow(1 - (beat - Math.floor(beat)), 3) * Math.min(1, energy.kick + 0.15);
  document.documentElement.style.setProperty("--kick", kick.toFixed(3));
  const bar = Math.floor(cycle);
  if (bar !== lastBar) { if (lastBar >= 0 && energy.kick > 0.3) glitchLogo(false); lastBar = bar; }
  if (ui) {
    animateTyping();
    updateSliders(cycle);
    drawRolls(cycle);
    updateArcFill(cycle);
  }
  drawVisual(cycle, kick);
  const t = performance.now(), dt = Math.min(0.05, (t - (frame.last || t)) / 1000);
  frame.last = t;
  drawGlyphs(dt);
  requestAnimationFrame(frame);
}

async function poll() {
  try {
    const r = await fetch(`ui.json?t=${Date.now()}`, { cache: "no-store" });
    if (r.ok) {
      const next = await r.json();
      const changed = !ui || next.updated !== ui.updated;
      ui = next;
      if (changed) { renderSide(); renderCode(); checkMoments(); updateDancer(); }
      renderAgent();
      checkSurprises();
    }
  } catch (e) { /* engine not up yet: keep the last frame */ }
  setTimeout(poll, POLL_MS);
}

// ------------------------------------------------------------------ host controls (not on the stream)

async function send(path, body, ok) {
  const note = $("ask-note");
  try {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    note.textContent = r.ok ? ok : "couldn't send it";
    return r.ok;
  } catch (err) {
    note.textContent = "couldn't reach the page server";
    return false;
  } finally {
    setTimeout(() => { note.textContent = ""; }, 6000);
  }
}
document.querySelectorAll("#ask .rate").forEach((b) => b.addEventListener("click", () =>
  send("/rate", { rating: Number(b.dataset.rating) }, b.dataset.rating > 0 ? "👍 noted" : "👎 noted")));
$("code").addEventListener("click", async (e) => {
  const b = e.target.closest(".lrate button");
  if (!b) return;
  const rating = Number(b.dataset.rating), role = b.dataset.role;
  if (await send("/rate", { rating, role }, `${rating > 0 ? "👍" : "👎"} the ${role}: the performer hears it`)) {
    lineRatings[role] = rating; renderCode();
  }
});
$("ask").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("ask-text"), text = input.value.trim();
  if (text && await send("/request", { text }, "sent: the performer takes it at the next edit")) input.value = "";
});

// A clip that finishes loading late gets its beat-synced speed then.
document.querySelectorAll("#dancer video").forEach((v) => v.addEventListener("loadedmetadata", () => ui && updateDancer()));

fit();
poll();
requestAnimationFrame(frame);

// ?moment=DROP previews a moment (with the drop's flash and shake), e.g. to
// check the look without waiting for the arc to get there.
if (params.get("moment")) setTimeout(() => {
  const text = params.get("moment").replace(/\\n/g, "\n");
  moment(text, text === "DROP");
  burst(text);
  // &advance=1.4: the burst as it looks that many seconds in (for stills).
  const ahead = parseFloat(params.get("advance") || "0");
  if (ahead > 0) {
    const now = performance.now();
    for (const p of glyphs) {
      const age = ahead - (p.born - now) / 1000;           // how long it has been flying by then
      if (age > 0) { p.z -= p.speed * age; p.x *= 1 + 0.05 * age; p.y *= 1 + 0.05 * age; }
      p.born = Math.min(p.born, now);
    }
  }
}, 300);
