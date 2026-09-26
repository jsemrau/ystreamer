// sleepless composer: stream UI.
// Polls ui.json (written by the engine, served by stream/ui_server.py) and
// draws the live code, the agents' decisions and a beat-synced visual.
"use strict";

const STAGE_W = 1920, STAGE_H = 1080;
const POLL_MS = 500;
const PHASE_NAMES = { build: "build", groove: "groove", breakdown: "breakdown", rebuild: "rebuild",
                      groove2: "peak", outro: "outro" };

// Colours and scene per style.
const STYLES = {
  lofi:      { accent: "#ffb870", accent2: "#ff7eb6", scene: "rain",   base: ["#1a1020", "#2a1a24"] },
  downtempo: { accent: "#f5a97f", accent2: "#c6a0f6", scene: "rain",   base: ["#12101c", "#241a2a"] },
  ambient:   { accent: "#8bd5ca", accent2: "#b7bdf8", scene: "aurora", base: ["#050a14", "#0b1624"] },
  piano:     { accent: "#e5c890", accent2: "#a6d189", scene: "aurora", base: ["#0b0a10", "#161420"] },
  deephouse: { accent: "#7dc4e4", accent2: "#f5bde6", scene: "grid",   base: ["#070914", "#101830"] },
  techno:    { accent: "#8aadf4", accent2: "#ed8796", scene: "grid",   base: ["#050507", "#0e0e16"] },
  hardcore:  { accent: "#ff5c5c", accent2: "#ffcc4d", scene: "grid",   base: ["#0a0303", "#1a0606"] },
  trance:    { accent: "#7cf2ff", accent2: "#c792ff", scene: "tunnel", base: ["#04040f", "#0c0a26"] },
  darkwave:  { accent: "#cfd8ff", accent2: "#8f9bd6", scene: "ridges", base: ["#030305", "#0a0b10"] },
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
function fit() {
  if (POSTER) {                     // the thumbnail is always exactly 1280x720
    const c = $("bg");
    c.style.width = "1280px"; c.style.height = "720px"; c.width = 1280; c.height = 720;
    return;
  }
  const s = Math.min(innerWidth / STAGE_W, innerHeight / STAGE_H);
  const stage = $("stage");
  stage.style.transform = `scale(${s})`;
  stage.style.left = `${(innerWidth - STAGE_W * s) / 2}px`;
  stage.style.top = `${(innerHeight - STAGE_H * s) / 2}px`;
  const c = $("bg");
  c.width = Math.round(innerWidth * Math.min(devicePixelRatio || 1, 1.5));
  c.height = Math.round(innerHeight * Math.min(devicePixelRatio || 1, 1.5));
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
  if (s.judged) chips.push(`<span class="chip">judge prefers AI <b>${Math.round(100 * s.judge_llm / s.judged)}%</b></span>`);
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

// Deterministic noise for the scenes.
function hash(n) { const x = Math.sin(n * 127.1) * 43758.5453; return x - Math.floor(x); }
function noise(x) { const i = Math.floor(x), f = x - i, u = f * f * (3 - 2 * f); return hash(i) * (1 - u) + hash(i + 1) * u; }

const drops = Array.from({ length: 140 }, (_, i) => ({ x: hash(i), y: hash(i + 99), v: 0.4 + hash(i + 7) * 0.8, l: 0.02 + hash(i + 3) * 0.05 }));
const bokeh = Array.from({ length: 26 }, (_, i) => ({ x: hash(i + 11), y: 0.35 + hash(i + 31) * 0.6, r: 0.02 + hash(i + 51) * 0.06, h: hash(i + 71) }));
const stars = Array.from({ length: 160 }, (_, i) => ({ x: hash(i + 201), y: hash(i + 401) * 0.7, s: hash(i + 601) }));

function draw() {
  const c = $("bg"), g = c.getContext("2d"), W = c.width, H = c.height;
  const style = (ui && ui.track && ui.track.style) || "techno";
  const st = STYLES[style] || STYLES.techno;
  intensity += (targetIntensity - intensity) * 0.02;
  const { beat, bar } = beatPhase();
  const kick = Math.pow(1 - (beat % 1), 3) * intensity;         // decays each beat
  if (document.body.classList.contains("poster")) intensity = targetIntensity = 1;
  const t = performance.now() / 1000;

  const grad = g.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, st.base[0]); grad.addColorStop(1, st.base[1]);
  g.fillStyle = grad; g.fillRect(0, 0, W, H);

  if (st.scene === "ridges") {                       // darkwave: Unknown Pleasures ridgelines
    const rows = 42, left = W * 0.2, right = W * 0.8, top = H * 0.3, step = (H * 0.66) / rows;
    g.lineWidth = Math.max(1, W / 1300);
    for (let r = 0; r < rows; r++) {
      const y0 = top + r * step, pts = [];
      for (let x = left; x <= right; x += W / 320) {
        const u = (x - left) / (right - left);
        const env = Math.pow(Math.sin(Math.PI * u), 8);
        const n = noise(u * 9 + r * 3.7 + bar * 0.15) * noise(u * 23 + r + t * 0.1);
        pts.push([x, y0 - env * n * step * (3 + 5 * intensity + 7 * kick)]);
      }
      g.beginPath();                                 // hide the rows behind with the background colour
      pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
      g.lineTo(right, y0 + step * 2); g.lineTo(left, y0 + step * 2); g.closePath();
      g.fillStyle = st.base[0]; g.fill();
      g.beginPath();                                 // then stroke only the ridge itself
      pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
      g.strokeStyle = `rgba(214,222,255,${0.25 + 0.5 * (r / rows)})`; g.stroke();
    }
  } else if (st.scene === "grid") {                  // techno / house: perspective grid
    const horizon = H * 0.46, speed = style === "hardcore" ? 2.2 : style === "deephouse" ? 0.8 : 1.3;
    const glow = g.createRadialGradient(W / 2, horizon, 0, W / 2, horizon, W * 0.55);
    glow.addColorStop(0, st.accent2 + "88"); glow.addColorStop(1, "transparent");
    g.globalAlpha = 0.35 + 0.65 * kick; g.fillStyle = glow; g.fillRect(0, 0, W, H); g.globalAlpha = 1;
    g.strokeStyle = st.accent; g.lineWidth = Math.max(1, W / 1400);
    for (let i = 0; i < 24; i++) {
      const z = ((i + (beat * speed) % 1) / 24);
      const y = horizon + Math.pow(z, 2.2) * (H - horizon);
      g.globalAlpha = 0.12 + 0.6 * z; g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke();
    }
    for (let i = -20; i <= 20; i++) {
      g.globalAlpha = 0.25; g.beginPath(); g.moveTo(W / 2 + i * 8, horizon); g.lineTo(W / 2 + i * W / 14, H); g.stroke();
    }
    g.globalAlpha = 1;
  } else if (st.scene === "tunnel") {                // trance: rings flying outwards
    const cx = W / 2, cy = H * 0.5;
    for (let i = 0; i < 26; i++) {
      const z = (i + (beat * 0.5) % 1) / 26;           // rings move outwards, one step per two beats
      const r = Math.pow(z, 2.4) * W * 0.9;
      g.strokeStyle = i % 2 ? st.accent : st.accent2;
      g.globalAlpha = (0.08 + 0.5 * z) * (0.6 + 0.4 * intensity);
      g.lineWidth = 1 + z * 6 + kick * 4;
      g.beginPath(); g.ellipse(cx, cy, r, r * 0.62, 0, 0, Math.PI * 2); g.stroke();
    }
    g.globalAlpha = 1;
  } else if (st.scene === "rain") {                  // lofi / downtempo: rain on a window, warm bokeh
    for (const b of bokeh) {
      const x = b.x * W, y = b.y * H, r = b.r * W * (1 + 0.15 * kick);
      const gr = g.createRadialGradient(x, y, 0, x, y, r);
      gr.addColorStop(0, (b.h > 0.5 ? st.accent : st.accent2) + "55"); gr.addColorStop(1, "transparent");
      g.fillStyle = gr; g.fillRect(x - r, y - r, 2 * r, 2 * r);
    }
    g.strokeStyle = "rgba(220,225,255,0.18)"; g.lineWidth = Math.max(1, W / 1600);
    for (const d of drops) {
      const y = ((d.y + t * d.v * 0.25) % 1.1) * H, x = d.x * W;
      g.beginPath(); g.moveTo(x, y); g.lineTo(x - W * 0.003, y + d.l * H); g.stroke();
    }
  } else {                                           // ambient / piano: aurora and stars
    for (const s of stars) {
      g.fillStyle = `rgba(255,255,255,${0.2 + 0.5 * s.s * (0.6 + 0.4 * Math.sin(t + s.s * 9))})`;
      g.fillRect(s.x * W, s.y * H, 1.5, 1.5);
    }
    for (let k = 0; k < 4; k++) {
      g.beginPath();
      for (let x = 0; x <= W; x += W / 120) {
        const u = x / W;
        const y = H * (0.3 + 0.08 * k) + Math.sin(u * 5 + t * 0.15 + k) * H * 0.06 + noise(u * 4 + t * 0.05 + k * 10) * H * 0.08;
        x === 0 ? g.moveTo(x, y) : g.lineTo(x, y);
      }
      g.lineTo(W, H); g.lineTo(0, H); g.closePath();
      const a = g.createLinearGradient(0, H * 0.2, 0, H);
      a.addColorStop(0, (k % 2 ? st.accent : st.accent2) + "30"); a.addColorStop(0.5, "transparent");
      g.fillStyle = a; g.fill();
    }
  }

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

function renderPoster() {
  const t = (ui && ui.track) || {};
  $("p-track").innerHTML = t.title ? `now playing <b>${esc(t.title)}</b> · ${esc(t.style)} · ${Math.round(t.bpm || 0)} bpm` : "";
}

fit();
poll();
requestAnimationFrame(draw);
