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

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const short = (model) => (model ? model.split("/").pop() : "");
const fmtTime = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const fmtNum = (n) => Number(n || 0).toLocaleString("en-US");

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
  $("back").addEventListener("click", () => { history.pushState({}, "", "/"); showStart(); });
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => selectTab(tab.dataset.tab)));
  $("player").addEventListener("timeupdate", highlightCurrentStep);
  window.addEventListener("popstate", route);
  route();
}

function route() {
  const runId = new URLSearchParams(location.search).get("run");
  if (runId) openRun(runId); else showStart();
}

function renderHealth() {
  const chip = (ok, label) => `<span class="chip ${ok ? "chip-ok" : "chip-off"}"><span class="h-1.5 w-1.5 rounded-full ${ok ? "bg-emerald-400" : "bg-slate-600"}"></span>${label}</span>`;
  $("health").innerHTML = chip(health.nebius_configured, "Nebius Token Factory") + chip(health.tavily_configured, "Tavily") + chip(health.github_configured, "GitHub");
  if (health.demo_repo) $("demo-repo").textContent = health.demo_repo;
  if (!health.github_configured) { $("open-pr").disabled = true; }
}

function renderSamples(samples) {
  $("samples").innerHTML = samples.map((s) => `
    <div class="card flex flex-col overflow-hidden">
      <video src="${esc(s.video_url)}#t=4" class="aspect-video w-full bg-black object-cover" muted preload="metadata"></video>
      <div class="flex flex-1 flex-col p-4">
        <p class="font-medium text-white">${esc(s.title)}</p>
        <p class="mt-1 flex-1 text-sm text-slate-400">${esc(s.description)}</p>
        <div class="mt-3 flex flex-wrap gap-2">
          <button class="btn btn-primary" data-sample="${esc(s.name)}" data-mode="live" ${health.nebius_configured ? "" : "disabled title='NEBIUS_API_KEY is not configured'"}>Run live</button>
          ${s.has_recording ? `<button class="btn-ghost" data-sample="${esc(s.name)}" data-mode="replay" title="Replay a recorded run (instant, no model calls)">Replay</button>` : ""}
        </div>
      </div>
    </div>`).join("") || `<p class="text-sm text-slate-500">No sample videos found in samples/.</p>`;
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
  const resp = await fetch("/api/runs", { method: "POST", body: form });
  if (!resp.ok) { toast((await resp.json().catch(() => ({}))).detail || "Could not start the run."); return; }
  const run = await resp.json();
  history.pushState({}, "", `/?run=${run.id}`);
  openRun(run.id);
}

function showStart() {
  if (source) source.close();
  $("player").pause();
  $("start").classList.remove("hidden");
  $("run").classList.add("hidden");
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
  const run = await fetch(`/api/runs/${runId}`).then((r) => (r.ok ? r.json() : null));
  if (!run) { toast("That run no longer exists (the server was restarted)."); history.replaceState({}, "", "/"); showStart(); return; }

  state = { run, events: [], started: null, report: null, test: null, diff: null, agent: [], research: [] };
  $("start").classList.add("hidden");
  $("run").classList.remove("hidden");
  $("run-title").textContent = "Analysing recording…";
  $("run-mode").textContent = run.mode === "replay" ? "Replay of a recorded run" : "Live run";
  $("run-mode").className = `chip ${run.mode === "replay" ? "chip-amber" : "chip-ok"}`;
  $("video-name").textContent = run.video_name;
  $("player").src = `/api/runs/${runId}/video`;
  ["report", "usage", "artifacts"].forEach((id) => $(id).classList.add("hidden"));
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
}

function onEvent(event) {
  if (state.events.some((e) => e.seq === event.seq)) return;  // replayed after a reconnect
  state.events.push(event);
  state.started ??= event.ts;
  state.last = event.ts;
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

function tickClock() {
  if (!state || state.started == null) return;
  const end = state.finished ? state.last : Date.now() / 1000;
  $("elapsed").textContent = `${(end - state.started).toFixed(0)}s`;
}

// ---------------------------------------------------------------- report and steps

function renderReport(report) {
  state.report = report;
  $("run-title").textContent = report.title;
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

function renderTimeline() {
  $("timeline").innerHTML = STAGES.map((stage, i) => {
    const s = stageState(stage.id);
    const red = stage.id === "reproduce" && s.status === "done";
    const cls = red ? "stage-red" : `stage-${s.status}`;
    const icon = { pending: `<span class="text-xs">${i + 1}</span>`, running: `<span class="spinner"></span>`, failed: "!", skipped: "–",
      done: red ? "✕" : `<svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>` }[s.status];
    const models = s.models.map((m) => `<span class="chip ${m === health.models?.escalate ? "chip-amber" : ""}">${esc(short(m))}</span>`).join("");
    const meta = s.done && s.done.tokens ? `<span class="font-mono text-xs text-slate-500">${fmtNum(s.done.tokens)} tok · ${s.done.latency_s.toFixed(0)}s</span>` : "";
    const message = s.last ? s.last.message : stage.hint;
    return `
      <li class="relative flex gap-3 ${cls} fade-in">
        ${i < STAGES.length - 1 ? `<span class="stage-line"></span>` : ""}
        <span class="stage-dot">${icon}</span>
        <div class="min-w-0 flex-1 pb-1">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-medium text-white">${stage.name}</span>
            ${red ? `<span class="chip chip-red">red test</span>` : ""}
            ${stage.id === "verify" && s.status === "done" ? `<span class="chip chip-ok">green test</span>` : ""}
            ${s.escalated ? `<span class="chip chip-amber">escalated</span>` : ""}
            ${models}
            <span class="ml-auto">${meta}</span>
          </div>
          <p class="mt-0.5 break-words text-sm ${s.status === "pending" ? "text-slate-500" : "text-slate-300"}">${esc(message)}</p>
          ${stageDetails(stage.id, s)}
        </div>
      </li>`;
  }).join("");
}

function stageDetails(id, s) {
  const p = (s.done && s.done.payload) || {};
  if (id === "triage" && p.files) {
    return `<ul class="mt-2 space-y-1 text-xs text-slate-400">${p.files.map((f) => `<li><code class="text-sky-300">${esc(f.path)}</code> · ${esc(f.reason)}</li>`).join("")}</ul>`;
  }
  if (id === "reproduce" && p.attempts) {
    const color = { red: "chip-red", green: "chip-amber", broken: "chip-off" };
    return `<div class="mt-2 flex flex-wrap gap-1.5">${p.attempts.map((a) => `<span class="chip ${color[a.status]}" title="${esc(a.diagnosis)}">#${a.attempt} ${esc(short(a.model))}: ${a.status}</span>`).join("")}</div>`;
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
    return `<pre class="mt-2 max-h-40 overflow-auto rounded-lg border border-slate-800 bg-slate-900/60 p-2.5 text-[11px] leading-relaxed text-slate-400">${esc(p.summary)}</pre>`;
  }
  if (id === "ship" && p.url) {
    return `<a href="${esc(p.url)}" target="_blank" rel="noopener" class="btn btn-primary mt-2">Open pull request #${p.number}
      <svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 17L17 7M9 7h8v8"/></svg></a>`;
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
      <li class="flex gap-2"><span class="w-6 flex-none text-right font-mono text-xs text-slate-600">${p.n}</span>
        <span class="min-w-0 flex-1"><code class="${p.ok ? "text-sky-300" : "text-rose-300"}">${esc(p.tool)}</code><span class="text-slate-500">(${esc(args)})</span>
        <span class="ml-1 chip ${p.model === health.models?.escalate ? "chip-amber" : ""}">${esc(short(p.model))}</span>
        <span class="mt-0.5 block truncate text-xs text-slate-500">${esc((p.result || "").split("\n")[0])}</span></span></li>`);
  }
  $("artifacts").classList.remove("hidden");
}

function selectTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll("[data-panel]").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== name));
}

// ---------------------------------------------------------------- usage

function renderUsage() {
  const rows = [];
  let total = 0;
  for (const stage of STAGES) {
    const s = stageState(stage.id);
    if (!s.done) continue;
    const models = stage.id === "research" ? ["Tavily search API"] : s.models.map(short);
    total += stage.id === "research" ? 0 : s.done.tokens;
    rows.push(`<tr><td class="py-1.5 pr-3 text-slate-300">${stage.name}</td><td class="py-1.5 pr-3 text-xs text-slate-400">${esc(models.join(" → ") || "–")}</td>
      <td class="py-1.5 pr-3 text-right font-mono text-xs">${stage.id === "research" ? `${state.research.length} search(es)` : fmtNum(s.done.tokens)}</td>
      <td class="py-1.5 text-right font-mono text-xs text-slate-400">${s.done.latency_s.toFixed(0)}s</td></tr>`);
  }
  if (!rows.length) return;
  $("usage").classList.remove("hidden");
  $("usage-rows").innerHTML = rows.join("");
  $("usage-total").textContent = `${fmtNum(total)} tokens`;
  const end = state.events.find((e) => e.stage === "pipeline" && e.status === "done");
  const cost = end && end.payload && end.payload.cost_usd;
  $("usage-cost").textContent = cost != null ? `Estimated cost: $${cost.toFixed(4)}` : "Estimated cost: n/a (Token Factory prices not configured in app/usage.py)";
}

init();
