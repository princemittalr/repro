// Repro landing page: the motion layer. Content is all in the HTML; this file only enhances it.
// Rules: one motion per section, transform/opacity only, play once on scroll, loops pause offscreen
// and in hidden tabs, and reduced motion gets the final states with no loops.
"use strict";

(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const reduced = FX.reduced();
  const desktop = window.matchMedia("(hover: hover) and (pointer: fine) and (min-width: 768px)").matches;
  const fmtClock = (s) => { s = Math.round(s); return s < 60 ? `${s}s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

  // ---------------------------------------------------------------- visibility: loops run only on screen

  const onScreen = new WeakMap();
  const visObserver = new IntersectionObserver((entries) => entries.forEach((e) => onScreen.set(e.target, e.isIntersecting)), { threshold: 0.15 });
  const watch = (el) => { onScreen.set(el, false); visObserver.observe(el); return el; };
  const active = (el) => onScreen.get(el) && !document.hidden;

  // Play-once trigger: fn runs the first time el is `threshold` visible.
  function once(el, fn, threshold = 0.25) {
    if (!el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { io.disconnect(); fn(); }
    }, { threshold });
    io.observe(el);
  }

  // A loop that ticks every `ms` while el is visible.
  function loop(el, ms, tick) {
    watch(el);
    let n = 0;
    setInterval(() => { if (active(el)) tick(n++); }, ms);
  }

  // ---------------------------------------------------------------- nav

  const nav = $("#nav");
  const onScroll = () => nav.classList.toggle("scrolled", window.scrollY > 40);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  const menuBtn = $("#menu-btn"), menu = $("#mobile-menu");
  menuBtn.addEventListener("click", () => {
    const open = menu.hidden;
    menu.hidden = !open;
    menuBtn.setAttribute("aria-expanded", String(open));
    nav.classList.toggle("scrolled", open || window.scrollY > 40);
  });
  $$("a", menu).forEach((a) => a.addEventListener("click", () => { menu.hidden = true; menuBtn.setAttribute("aria-expanded", "false"); }));

  // ---------------------------------------------------------------- scroll reveals (once)

  if (reduced) $$("[data-reveal]").forEach((el) => el.classList.add("is-in"));
  else {
    const io = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); }
    }), { threshold: 0.15, rootMargin: "0px 0px -40px 0px" });
    $$("[data-reveal]").forEach((el) => io.observe(el));
  }

  // ---------------------------------------------------------------- hero: spotlight grid, rotator, product card

  const grid = $("#grid");
  if (desktop && !reduced) {
    const hero = $("#top");
    hero.addEventListener("pointermove", (e) => {
      const r = grid.getBoundingClientRect();
      grid.style.setProperty("--mx", `${e.clientX - r.left}px`);
      grid.style.setProperty("--my", `${e.clientY - r.top}px`);
      grid.classList.add("lit");
    });
    hero.addEventListener("pointerleave", () => grid.classList.remove("lit"));
  }

  // Word rotator: "Show it the bug / glitch / regression / crash." Stops on the first word with reduced motion.
  const rotator = $("#rotator"), fixWord = $("#fix-word"), title = $(".hero-title");
  if (!reduced) {
    const words = ["bug", "glitch", "regression", "crash"];
    rotator.innerHTML = words.map((w, i) => `<span class="${i ? "in-down" : ""}">${w}<i>.</i></span>`).join("");
    const spans = $$("span", rotator);
    // Measure each word's own width (spans stretch in the grid, so use a range over the text).
    const widthOf = (el) => { const r = document.createRange(); r.selectNodeContents(el); return Math.ceil(r.getBoundingClientRect().width); };
    const fit = (i) => { rotator.style.width = `${widthOf(spans[i])}px`; };
    let current = 0, paused = false;
    document.fonts && document.fonts.ready.then(() => fit(current));
    window.addEventListener("resize", () => fit(current));
    fit(0);
    title.addEventListener("mouseenter", () => { paused = true; });
    title.addEventListener("mouseleave", () => { paused = false; });
    watch(title);
    setInterval(() => {
      if (paused || !active(title)) return;
      const next = (current + 1) % words.length;
      spans[current].classList.add("out-up");
      spans[next].classList.remove("out-up");
      spans[next].classList.add("in-down");
      void spans[next].offsetWidth;                       // commit the start position
      spans[next].classList.remove("in-down");
      const prev = current;
      setTimeout(() => { spans[prev].classList.remove("out-up"); spans[prev].classList.add("in-down"); }, 460);
      fit(next);
      current = next;
      fixWord.classList.remove("flash");
      void fixWord.offsetWidth;
      fixWord.classList.add("flash");
    }, 2600);
  }

  // Product card: the recorded Coupon stacks run, replayed. Each stage runs, then lands on its real result.
  const heroVideo = $("#hero-video"), heroSteps = $$("#hero-steps .mini-step"), heroResult = $("#hero-result");
  const card = $(".hero-card");
  watch(card);
  if (!reduced) {
    once(card, () => { heroVideo.play().catch(() => {}); }, 0.3);
    const waitVisible = async () => { while (!active(card)) await sleep(300); };
    (async function replay() {
      await sleep(900);  // after the hero reveal
      for (;;) {
        heroSteps.forEach((s) => { s.dataset.state = "pending"; });
        heroResult.classList.add("off");
        await sleep(500);
        for (const step of heroSteps) {
          await waitVisible();
          step.dataset.state = "running";
          await sleep(step.dataset.final === "caught" ? 1100 : 750);
          step.dataset.state = step.dataset.final;
        }
        heroResult.classList.remove("off");
        await sleep(5000);
        await waitVisible();
      }
    })();
  }

  // ---------------------------------------------------------------- real runs: ticker and pings

  const ticker = $("#ticker"), marquee = $("#marquee");
  const ago = (ts) => {
    const m = Math.round((Date.now() / 1000 - ts) / 60);
    return m < 60 ? `${Math.max(1, m)} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
  };
  const runLine = (r) => {
    const outcome = r.success ? (r.pull_request ? `PR #${r.pull_request.number} opened` : "fixed and verified") : "not fixed";
    return { outcome, label: r.source === "live" ? `live run · ${ago(r.started)}` : "recorded run" };
  };

  function cloneTicker() {
    // A second copy makes the loop seamless; it is hidden from screen readers.
    $$("[data-clone]", ticker).forEach((el) => el.remove());
    $$("li", ticker).forEach((li) => { const c = li.cloneNode(true); c.dataset.clone = ""; c.setAttribute("aria-hidden", "true"); ticker.appendChild(c); });
  }

  let runs = [];
  // On the static build (scripts/build_static.py) the API lives on another origin. Asking for
  // /api/health first also wakes the free Render instance while the visitor reads the hero.
  const API = window.REPRO_API || "";
  if (API) fetch(`${API}/api/health`).catch(() => {});
  fetch(`${API}/api/recent`).then((r) => (r.ok ? r.json() : [])).then((data) => {
    runs = data;
    const live = data.filter((r) => r.source === "live").slice(0, 5);
    live.reverse().forEach((r) => {
      const { outcome, label } = runLine(r);
      ticker.insertAdjacentHTML("afterbegin", `<li class="flex flex-none items-center gap-2"><span class="${r.success ? "text-fix-fg" : "text-bug-fg"}">${r.success ? "✓" : "✕"}</span>${esc(r.title)}
        <span class="text-muted">·</span> ${esc(outcome)} <span class="text-muted">·</span> ${fmtClock(r.duration_s)} <span class="chip chip-ok">${esc(label)}</span></li>`);
    });
    if (!reduced) cloneTicker();
    schedulePings();
  }).catch(() => { if (!reduced) cloneTicker(); });
  watch(marquee);
  setInterval(() => marquee.classList.toggle("paused", !active(marquee)), 1000);

  // Pings: small toasts about real runs, at most 3 per visit, never on phones or with reduced motion.
  function schedulePings() {
    const box = $("#pings");
    if (reduced || !window.matchMedia("(min-width: 640px)").matches || !runs.length) return;
    let shown = 0;
    const queue = runs.filter((r) => r.success).slice(0, 3);
    const next = async () => {
      if (shown >= queue.length || box.dataset.closed) return;
      if (document.hidden) { setTimeout(next, 2000); return; }
      const r = queue[shown++];
      const { outcome, label } = runLine(r);
      const el = document.createElement("div");
      el.className = "ping-toast off pointer-events-auto mt-2 flex items-start gap-3 rounded-xl border border-line-strong/80 bg-surface-1/95 p-3 pr-2 text-sm shadow-2xl shadow-black/50 backdrop-blur";
      el.innerHTML = `<span class="pulse-dot mt-1.5 flex-none"></span>
        <div class="min-w-0 flex-1"><p class="font-medium text-ink">${esc(r.title)}: ${esc(outcome)}</p>
          <p class="text-xs text-ink-2">${fmtClock(r.duration_s)}${r.tests_total ? ` · ${r.tests_passed}/${r.tests_total} tests green` : ""} · ${esc(label)}</p></div>
        <button class="rounded p-1 text-muted hover:text-ink" aria-label="Dismiss">✕</button>`;
      $("button", el).addEventListener("click", () => { box.dataset.closed = "1"; el.remove(); });
      box.appendChild(el);
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove("off")));
      await sleep(4300);
      el.classList.add("off");
      await sleep(450);
      el.remove();
      setTimeout(next, 3000 + Math.random() * 2000);
    };
    // Start only once the visitor has scrolled past the hero, so a toast never sits on the CTAs.
    const hero = $("#top");
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => !e.isIntersecting)) { io.disconnect(); setTimeout(next, 2500); }
    }, { threshold: 0 });
    io.observe(hero);
  }

  // ---------------------------------------------------------------- before / after slider

  const ba = $("#ba"), range = $("#ba-range");
  const setPos = (v) => { ba.style.setProperty("--pos", `${v}%`); range.value = v; };
  range.addEventListener("input", () => { ba.dataset.touched = "1"; setPos(Number(range.value)); });
  if (!reduced) {
    setPos(100);
    once(ba, () => {
      const start = performance.now();
      (function frame(now) {
        if (ba.dataset.touched) return;
        const t = Math.min(1, (now - start) / 1200);
        const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;  // ease-in-out
        setPos(100 - 50 * eased);
        if (t < 1) requestAnimationFrame(frame);
      })(start);
    }, 0.5);
  }

  // ---------------------------------------------------------------- process draw (scroll-linked)

  const process = $("#process"), nodes = $$(".node", process);
  if (!reduced) {
    let ticking = false;
    const update = () => {
      ticking = false;
      const r = process.getBoundingClientRect();
      const vh = window.innerHeight;
      const p = Math.min(1, Math.max(0, (vh * 0.8 - r.top) / (r.height + vh * 0.25)));
      process.style.setProperty("--p", p.toFixed(3));
      nodes.forEach((n, i) => n.classList.toggle("lit", p >= i / (nodes.length - 1) - 0.001 && p > 0));
      process.classList.toggle("green", p >= 3 / (nodes.length - 1));  // reaching Verify turns the line green
    };
    window.addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
    window.addEventListener("resize", update);
    update();
  }

  // ---------------------------------------------------------------- see it run: hover-play

  $$("[data-card]").forEach(FX.hoverPlay);

  // ---------------------------------------------------------------- count-up stats

  const stats = $$("#numbers [data-count]");
  if (!reduced) {
    stats.forEach((el) => { el.textContent = "0"; });
    once($("#numbers"), () => stats.forEach((el) => FX.countUp(el)), 0.4);
  }

  // ---------------------------------------------------------------- bento micro-loops

  if (!reduced) {
    $$(".bento-tile").forEach((tile) => tile.addEventListener("pointermove", (e) => {
      const r = tile.getBoundingClientRect();
      tile.style.setProperty("--mx", `${e.clientX - r.left}px`);
      tile.style.setProperty("--my", `${e.clientY - r.top}px`);
    }));

    // Real keyframes and steps from the recorded Coupon stacks run (demo/coupon_stacks.json).
    const framesTile = $('[data-loop="frames"]'), frames = $$(".frames > figure", framesTile), caption = $("#frame-caption");
    const arrow = '<span class="text-muted">→</span>';
    const captions = [
      ["2.06", `click Add to cart ${arrow} Classic Tee card`],
      ["3.69", `click Add to cart ${arrow} Dev Mug card`],
      ["6.19", `type Coupon code ${arrow} <span class="text-string">"SAVE10"</span>`],
      ["7.44", `click Apply button ${arrow} Discount −$3.25`],
      ["10.62", `type Coupon code ${arrow} <span class="text-string">"SAVE10"</span> again`],
      ["11.94", `click Apply button ${arrow} Discount −$3.25 ${arrow} <span class="text-bug-fg">−$6.50 ✕</span>`],
    ];
    loop(framesTile, 1600, async (n) => {
      const i = n % frames.length;
      frames.forEach((f, j) => f.classList.toggle("hot", j === i));
      caption.classList.add("swap");
      await sleep(150);
      caption.innerHTML = `<span class="text-ident">t=${captions[i][0]}s</span> ${captions[i][1]}`;
      caption.classList.remove("swap");
    });

    const routes = [
      ["Perceive", "Nemotron 3.5 Super VL", "reads video and keyframes"],
      ["Triage", "Nemotron 3.5 Lightning", "ranks files, fast and cheap"],
      ["Reproduce", "Nemotron 3 Super", "writes the Playwright test"],
      ["Fix", "Nemotron 3 Super", "drives the tool-calling agent"],
      ["Escalate", "Nemotron 3 Ultra", "only after two failed runs"],
    ];
    const modelsTile = $('[data-loop="models"]');
    const swapEls = [$("#route-model"), $("#route-why")];
    loop(modelsTile, 2200, async (n) => {
      const [stage, model, why] = routes[(n + 1) % routes.length];
      swapEls.forEach((el) => el.classList.add("swap"));
      await sleep(200);
      $("#route-stage").textContent = stage;
      swapEls[0].textContent = model;
      swapEls[1].textContent = why;
      swapEls.forEach((el) => el.classList.remove("swap"));
    });

    // The Playwright test writes itself line by line, then runs red on the bug, once.
    const typingTile = $('[data-loop="typing"]'), lines = $$(".typed-line", typingTile), failLine = $(".fail-line", typingTile);
    typingTile.querySelector(".typing").classList.add("armed");
    failLine.classList.add("armed");
    once(typingTile, async () => {
      for (const line of lines) { line.classList.add("on"); await sleep(420); }
      await sleep(300);
      failLine.classList.add("on");
    }, 0.4);

    // The diff: the bug line glows red, then the guard slides in above it and the line goes quiet.
    const diffTile = $('[data-loop="diff"]');
    diffTile.classList.add("animating");
    loop(diffTile, 1300, (n) => diffTile.classList.toggle("fixed", n % 5 >= 2));  // 0-1 bug, 2-4 fixed

    const toolsTile = $('[data-loop="tools"]'), tools = $$("#tools .chip");
    const order = [0, 4, 0, 2, 3, 5];  // read_file, web_search, read_file, edit_file, run_tests, finish
    loop(toolsTile, 900, (n) => tools.forEach((t, i) => {
      const on = i === order[n % order.length];
      t.classList.toggle(i === 4 ? "chip-amber" : "chip-ok", on);
    }));

    const checksTile = $('[data-loop="checks"]'), checks = $$("#checks .check-row");
    loop(checksTile, 700, (n) => {
      const phase = n % 7;  // all off, then tick one by one, then hold
      checks.forEach((c, i) => c.classList.toggle("ok", phase > i && phase !== 0));
    });
  }

  // ---------------------------------------------------------------- proof stack (real results)

  const stack = $("#stack"), cards = $$("article", stack), count = $("#stack-count");
  let order = cards.map((_, i) => i), stackPaused = false;
  const layout = () => {
    order.forEach((cardIndex, pos) => {
      const c = cards[cardIndex];
      c.dataset.pos = String(Math.min(pos, 3));
      c.setAttribute("aria-hidden", String(pos !== 0));
      $$("a", c).forEach((a) => { a.tabIndex = pos === 0 ? 0 : -1; });
    });
    count.textContent = `${order[0] + 1} / ${cards.length}`;
  };
  const shuffle = async (dir = 1) => {
    if (dir > 0) {
      const top = cards[order[0]];
      if (!reduced) { top.classList.add("leaving"); await sleep(260); top.classList.remove("leaving"); }
      order.push(order.shift());
    } else {
      order.unshift(order.pop());
    }
    layout();
  };
  layout();
  $("#stack-next").addEventListener("click", () => shuffle(1));
  $("#stack-prev").addEventListener("click", () => shuffle(-1));
  stack.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") { e.preventDefault(); shuffle(1); }
    if (e.key === "ArrowLeft") { e.preventDefault(); shuffle(-1); }
  });
  ["mouseenter", "focusin"].forEach((t) => stack.addEventListener(t, () => { stackPaused = true; }));
  ["mouseleave", "focusout"].forEach((t) => stack.addEventListener(t, () => { stackPaused = false; }));
  let dragX = null;
  stack.addEventListener("pointerdown", (e) => { dragX = e.clientX; });
  stack.addEventListener("pointerup", (e) => {
    if (dragX !== null && Math.abs(e.clientX - dragX) > 50) shuffle(e.clientX < dragX ? 1 : -1);
    dragX = null;
  });
  if (!reduced) loop(stack, 5000, () => { if (!stackPaused) shuffle(1); });

  // ---------------------------------------------------------------- final CTA: click to success

  const cta = $("#cta-btn");
  cta.addEventListener("click", async (e) => {
    if (reduced || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0 || cta.dataset.state !== "idle") return;
    e.preventDefault();
    cta.dataset.state = "working";
    await sleep(550);
    cta.dataset.state = "caught";
    await sleep(550);
    cta.dataset.state = "fixed";
    FX.burstFrom(cta);
    await sleep(700);
    location.href = cta.href;
  });
  window.addEventListener("pageshow", () => { cta.dataset.state = "idle"; });  // back button from the app

  // ---------------------------------------------------------------- magnetic primary buttons (desktop only)

  if (desktop && !reduced) {
    const magnets = $$("[data-magnetic]");
    window.addEventListener("pointermove", (e) => {
      for (const m of magnets) {
        const r = m.getBoundingClientRect();
        const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        const dx = e.clientX - cx, dy = e.clientY - cy;
        const near = Math.abs(dx) < r.width / 2 + 80 && Math.abs(dy) < r.height / 2 + 80;
        m.style.transform = near ? `translate(${(dx / (r.width / 2 + 80)) * 6}px, ${(dy / (r.height / 2 + 80)) * 5}px)` : "";
      }
    }, { passive: true });
  }
})();
