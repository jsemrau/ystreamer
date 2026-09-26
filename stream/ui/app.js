// sleepless composer: stream UI.
// Polls ui.json (written by the engine, served by stream/ui_server.py) and
// draws the live code, the agents' decisions and a beat-synced visual.
"use strict";

const STAGE_W = 1920, STAGE_H = 1080;
const POLL_MS = 500;
const PHASE_NAMES = { build: "build", groove: "groove", breakdown: "breakdown", rebuild: "rebuild",
                      groove2: "peak", outro: "outro" };

// Colours per style (the 3D wave field takes them).
const STYLES = {
  lofi:      { accent: "#ffb870", accent2: "#ff7eb6", base: ["#1a1020", "#2a1a24"] },
  downtempo: { accent: "#f5a97f", accent2: "#c6a0f6", base: ["#12101c", "#241a2a"] },
  ambient:   { accent: "#8bd5ca", accent2: "#b7bdf8", base: ["#050a14", "#0b1624"] },
  piano:     { accent: "#e5c890", accent2: "#a6d189", base: ["#0b0a10", "#161420"] },
  deephouse: { accent: "#7dc4e4", accent2: "#f5bde6", base: ["#070914", "#101830"] },
  techno:    { accent: "#8aadf4", accent2: "#ed8796", base: ["#050507", "#0e0e16"] },
  hardcore:  { accent: "#ff5c5c", accent2: "#ffcc4d", base: ["#0a0303", "#1a0606"] },
  trance:    { accent: "#7cf2ff", accent2: "#c792ff", base: ["#04040f", "#0c0a26"] },
  darkwave:  { accent: "#cfd8ff", accent2: "#8f9bd6", base: ["#030305", "#0a0b10"] },
};

const ICONS = {
  director:  '<svg viewBox="0 0 16 16"><path fill="currentColor" d="M8 1l2.2 4.6 5 .7-3.6 3.5.9 5L8 12.4 3.5 14.8l.9-5L.8 6.3l5-.7z"/></svg>',
  performer: '<svg viewBox="0 0 16 16"><path fill="currentColor" d="M11.5 1.5l3 3L5 14H2v-3z"/></svg>',
  judge:     '<svg viewBox="0 0 16 16"><path fill="currentColor" d="M7 1h2v2h5v2h-1l2 5a3 3 0 01-6 0l2-5H9v8h3v2H4v-2h3V5H5l2 5a3 3 0 01-6 0l2-5H2V3h5z"/></svg>',
  narrator:  '<svg viewBox="0 0 16 16"><path fill="currentColor" d="M2 3h12v8H7l-4 3v-3H2z"/></svg>',
  arc:       '<svg viewBox="0 0 16 16"><path fill="currentColor" d="M1 11c3-7 6-7 7-2s4 5 7-2v5H1z"/></svg>',
  track:     '<svg viewBox="0 0 16 16"><path fill="currentColor" d="M6 2l8-1v10a2 2 0 11-2-2V4L7 5v8a2 2 0 11-2-2V2z"/></svg>',
};
const AGENT_NAMES = { director: "Director", performer: "Performer", judge: "Judge", narrator: "Narrator",
                      arc: "Arc", track: "New track" };

let ui = null;              // latest ui.json
let shownEvents = new Set();
let lastCode = {};          // channel -> text, to spot edits
let typing = {};            // channel -> {text, start}
let intensity = 0.8, targetIntensity = 0.8;

// ------------------------------------------------------------------ helpers

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const POSTER = new URLSearchParams(location.search).get("poster");
const BANNER = new URLSearchParams(location.search).get("banner");
function fit() {
  if (POSTER || BANNER) {           // thumbnail 1280x720, channel banner 2048x1152, exactly
    const [w, h] = BANNER ? [2048, 1152] : [1280, 720];
    const c = $("bg");
    c.style.width = `${w}px`; c.style.height = `${h}px`; c.width = w; c.height = h;
    return;
  }
  const s = Math.min(innerWidth / STAGE_W, innerHeight / STAGE_H);
  const stage = $("stage");
  stage.style.transform = `scale(${s})`;
  stage.style.left = `${(innerWidth - STAGE_W * s) / 2}px`;
  stage.style.top = `${(innerHeight - STAGE_H * s) / 2}px`;
  const c = $("bg");
  // The background renders at 60% resolution and is scaled up: it is soft
  // anyway, and this keeps the frame rate up when streaming without a GPU.
  c.width = Math.round(innerWidth * 0.6);
  c.height = Math.round(innerHeight * 0.6);
}
addEventListener("resize", fit);

function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 10) return "now";
  if (s < 60) return `${Math.round(s)}s ago`;
  return `${Math.round(s / 60)}m ago`;
}

// Tidal syntax highlighting (mini-notation strings, numbers, functions, operators).
const FN = /\b(n|s|note|scale|struct|jux|rev|every|off|slow|fast|range|sine|saw|tri|degradeBy|sometimesBy|swingBy|whenmod|echo|silence|pF|pI|xfadeIn|orbit|gain|lpf|hpf|room|size|delay|delaytime|delayfeedback|legato|pan|crush|shape|speed|resonance)\b/g;
function highlight(src) {
  let out = "", i = 0;
  const re = /("[^"]*")|(\bd\d\b)|(\$|#|\|\*|\|\+|\|<|<~|~>)|(-?\d+(?:\.\d+)?)|([A-Za-z_][\w]*)|(\s+)|(.)/g;
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
    FN.lastIndex = 0;
    i++;
  }
  return out;
}

// ---------------------------------------------------------------- rendering

function renderTop() {
  const t = ui.track || {};
  $("title").textContent = t.title || "…";
  const chips = [];
  if (t.style) chips.push(`<span class="chip"><b>${esc(t.style)}</b></span>`);
  if (t.key) chips.push(`<span class="chip">${esc(t.key)}</span>`);
  if (t.bpm) chips.push(`<span class="chip"><b>${Math.round(t.bpm)}</b> bpm</span>`);
  if (t.mood) chips.push(`<span class="chip">${esc(t.mood)}</span>`);
  $("chips").innerHTML = chips.join("");

  const phases = ui.phases || Object.keys(PHASE_NAMES);
  const at = phases.indexOf(ui.phase);
  $("arc").innerHTML = phases.map((p, i) =>
    `<div class="step ${i < at ? "done" : i === at ? "now" : ""}">${PHASE_NAMES[p] || p}</div>`).join("");

  const st = STYLES[t.style] || STYLES.techno;
  document.documentElement.style.setProperty("--accent", st.accent);
  document.documentElement.style.setProperty("--accent2", st.accent2);
  targetIntensity = { build: 0.55, groove: 0.85, breakdown: 0.3, rebuild: 0.65, groove2: 1.0, outro: 0.35 }[ui.phase] ?? 0.7;
}

function renderCode() {
  $("tempo").innerHTML = highlight(ui.tempo_line || "");
  const now = performance.now();
  const rows = (ui.code || []).map((line) => {
    const prev = lastCode[line.channel];
    if (prev !== undefined && prev !== line.text && line.edited) typing[line.channel] = { text: line.text, start: now };
    if (prev === undefined && Object.keys(lastCode).length) typing[line.channel] = { text: line.text, start: now };
    lastCode[line.channel] = line.text;
    return line;
  });
  const html = rows.map((line) => {
    const cls = ["line", line.edited ? "edited" : "", line.muted ? "muted" : "",
                 typing[line.channel] ? "fresh" : ""].join(" ");
    return `<div class="${cls}" data-ch="${line.channel}"><span class="no">${line.channel}</span>` +
           `<span class="src">${line.muted ? "-- " : ""}${highlight(line.text)}</span></div>`;
  }).join("");
  $("code").innerHTML = html || `<div class="line"><span class="no"></span><span class="src" style="color:var(--muted)">-- an empty room. the first line is on its way…</span></div>`;
  $("comment").textContent = ui.comment ? `-- ${ui.comment}` : "";
  fitCode();
}

// A full peak (7+ lines) doesn't fit at the normal size: shrink the code
// until every line shows.
function fitCode() {
  const box = $("code");
  let size = 20;
  box.style.fontSize = "";
  box.querySelectorAll(".line").forEach((el) => { el.style.fontSize = ""; });
  while (box.scrollHeight > box.clientHeight + 1 && size > 13) {
    size -= 1;
    box.querySelectorAll(".line").forEach((el) => { el.style.fontSize = `${size}px`; el.style.paddingTop = el.style.paddingBottom = `${Math.round(size / 4)}px`; });
  }
}

// Typewriter for freshly edited lines (runs every frame for ~1.5 s).
function animateTyping() {
  const now = performance.now();
  for (const ch of Object.keys(typing)) {
    const { text, start } = typing[ch];
    const el = document.querySelector(`.line[data-ch="${ch}"] .src`);
    const k = Math.min(1, (now - start) / 1500);
    if (!el || k >= 1) { if (el) el.innerHTML = highlight(text); delete typing[ch]; continue; }
    const n = Math.floor(text.length * (0.15 + 0.85 * k));
    el.innerHTML = highlight(text.slice(0, n)) + '<span class="caret"></span>';
  }
}

function card(ev, old, fresh) {
  const kind = ev.agent;
  const colour = { director: "var(--director)", performer: "var(--performer)", judge: "var(--judge)",
                   narrator: "var(--narrator)", arc: "var(--arc)", track: "var(--accent)" }[kind] || "var(--accent)";
  let meta = "", body = "";
  if (kind === "performer") {
    const src = ev.source || "";
    const badge = src.startsWith("LLM") ? `<span class="badge ai">AI${src.includes("judged") ? " · judged" : ""}</span>`
                : src === "jevtypesafe" ? `<span class="badge ai">decision AI</span>` : `<span class="badge random">built-in</span>`;
    meta = badge;
    body = (ev.listening ? `<div class="hears">${esc(ev.listening)}</div>` : "") +
           `<div class="move">${esc(ev.edit)}</div>` +
           (ev.reason ? `<div class="why">${esc(ev.reason)}</div>` : "");
  } else if (kind === "judge") {
    const opts = (ev.options || []).slice(0, 6);
    meta = ev.confidence != null ? `<span class="badge">confidence ${Math.round(ev.confidence * 100)}%</span>` : "";
    body = `<div class="opts">` + opts.map((o, i) => {
      const p = o.p != null ? Math.round(o.p * 100) : null;
      return `<div class="opt ${i === ev.chosen ? "pick" : ""}"><span class="txt">${i === ev.chosen ? "▸ " : ""}${esc(o.text)}</span>` +
             `<span class="bar"><i style="width:${p ?? 0}%"></i></span>` +
             `<span class="src"><span class="badge ${o.from}">${o.from === "ai" ? "AI" : "random"}${p != null ? " " + p + "%" : ""}</span></span></div>`;
    }).join("") + `</div>`;
  } else if (kind === "director") {
    meta = `<span class="badge">${esc(ev.model || "")}</span>`;
    const parts = String(ev.decision || "").split(/,\s*/).map((p) => `<span>${esc(p.replace(/^'|'$/g, ""))}</span>`).join("");
    body = `<div class="plan">${parts}</div>` + (ev.reason ? `<div class="why">${esc(ev.reason)}</div>` : "");
  } else if (kind === "narrator") {
    body = `<div class="quote">“${esc(ev.text)}”</div>`;
  } else if (kind === "arc") {
    body = `<div class="why"><b>${esc(PHASE_NAMES[ev.before] || ev.before)} → ${esc(PHASE_NAMES[ev.after] || ev.after)}</b></div>` +
           (ev.why ? `<div class="note">${esc(ev.why)}</div>` : "");
  } else if (kind === "track") {
    body = `<div class="why"><b>${esc(ev.title)}</b> · ${esc(ev.style)} · ${esc(ev.key)} · ${Math.round(ev.bpm || 0)} bpm</div>`;
  }
  return `<div class="card ${old ? "old" : ""} ${fresh ? "new" : ""}" style="--c:${colour}" data-id="${ev.id}">` +
         `<div class="who"><span class="av">${ICONS[kind] || ""}</span><b>${AGENT_NAMES[kind] || kind}</b>${meta}` +
         `<span class="time">${ago(ev.t)}</span></div><div class="body">${body}</div></div>`;
}

let feedKey = "";
function renderFeed() {
  // Newest first; only rebuilt when the events change (so cards don't
  // re-animate on every poll), and only new cards slide in.
  const events = (ui.events || []).slice(-6).reverse();
  const key = events.map((e) => e.id).join(",");
  if (key !== feedKey) {
    feedKey = key;
    $("feed").innerHTML = events.map((ev, i) => card(ev, i >= 3, !shownEvents.has(ev.id))).join("");
    events.forEach((ev) => shownEvents.add(ev.id));
  } else {
    document.querySelectorAll("#feed .card").forEach((el) => {
      const ev = events.find((e) => String(e.id) === el.dataset.id);
      if (ev) el.querySelector(".time").textContent = ago(ev.t);
    });
  }

  const thinking = ui.thinking || {};
  $("thinking").innerHTML = Object.entries(thinking).map(([agent, t]) =>
    `<div class="think"><span class="dots"><span></span><span></span><span></span></span>` +
    `<span><b>${esc(agent)}</b> is thinking with ${esc(t.model)}…</span>` +
    `<span style="margin-left:auto">${Math.round(Math.max(0, Date.now() / 1000 - t.since))}s</span></div>`).join("");
}

function renderFooter() {
  const n = $("narration");
  n.textContent = ui.narration ? `“${ui.narration}”` : n.textContent;
  n.classList.toggle("on", !!ui.narration);
  const s = ui.stats || {};
  const chips = [];
  if (ui.models && ui.models.performer) chips.push(`<span class="chip">performer <b>${esc(ui.models.performer)}</b></span>`);
  if (ui.models && ui.models.judge) chips.push(`<span class="chip">judge <b>${esc(ui.models.judge)}</b></span>`);
  if (s.edits) chips.push(`<span class="chip">AI edits <b>${s.llm}/${s.edits}</b></span>`);
  // A percentage of one or two comparisons says nothing: show the count
  // until there are a few, then both.
  if (s.judged) chips.push(`<span class="chip">judge prefers AI <b>${s.judge_llm}/${s.judged}` +
                           (s.judged >= 5 ? ` · ${Math.round(100 * s.judge_llm / s.judged)}%` : "") + `</b></span>`);
  $("stats").innerHTML = chips.join("");
}

async function poll() {
  try {
    const r = await fetch(`ui.json?t=${Date.now()}`, { cache: "no-store" });
    if (r.ok) {
      const next = await r.json();
      const changed = !ui || next.updated !== ui.updated;
      ui = next;
      if (params.get("style")) ui.track = Object.assign({}, ui.track || {}, { style: params.get("style") });
      if (changed) { renderTop(); renderCode(); renderFooter(); renderPoster(); }
      renderFeed();      // also refreshes "time ago" and thinking timers
    }
  } catch (e) { /* engine not up yet: keep the last frame */ }
  setTimeout(poll, POLL_MS);
}

// ------------------------------------------------------------- the visual

function beatPhase() {
  const c = ui && ui.clock;
  if (!c || !c.cps) return { beat: performance.now() / 500, bar: performance.now() / 2000 };
  // Map the engine's clock (a wall-clock anchor + cycles) to now. In a dry run
  // the anchor is simulated time, which only shifts the phase.
  const elapsed = Date.now() / 1000 - (c.ref_time || 0);
  const cycle = (c.ref_cycle || 0) + elapsed * c.cps;
  return { beat: cycle * 4, bar: cycle };
}

// Deterministic noise for the waves.
function hash(n) { const x = Math.sin(n * 127.1) * 43758.5453; return x - Math.floor(x); }
function noise(x) { const i = Math.floor(x), f = x - i, u = f * f * (3 - 2 * f); return hash(i) * (1 - u) + hash(i + 1) * u; }

// What is playing, as wave energy: the beat pulses, the bass swells, the
// chords roll, and lead / arp / acid ripple fast. From the live code (roles
// that play and aren't muted), eased so a new line swells in.
const ROLE_WAVES = { beat: "kick", perc: "kick", bass: "bass", chords: "pad", keys: "pad", texture: "pad",
                     lead: "high", arp: "high", acid: "high" };
let energy = { kick: 0, bass: 0, pad: 0, high: 0 };

function targetEnergy() {
  const e = { kick: 0, bass: 0, pad: 0, high: 0 };
  for (const line of (ui && ui.code) || []) {
    const kind = ROLE_WAVES[line.role];
    if (kind && !line.muted) e[kind] = Math.min(1, e[kind] + (kind === "pad" || kind === "high" ? 0.55 : 1));
  }
  if (document.body.classList.contains("poster")) return { kick: 1, bass: 1, pad: 0.8, high: 0.9 };
  if (!ui || !ui.code || !ui.code.length) return { kick: 0.25, bass: 0.3, pad: 0.4, high: 0.2 };   // idle: gentle
  return e;
}

// The 3D wave field: rows of waveforms on a plane, flying towards the
// viewer in time with the music, seen by a camera that slowly sways.
const ROWS = 34, COLS = 84, NEAR = 1.2, FAR = 16, SPAN = 11;

function waveHeight(x, z, t, beat, kick) {
  const e = energy;
  const d = Math.hypot(x, (z - 6) * 0.9);
  const shock = e.kick * kick * 1.1 * Math.exp(-Math.pow(d - (beat % 1) * 9, 2) * 0.6);   // a ring per beat
  const bass = e.bass * 0.55 * Math.sin(x * 0.55 + t * 1.3) * (0.6 + 0.4 * kick);
  const pad = e.pad * 0.35 * Math.sin(x * 1.1 + z * 0.45 + t * 0.5) * Math.cos(z * 0.3 - t * 0.35);
  const high = e.high * 0.14 * Math.sin(x * 2.3 - z * 1.3 + t * 5.5) * (0.5 + noise(x * 0.8 + z + t * 0.7));
  const calm = 0.12 * Math.sin(x * 0.8 + z * 0.6 + t * 0.4);                        // never quite still
  return (shock + bass + pad + high + calm) * (0.45 + 0.75 * intensity);
}

function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`;
}

// A smooth line through the points (quadratic curves through the midpoints).
function smoothPath(g, xs, ys, n) {
  g.moveTo(xs[0], ys[0]);
  for (let c = 1; c < n; c++) g.quadraticCurveTo(xs[c], ys[c], (xs[c] + xs[c + 1]) / 2, (ys[c] + ys[c + 1]) / 2);
  g.lineTo(xs[n], ys[n]);
}

function drawWaves(g, W, H, st, beat, bar, kick, t) {
  const target = targetEnergy();
  for (const k in energy) energy[k] += (target[k] - energy[k]) * 0.03;
  const f = H * 0.95;                                         // focal length
  const camY = 2.7 + 0.25 * Math.sin(t * 0.13), horizon = H * 0.2;
  const yaw = 0.22 * Math.sin(t * 0.07), cy = Math.cos(yaw), sy = Math.sin(yaw);
  const speed = 0.5;                                          // rows per beat
  const shift = (beat * speed) % 1, dz = (FAR - NEAR) / ROWS;
  const lw = Math.max(1, W / 1920);

  // A soft glow on the horizon that breathes with the kick.
  const glow = g.createRadialGradient(W / 2, horizon, 0, W / 2, horizon, W * 0.6);
  glow.addColorStop(0, hexA(st.accent2, 0.28 + 0.3 * kick * energy.kick)); glow.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = glow; g.fillRect(0, 0, W, H);

  const occlude = hexA(st.base[1], 0.9);
  g.lineJoin = "round";
  for (let r = ROWS - 1; r >= 0; r--) {                       // far to near: nearer rows hide farther ones
    const zw = NEAR + (r + 1 - shift) * dz;                   // world depth of this row, moving closer
    const xs = new Float32Array(COLS + 1), ys = new Float32Array(COLS + 1);
    let visible = true, low = 0;
    for (let c = 0; c <= COLS; c++) {
      const xw = (c / COLS - 0.5) * SPAN * (zw / 6 + 0.6);
      const y = waveHeight(xw, zw + beat * speed * dz, t, beat, kick) * 1.6;
      const xr = xw * cy - (zw - 6) * sy, zr = (zw - 6) * cy + xw * sy + 6;   // sway around the field's centre
      if (zr < 0.3) { visible = false; break; }
      xs[c] = W / 2 + (xr * f) / zr; ys[c] = horizon + ((camY - y) * f) / zr;
      if (ys[c] > low) low = ys[c];
    }
    if (!visible) continue;
    const near = 1 - (zw - NEAR) / (FAR - NEAR);              // 0 far .. 1 near
    g.beginPath();
    smoothPath(g, xs, ys, COLS);
    // Occlude a band under the wave (enough to hide the rows behind it).
    const band = low + (H * 0.07) * (0.3 + near);
    g.lineTo(xs[COLS], band); g.lineTo(xs[0], band); g.closePath();
    g.fillStyle = occlude; g.fill();
    g.beginPath();
    smoothPath(g, xs, ys, COLS);
    const width = lw * (0.6 + 2.4 * near * near) * (1 + 0.6 * kick * energy.kick);
    if (near > 0.6) {                                         // a cheap glow on the nearest rows
      g.strokeStyle = hexA(st.accent, 0.12 * near); g.lineWidth = width * 4; g.stroke();
    }
    g.strokeStyle = hexA(r % 3 === 0 ? st.accent2 : st.accent, 0.3 + 0.65 * near);
    g.lineWidth = width; g.stroke();
  }
}

function draw() {
  const c = $("bg"), g = c.getContext("2d"), W = c.width, H = c.height;
  const style = (ui && ui.track && ui.track.style) || params.get("style") || "trance";
  const st = STYLES[style] || STYLES.techno;
  intensity += (targetIntensity - intensity) * 0.02;
  const { beat, bar } = beatPhase();
  const kick = Math.pow(1 - (beat % 1), 3);                   // decays each beat
  if (document.body.classList.contains("poster")) intensity = targetIntensity = 1;
  const t = performance.now() / 1000;

  const grad = g.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, st.base[0]); grad.addColorStop(1, st.base[1]);
  g.fillStyle = grad; g.fillRect(0, 0, W, H);
  drawWaves(g, W, H, st, beat, bar, kick, t);

  // Vignette so the panels stay readable.
  const v = g.createRadialGradient(W / 2, H / 2, H * 0.3, W / 2, H / 2, W * 0.75);
  v.addColorStop(0, "rgba(0,0,0,0)"); v.addColorStop(1, "rgba(0,0,0,0.55)");
  g.fillStyle = v; g.fillRect(0, 0, W, H);

  animateTyping();
  requestAnimationFrame(draw);
}

// ?poster=1: a thumbnail for YouTube (stream/thumbnail.py renders it).
// ?style=trance forces a style (for previews without a running engine).
const params = new URLSearchParams(location.search);
if (params.get("poster")) document.body.classList.add("poster");
// ?banner=1: YouTube channel art, 2048x1152 (stream/thumbnail.py --banner).
if (params.get("banner")) document.body.classList.add("poster", "banner");

function renderPoster() {
  const t = (ui && ui.track) || {};
  $("p-track").innerHTML = t.title ? `now playing <b>${esc(t.title)}</b> · ${esc(t.style)} · ${Math.round(t.bpm || 0)} bpm` : "";
}

fit();
poll();
requestAnimationFrame(draw);
