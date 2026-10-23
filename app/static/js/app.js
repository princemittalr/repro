// Repro UI: start a run, stream its pipeline events over SSE, render the live timeline.
"use strict";

const STAGES = [
  { id: "perceive", name: "Perceive", hint: "Watch the recording and extract repro steps" },
  { id: "triage", name: "Triage", hint: "Rank the files most likely to hold the bug" },
  { id: "reproduce", name: "Reproduce", hint: "Write a Playwright test that fails on the bug" },
  { id: "research", name: "Research", hint: "Live web research when the agent needs it" },
  { id: "fix", name: "Fix", hint: "Patch the code until the test passes" },
  { id: "verify", name: "Verify", hint: "Regression test green, existing suite green" },
  { id: "ship", name: "Pull request", hint: "Open a PR with the fix, test and evidence" },
];
const STAGE_EVENTS = [...STAGES.map((s) => s.id), "pipeline"];

// Friendly model names; the full id stays in the tooltip.
const MODEL_NAMES = {
  "nemotron-3.5-super-vl": "Nemotron 3.5 Super VL",
  "nemotron-3_5-lightning": "Nemotron 3.5 Lightning",
  "nemotron-3.5-lightning": "Nemotron 3.5 Lightning",
  "nemotron-3-super-120b-a12b": "Nemotron 3 Super",
  "nemotron-3-ultra-550b-a55b": "Nemotron 3 Ultra",
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const short = (model) => (model ? model.split("/").pop() : "");
const friendly = (model) => MODEL_NAMES[short(model).toLowerCase()] || short(model);
const modelChip = (model, extra = "") => `<span class="chip ${extra}" title="${esc(model)}">${esc(friendly(model))}</span>`;
const fmtTime = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const fmtClock = (s) => { s = Math.max(0, Math.round(s)); return s < 60 ? `${s}s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const fmtNum = (n) => Number(n || 0).toLocaleString("en-US");
const ICONS = {
  check: `<svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>`,
  // A red test is the goal of REPRODUCE: shown as "bug caught", never as an error cross.
  bug: `<svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 8a4 4 0 118 0v1H8V8z"/><rect x="7" y="9" width="10" height="10" rx="5"/><path d="M12 13v6M3 13h4M17 13h4M4 7l3 2M20 7l-3 2M4 19l3-2M20 19l-3-2"/></svg>`,
  caret: `<svg viewBox="0 0 24 24" class="caret h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>`,
};

let health = {};
let state = null;   // the run being shown
let source = null;  // its EventSource

// ---------------------------------------------------------------- start screen

async function init() {
  health = await fetch("/api/health").then((r) => r.json()).catch(() => ({}));
  renderHealth();
  const samples = await fetch("/api/samples").then((r) => r.json()).catch(() => []);
  renderSamples(samples);
  wireUpload();
  $("back").addEventListener("click", () => { history.pushState({}, "", "/app"); showStart(); });
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => selectTab(tab.dataset.tab)));
  $("player").addEventListener("timeupdate", highlightCurrentStep);
  wireTimeline();
  window.addEventListener("popstate", route);
  route();
}

function route() {
  const params = new URLSearchParams(location.search);
  const runId = params.get("run");
  const sample = params.get("sample");
  if (runId) openRun(runId);
  else if (sample) {
    // Deep link from the landing page, e.g. /app?sample=coupon&mode=replay: start it straight away.
    history.replaceState({}, "", "/app");
    startRun({ sample, mode: params.get("mode") === "live" ? "live" : "replay" });
  } else showStart();
}

function renderHealth() {
  const b = health.budget || {};
  const integration = (ok, label) => `<span class="chip hidden lg:inline-flex ${ok ? "chip-ok" : "chip-off"}" title="${label} ${ok ? "configured" : "not configured"}"><span class="h-1.5 w-1.5 rounded-full ${ok ? "bg-emerald-400" : "bg-slate-600"}"></span>${label}</span>`;
  const live = health.nebius_configured && b.live_enabled !== false && b.live_runs_left_today !== 0;
  const status = live
    ? `<span class="chip chip-ok" title="Live runs left today on this demo"><span class="pulse-dot"></span>${b.live_runs_left_today ?? ""} live runs left</span>`
    : `<span class="chip chip-amber" title="Replays of real recorded runs are always available">Replay mode</span>`;
  $("health").innerHTML = integration(health.nebius_configured, "Nebius") + integration(health.tavily_configured, "Tavily")
    + integration(health.github_configured, "GitHub") + status;
  if (health.demo_repo) $("demo-repo").textContent = health.demo_repo;
  if (!health.github_configured) $("open-pr").disabled = true;
}

// Live runs spend real Nebius credit, so the demo has a daily budget (see app/budget.py).
function liveDisabled() {
  if (!health.nebius_configured) return "disabled title='NEBIUS_API_KEY is not configured'";
  const b = health.budget || {};
  if (b.live_enabled === false || b.live_runs_left_today === 0) return "disabled title='Today\'s live-run budget is used up. Replay shows a real recorded run.'";
  return "";
}

function renderBudget() {
  const b = health.budget;
  if (!b) return;
  $("budget-note").textContent = !b.live_enabled
    ? "Live runs are switched off on this demo. Replay shows a real recorded run, step by step."
    : `Live runs call Nemotron on Nebius Token Factory: ${b.live_runs_left_today} left today (up to ${b.runs_per_visitor} per visitor). Replay is instant and free.`;
  if (liveDisabled()) $("upload-note").textContent = "Uploads need a live run, which isn't available right now. Try a sample replay instead.";
}

function renderSamples(samples) {
  renderBudget();
  $("samples").innerHTML = samples.map((s) => `
    <div class="card lift flex flex-col overflow-hidden" data-card>
      <div class="sample-media">
        <img src="${esc(s.poster_url)}" alt="" width="960" height="540" loading="lazy">
        <video data-src="${esc(s.video_url)}" muted loop playsinline preload="none" aria-hidden="true"></video>
        <span class="play-hint chip border-slate-700 bg-slate-950/80 text-slate-200">${s.has_recording ? `<span class="hover-only">Hover to preview</span><span class="touch-only">Replay available</span>` : "Live only"}</span>
      </div>
      <div class="flex flex-1 flex-col p-4">
        <p class="font-medium text-white">${esc(s.title)}</p>
        <p class="mt-1 flex-1 text-sm text-slate-400">${esc(s.description)}</p>
        ${s.has_recording ? "" : `<p class="mt-2 text-xs text-slate-400">No recorded replay yet: this bug has more than one reasonable fix, so it is shown live only.</p>`}
        <div class="mt-3 flex flex-wrap gap-2">
          ${s.has_recording
            ? `<button class="btn btn-primary" data-sample="${esc(s.name)}" data-mode="replay" title="Replay a recorded real run (instant, no model calls)">Replay run</button>
               <button class="btn-ghost" data-sample="${esc(s.name)}" data-mode="live" ${liveDisabled()}>Run live</button>`
            : `<button class="btn btn-primary" data-sample="${esc(s.name)}" data-mode="live" ${liveDisabled()}>Run live</button>`}
        </div>
      </div>
    </div>`).join("") || `<p class="text-sm text-slate-400">No sample videos found in samples/.</p>`;
  $("samples").querySelectorAll("[data-card]").forEach(FX.hoverPlay);
  $("samples").querySelectorAll("button[data-sample]").forEach((b) =>
    b.addEventListener("click", () => startRun({ sample: b.dataset.sample, mode: b.dataset.mode })));
}

function wireUpload() {
  const zone = $("dropzone");
  $("file").addEventListener("change", (e) => e.target.files[0] && startRun({ file: e.target.files[0] }));
  ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("border-emerald-500"); }));
  ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.remove("border-emerald-500"); }));
  zone.addEventListener("drop", (e) => e.dataTransfer.files[0] && startRun({ file: e.dataTransfer.files[0] }));
}

async function startRun({ sample, file, mode = "live" }) {
  const form = new FormData();
  if (sample) form.append("sample", sample);
  if (file) form.append("video", file);
  form.append("mode", mode);
  form.append("open_pr", $("open-pr").checked ? "true" : "false");
  const resp = await fetch("/api/runs", { method: "POST", body: form }).catch(() => null);
  if (!resp || !resp.ok) { toast((resp && (await resp.json().catch(() => ({}))).detail) || "Could not start the run."); showStart(); return; }
  const run = await resp.json();
  history.pushState({}, "", `/app?run=${run.id}`);
  openRun(run.id);
}

function showStart() {
  if (source) source.close();
  clearInterval(openRun.clock);
  $("player").pause();
  $("start").classList.remove("hidden");
  $("run").classList.add("hidden");
  document.title = "Try Repro: turn a bug recording into a pull request";
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add("hidden"), 6000);
}

// ---------------------------------------------------------------- run screen

async function openRun(runId) {
  if (source) source.close();
  const run = await fetch(`/api/runs/${runId}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!run) { toast("That run no longer exists (the server was restarted)."); history.replaceState({}, "", "/app"); showStart(); return; }

  state = { run, events: [], first: null, last: null, lastSeen: null, report: null, research: [],
            seen: {}, open: new Set(), expanded: new Set(), celebrated: false };
  $("start").classList.add("hidden");
  $("run").classList.remove("hidden");
  window.scrollTo({ top: 0 });
  $("run-title").textContent = "Analysing recording…";
  $("run-mode").textContent = run.mode === "replay" ? "Replay of a recorded run" : "Live run";
  $("run-mode").className = `chip ${run.mode === "replay" ? "chip-amber" : "chip-ok"}`;
  $("video-name").textContent = run.video_name;
  const sample = (run.video_name.match(/^shoplite-(\w+)\.mp4$/) || [])[1];
  $("player").poster = sample ? `/static/img/poster-${sample}.jpg` : "";
  $("player").src = `/api/runs/${runId}/video`;
  ["report", "usage", "artifacts", "success"].forEach((id) => $(id).classList.add("hidden"));
  $("test-code").textContent = "The test appears here once REPRODUCE finishes.";
  $("diff-code").textContent = "The diff appears here once FIX finishes.";
  $("agent-log").innerHTML = "";
  selectTab("test");
  renderTimeline();
  setRunState("running");

  source = new EventSource(`/api/runs/${runId}/events`);
  STAGE_EVENTS.forEach((name) => source.addEventListener(name, (msg) => onEvent(JSON.parse(msg.data))));
  source.addEventListener("end", () => { source.close(); finishRun(); });
  source.onerror = () => { if (state.finished) source.close(); };
  clearInterval(openRun.clock);
  openRun.clock = setInterval(tickClock, 500);
  tickClock();
}

function onEvent(event) {
  if (state.events.some((e) => e.seq === event.seq)) return;  // replayed after a reconnect
  state.events.push(event);
  state.first ??= event.ts;
  state.last = event.ts;
  state.lastSeen = performance.now();
  const p = event.payload || {};

  if (event.stage === "perceive" && event.status === "done" && p.report) renderReport(p.report);
  if (event.stage === "reproduce" && p.test_code) setCode("test-code", p.test_code, "python");
  if (event.stage === "fix" && event.status === "progress") addAgentStep(event);
  if (event.stage === "fix" && p.diff) setCode("diff-code", p.diff, "diff");
  if (event.stage === "verify" && p.diff) setCode("diff-code", p.diff, "diff");
  if (event.stage === "research" && event.status === "progress") state.research.push(p);
  if (event.stage === "pipeline" && ["done", "failed"].includes(event.status)) {
    state.finished = true;
    setRunState(event.status === "done" ? "done" : "failed", event.message);
    if (event.status === "done") celebrate();
  }
  renderTimeline();
  renderUsage();
}

function finishRun() {
  state.finished = true;
  clearInterval(openRun.clock);
  tickClock();
  const last = state.events.at(-1);
  if (!last || last.stage !== "pipeline") setRunState("failed", "The run stopped unexpectedly.");
}

function setRunState(kind, message) {
  const el = $("run-state");
  const styles = { running: ["chip", "Running"], done: ["chip chip-ok", "Fixed and verified"], failed: ["chip chip-red", "Stopped"] };
  const [cls, label] = styles[kind];
  el.className = cls;
  // Only a stopped run needs its reason spelled out; "done" speaks for itself.
  el.innerHTML = kind === "running" ? `<span class="spinner"></span>${label}` : esc(kind === "failed" && message ? `${label}: ${message}` : label);
}

// One clock: the server's event timestamps. Between events it advances with the browser's own
// monotonic clock, so a skewed client clock can never make it negative.
function elapsed() {
  if (state.first == null) return 0;
  const sinceLast = state.finished ? 0 : (performance.now() - state.lastSeen) / 1000;
  return Math.max(0, state.last - state.first + sinceLast);
}

function tickClock() {
  if (!state) return;
  const recorded = state.run.recorded_duration_s;
  $("elapsed").textContent = state.run.mode === "replay" && recorded
    ? `replay ${fmtClock(elapsed())} · original run ${fmtClock(recorded)}`
    : fmtClock(elapsed());
}

// The climax of a run: a success banner with the result, and a small burst.
function celebrate() {
  if (state.celebrated) return;
  state.celebrated = true;
  const pr = stageState("ship").done?.payload || {};
  const verify = stageState("verify").done?.payload || {};
  const tests = /tests (\d+)[\s\S]*?pass (\d+)/.exec(verify.summary || "");
  const took = state.run.mode === "replay" && state.run.recorded_duration_s ? state.run.recorded_duration_s : elapsed();
  const facts = [
    `in ${fmtClock(took)}${state.run.mode === "replay" ? " (original run)" : ""}`,
    tests ? `${tests[2]}/${tests[1]} unit tests green` : "existing suite green",
    `${fmtNum(state.events.at(-1)?.tokens)} tokens`,
  ];
  $("success").innerHTML = `
    <div class="success-banner flex flex-wrap items-center gap-4 rounded-2xl border border-emerald-500/30 bg-gradient-to-r from-emerald-500/15 via-emerald-500/5 to-transparent p-4 sm:p-5">
      <span id="success-icon" class="grid h-11 w-11 flex-none place-items-center rounded-full bg-emerald-500 text-slate-950">${ICONS.check}</span>
      <div class="min-w-0 flex-1">
        <p class="text-lg font-semibold tracking-tight text-white">Bug caught, fixed and verified</p>
        <p class="text-sm text-slate-300">${facts.map(esc).join(" · ")}</p>
      </div>
      ${pr.url ? `<a href="${esc(pr.url)}" target="_blank" rel="noopener" class="btn btn-primary">Open pull request #${esc(pr.number)}
        <svg viewBox="0 0 24 24" class="arrow h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M7 17L17 7M9 7h8v8"/></svg></a>` : ""}
    </div>`;
  $("success").classList.remove("hidden");
  requestAnimationFrame(() => FX.burstFrom($("success-icon")));
}

// ---------------------------------------------------------------- report and steps

function renderReport(report) {
  state.report = report;
  $("run-title").textContent = report.title;
  document.title = `${report.title} · Repro`;
  $("expected").textContent = report.expected_behavior;
  $("actual").textContent = report.actual_behavior;
  $("confidence").textContent = `confidence ${(report.confidence * 100).toFixed(0)}%`;
  $("steps").innerHTML = report.steps.map((s) => `
    <li><button class="step" data-t="${s.timestamp_s}">
      <span class="w-12 flex-none font-mono text-xs text-emerald-400">${fmtTime(s.timestamp_s)}</span>
      <span class="text-sm"><span class="mr-1.5 rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[11px] uppercase text-slate-400">${esc(s.action)}</span>${esc(s.target)}${s.value ? ` <code class="text-amber-300">"${esc(s.value)}"</code>` : ""}</span>
    </button></li>`).join("");
  $("steps").querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => seek(Number(b.dataset.t))));
  $("report").classList.remove("hidden");
}

function seek(t) {
  const player = $("player");
  player.currentTime = Math.max(0, t - 0.3);  // land just before the action
  player.play().catch(() => {});
}

function highlightCurrentStep() {
  const t = $("player").currentTime;
  const buttons = [...document.querySelectorAll("#steps .step")];
  let current = null;
  for (const b of buttons) if (Number(b.dataset.t) <= t + 0.35) current = b;
  buttons.forEach((b) => b.classList.toggle("current", b === current));
}

// ---------------------------------------------------------------- timeline

function stageState(id) {
  const events = state.events.filter((e) => e.stage === id);
  const last = events.at(-1);
  const models = [...new Set(events.map((e) => e.model).filter(Boolean))];
  const escalated = events.some((e) => e.payload && e.payload.escalated);
  let status = last ? last.status : "pending";
  if (status === "started" || status === "progress") status = "running";
  if (state.finished && status === "running") status = "failed";
  return { events, last, status, models, escalated, done: events.filter((e) => ["done", "failed", "skipped"].includes(e.status)).at(-1) };
}

// The timeline is re-rendered on every event, so open <details> and expanded messages are kept in state.
function wireTimeline() {
  const tl = $("timeline");
  tl.addEventListener("toggle", (e) => {
    const key = e.target.dataset && e.target.dataset.key;
    if (key) e.target.open ? state.open.add(key) : state.open.delete(key);
  }, true);
  tl.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-expand]");
    if (!btn) return;
    const id = btn.dataset.expand;
    state.expanded.has(id) ? state.expanded.delete(id) : state.expanded.add(id);
    renderTimeline();
  });
}

function renderTimeline() {
  $("timeline").innerHTML = STAGES.map((stage, i) => {
    const s = stageState(stage.id);
    const caught = stage.id === "reproduce" && s.status === "done";
    const visual = caught ? "caught" : s.status;
    const changed = state.seen[stage.id] !== undefined && state.seen[stage.id] !== visual;
    state.seen[stage.id] = visual;
    const icon = { pending: `<span class="text-xs">${i + 1}</span>`, running: `<span class="spinner"></span>`, failed: "!", skipped: "–",
      caught: ICONS.bug, done: ICONS.check }[visual];
    const models = s.models.map((m) => modelChip(m, m === health.models?.escalate ? "chip-amber" : "")).join("");
    const meta = s.done && s.done.tokens ? `<span class="font-mono text-xs text-slate-400">${fmtNum(s.done.tokens)} tok · ${s.done.latency_s.toFixed(0)}s</span>` : "";
    const message = s.last ? s.last.message : stage.hint;
    const long = message.length > 140;
    const expanded = state.expanded.has(stage.id);
    return `
      <li class="relative flex gap-3 stage-${visual} ${changed ? "just-changed" : ""}">
        ${i < STAGES.length - 1 ? `<span class="stage-line"></span>` : ""}
        <span class="stage-dot" title="${caught ? "Bug caught: the new test fails on the buggy code, as it should" : esc(s.status)}">${icon}</span>
        <div class="min-w-0 flex-1 pb-1">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-medium text-white">${stage.name}</span>
            ${caught ? `<span class="chip chip-red">bug caught · red test</span>` : ""}
            ${stage.id === "verify" && s.status === "done" ? `<span class="chip chip-ok">green test</span>` : ""}
            ${s.escalated ? `<span class="chip chip-amber">escalated</span>` : ""}
            ${models}
            <span class="ml-auto">${meta}</span>
          </div>
          <p class="mt-0.5 break-words text-sm ${s.status === "pending" ? "text-slate-500" : "text-slate-300"} ${long && !expanded ? "clamp-2" : ""}">${esc(message)}</p>
          ${long ? `<button class="mt-0.5 text-xs text-emerald-400 hover:underline" data-expand="${stage.id}" aria-expanded="${expanded}">${expanded ? "Show less" : "Show more"}</button>` : ""}
          ${stageDetails(stage.id, s)}
        </div>
      </li>`;
  }).join("");
}

// Collapsed by default: the headline result is in the stage message, the detail is one click away.
function details(key, label, body) {
  return `<details class="mt-2" data-key="${key}" ${state.open.has(key) ? "open" : ""}>
    <summary class="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200">${ICONS.caret}${label}</summary>
    ${body}</details>`;
}

function stageDetails(id, s) {
  const p = (s.done && s.done.payload) || {};
  if (id === "triage" && p.files) {
    return `<div class="mt-2 flex flex-wrap gap-1.5">${p.files.map((f) => `<code class="rounded bg-slate-800/80 px-1.5 py-0.5 text-xs text-sky-300">${esc(f.path)}</code>`).join("")}</div>`
      + details("triage", "Why these files", `<ul class="mt-2 space-y-1 text-xs text-slate-400">${p.files.map((f) => `<li><code class="text-sky-300">${esc(f.path)}</code> · ${esc(f.reason)}</li>`).join("")}</ul>`);
  }
  if (id === "reproduce" && p.attempts) {
    const color = { red: "chip-red", green: "chip-amber", broken: "chip-off" };
    return `<div class="mt-2 flex flex-wrap gap-1.5">${p.attempts.map((a) => `<span class="chip ${color[a.status]}" title="${esc(a.diagnosis)}">#${a.attempt} ${esc(friendly(a.model))}: ${a.status}</span>`).join("")}</div>`;
  }
  if (id === "research" && state.research.length) {
    return `<ul class="mt-2 space-y-2 text-xs">${state.research.map((r) => `
      <li class="rounded-lg border border-slate-800 bg-slate-900/60 p-2.5">
        <p class="font-medium text-slate-200">“${esc(r.query)}”</p>
        ${r.answer ? `<p class="mt-1 text-slate-400">${esc(r.answer.slice(0, 260))}${r.answer.length > 260 ? "…" : ""}</p>` : ""}
        <p class="mt-1 flex flex-wrap gap-x-3">${(r.sources || []).slice(0, 3).map((src) => `<a class="text-sky-400 hover:underline" href="${esc(src.url)}" target="_blank" rel="noopener">${esc(new URL(src.url).hostname)}</a>`).join("")}</p>
      </li>`).join("")}</ul>`;
  }
  if (id === "fix" && p.root_cause) {
    return `<div class="mt-2 rounded-lg border border-slate-800 bg-slate-900/60 p-2.5 text-xs text-slate-400"><p><span class="text-slate-200">Root cause:</span> ${esc(p.root_cause)}</p>
      <p class="mt-1">Changed: ${(p.changed_files || []).map((f) => `<code class="text-sky-300">${esc(f)}</code>`).join(", ")}</p></div>`;
  }
  if (id === "verify" && p.summary) {
    const tests = /tests (\d+)[\s\S]*?pass (\d+)/.exec(p.summary);
    return (tests ? `<p class="mt-1 text-xs text-emerald-300">${tests[2]}/${tests[1]} unit tests pass (existing suite plus any the agent added)</p>` : "")
      + details("verify", "Test output", `<pre class="mt-2 max-h-56 overflow-auto overscroll-auto rounded-lg border border-slate-800 bg-slate-900/60 p-2.5 text-[11px] leading-relaxed text-slate-400">${esc(p.summary)}</pre>`);
  }
  if (id === "ship" && p.url) {
    return `<a href="${esc(p.url)}" target="_blank" rel="noopener" class="btn btn-primary mt-2">Open pull request #${p.number}
      <svg viewBox="0 0 24 24" class="arrow h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M7 17L17 7M9 7h8v8"/></svg></a>`;
  }
  return "";
}

// ---------------------------------------------------------------- artifacts

function setCode(id, text, language) {
  const el = $(id);
  el.textContent = text;
  el.className = `language-${language}`;
  delete el.dataset.highlighted;
  if (window.hljs) hljs.highlightElement(el);
  $("artifacts").classList.remove("hidden");
}

function addAgentStep(event) {
  const p = event.payload || {};
  if (p.escalated) {
    $("agent-log").insertAdjacentHTML("beforeend", `<li class="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-amber-200">${esc(event.message)}</li>`);
  } else if (p.tool) {
    const args = Object.entries(p.args || {}).map(([k, v]) => `${k}=${String(v).slice(0, 80)}`).join(", ");
    $("agent-log").insertAdjacentHTML("beforeend", `
      <li class="flex gap-2"><span class="w-6 flex-none text-right font-mono text-xs text-slate-500">${p.n}</span>
        <span class="min-w-0 flex-1"><code class="${p.ok ? "text-sky-300" : "text-rose-300"}">${esc(p.tool)}</code><span class="text-slate-500">(${esc(args)})</span>
        <span class="ml-1">${modelChip(p.model, p.model === health.models?.escalate ? "chip-amber" : "")}</span>
        <span class="mt-0.5 block truncate text-xs text-slate-400">${esc((p.result || "").split("\n")[0])}</span></span></li>`);
  }
  $("artifacts").classList.remove("hidden");
}

function selectTab(name) {
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === name);
    t.setAttribute("aria-selected", t.dataset.tab === name);
  });
  document.querySelectorAll("[data-panel]").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== name));
}

// ---------------------------------------------------------------- usage

function renderUsage() {
  const rows = [];
  let total = 0;
  let time = 0;
  for (const stage of STAGES) {
    const s = stageState(stage.id);
    if (!s.done) continue;
    const models = stage.id === "research" ? ["Tavily search API"] : s.models.map(friendly);
    total += stage.id === "research" ? 0 : s.done.tokens;
    time += s.done.latency_s;
    rows.push(`<tr><td class="py-1.5 pr-3 text-slate-300">${stage.name}</td><td class="py-1.5 pr-3 text-xs text-slate-400" title="${esc(s.models.join(", "))}">${esc(models.join(" → ") || "–")}</td>
      <td class="py-1.5 pr-3 text-right font-mono text-xs">${stage.id === "research" ? `${state.research.length} search(es)` : fmtNum(s.done.tokens)}</td>
      <td class="py-1.5 text-right font-mono text-xs text-slate-400">${s.done.latency_s.toFixed(0)}s</td></tr>`);
  }
  if (!rows.length) return;
  rows.push(`<tr class="font-medium"><td class="pt-2 pr-3 text-slate-200">Total</td><td></td><td class="pt-2 pr-3 text-right font-mono text-xs text-slate-200">${fmtNum(total)}</td>
    <td class="pt-2 text-right font-mono text-xs text-slate-200">${time.toFixed(0)}s</td></tr>`);
  $("usage").classList.remove("hidden");
  $("usage-rows").innerHTML = rows.join("");
  $("usage-total").textContent = `${fmtNum(total)} tokens`;
  const end = state.events.find((e) => e.stage === "pipeline" && e.status === "done");
  const cost = end && end.payload && end.payload.cost_usd;
  const notes = [];
  if (state.run.mode === "replay") notes.push("Stage times are from the original live run; the replay shortens the waits.");
  const perceive = stageState("perceive").done;
  if (perceive && perceive.status === "done" && !perceive.tokens) notes.push("This run reused an earlier perception report, so Perceive shows no tokens.");
  if (cost != null) notes.push(`Estimated cost: $${cost.toFixed(4)}`);
  $("usage-note").textContent = notes.join(" ");
}

init();
