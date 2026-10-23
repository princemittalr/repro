// Shared motion helpers for the landing page and the app. Every effect respects reduced motion.
"use strict";

window.FX = (() => {
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Small particle burst for success moments: emerald and white only, ~1s, canvas so it never touches layout.
  function burst(x, y, { count = 70, colors = ["#10b981", "#34d399", "#6ee7b7", "#ffffff"] } = {}) {
    if (reduced()) return;
    const canvas = document.createElement("canvas");
    canvas.className = "burst";
    canvas.setAttribute("aria-hidden", "true");
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = innerWidth * dpr;
    canvas.height = innerHeight * dpr;
    document.body.appendChild(canvas);
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    const parts = Array.from({ length: count }, () => {
      const angle = Math.random() * Math.PI * 2;
      const speed = 3 + Math.random() * 7;
      return { x, y, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed - 3, size: 3 + Math.random() * 4,
               rot: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.3, color: colors[(Math.random() * colors.length) | 0] };
    });
    const start = performance.now();
    (function frame(now) {
      const t = (now - start) / 1100;
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      for (const p of parts) {
        p.vy += 0.25; p.vx *= 0.985; p.x += p.vx; p.y += p.vy; p.rot += p.vr;
        ctx.save();
        ctx.globalAlpha = Math.max(0, 1 - t);
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.fillStyle = p.color;
        ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
        ctx.restore();
      }
      if (t < 1) requestAnimationFrame(frame); else canvas.remove();
    })(start);
  }

  function burstFrom(el) {
    const r = el.getBoundingClientRect();
    burst(r.left + r.width / 2, r.top + r.height / 2);
  }

  // Count a number up from 0 (ease-out, tabular figures come from CSS).
  function countUp(el, { duration = 1500 } = {}) {
    const target = Number(el.dataset.count);
    const decimals = Number(el.dataset.decimals || 0);
    const render = (v) => { el.textContent = v.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }); };
    if (reduced() || !Number.isFinite(target)) { render(target); return; }
    const start = performance.now();
    (function frame(now) {
      const t = Math.min(1, (now - start) / duration);
      render(target * (1 - Math.pow(1 - t, 4)));
      if (t < 1) requestAnimationFrame(frame);
    })(start);
  }

  // Hover-play for sample cards: a muted loop of the bug while the pointer is over the card.
  function hoverPlay(card) {
    const media = card.querySelector(".sample-media");
    const video = media && media.querySelector("video");
    if (!video || !window.matchMedia("(hover: hover)").matches || reduced()) return;
    card.addEventListener("mouseenter", () => {
      if (!video.src) video.src = video.dataset.src;
      video.currentTime = Number(video.dataset.start || 0);
      video.play().then(() => media.classList.add("playing")).catch(() => {});
    });
    card.addEventListener("mouseleave", () => { video.pause(); media.classList.remove("playing"); });
  }

  return { reduced, burst, burstFrom, countUp, hoverPlay };
})();
