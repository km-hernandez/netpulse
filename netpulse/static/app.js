/* NetPulse dashboard — dependency-free (vanilla JS + canvas). */
(() => {
  "use strict";

  const BEATS = 40;
  const SPARK_POINTS = 60;
  const state = {
    minutes: 60,
    targets: new Map(),   // id -> { meta, stats, recent: [] , el }
    bandwidth: [],
    selected: null,
    detailHistory: [],
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // ---------- formatting ----------
  const fmtMs = (v) => (v == null ? "—" : v < 10 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : Math.round(v).toString());
  const fmtPct = (v) => (v == null ? "—" : `${v.toFixed(v === 100 || v === 0 ? 0 : 1)}%`);
  const fmtBps = (bps) => {
    if (bps == null) return "—";
    const units = ["bps", "Kbps", "Mbps", "Gbps"];
    let i = 0;
    while (bps >= 1000 && i < units.length - 1) { bps /= 1000; i++; }
    return `${bps.toFixed(bps < 10 ? 2 : bps < 100 ? 1 : 0)} ${units[i]}`;
  };
  const fmtBytes = (b) => {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
    return `${b.toFixed(1)} ${units[i]}`;
  };
  const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  const classify = (r, warn) => (!r ? "" : !r.ok ? "bad" : r.latency_ms != null && r.latency_ms > warn ? "warn" : "ok");

  // ---------- canvas helpers ----------
  function setupCanvas(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const { width, height } = canvas.getBoundingClientRect();
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { ctx, w: width, h: height };
  }

  function niceMax(v) {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
  }

  /**
   * Generic time-series chart.
   * series: [{ points: [{t, v}], color, fill }]
   * opts: { yFmt, tMin, tMax, markers: [{t, color}] }
   */
  function drawSeries(canvas, series, opts) {
    const { ctx, w, h } = setupCanvas(canvas);
    const pad = { l: 78, r: 24, t: 10, b: 22 };
    const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
    const all = series.flatMap((s) => s.points.filter((p) => p.v != null));
    const tMin = opts.tMin ?? Math.min(...all.map((p) => p.t));
    const tMax = opts.tMax ?? Math.max(...all.map((p) => p.t));
    const yMax = niceMax(Math.max(1e-9, ...all.map((p) => p.v)) * 1.1);
    const x = (t) => pad.l + ((t - tMin) / Math.max(1e-9, tMax - tMin)) * iw;
    const y = (v) => pad.t + ih - (v / yMax) * ih;

    ctx.font = "11px ui-monospace, Menlo, monospace";
    ctx.fillStyle = css("--muted");
    ctx.strokeStyle = css("--grid");
    ctx.lineWidth = 1;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let i = 0; i <= 4; i++) {
      const v = (yMax / 4) * i, yy = Math.round(y(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r, yy); ctx.stroke();
      ctx.fillText(opts.yFmt(v), pad.l - 8, yy);
    }
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    if (isFinite(tMin) && isFinite(tMax) && tMax > tMin) {
      for (let i = 0; i <= 4; i++) {
        const t = tMin + ((tMax - tMin) / 4) * i;
        ctx.textAlign = i === 0 ? "left" : i === 4 ? "right" : "center";
        ctx.fillText(new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), x(t), h - pad.b + 6);
      }
    }

    (opts.markers || []).forEach((m) => {
      ctx.fillStyle = m.color;
      ctx.globalAlpha = 0.18;
      ctx.fillRect(x(m.t) - 1.5, pad.t, 3, ih);
      ctx.globalAlpha = 1;
    });

    for (const s of series) {
      const pts = s.points;
      if (!pts.length) continue;
      // split into segments at gaps (null values)
      const segments = [];
      let cur = [];
      for (const p of pts) {
        if (p.v == null) { if (cur.length) segments.push(cur); cur = []; }
        else cur.push(p);
      }
      if (cur.length) segments.push(cur);

      for (const seg of segments) {
        if (s.fill) {
          const g = ctx.createLinearGradient(0, pad.t, 0, pad.t + ih);
          g.addColorStop(0, s.color + "55");
          g.addColorStop(1, s.color + "00");
          ctx.fillStyle = g;
          ctx.beginPath();
          ctx.moveTo(x(seg[0].t), y(0));
          seg.forEach((p) => ctx.lineTo(x(p.t), y(p.v)));
          ctx.lineTo(x(seg[seg.length - 1].t), y(0));
          ctx.closePath();
          ctx.fill();
        }
        ctx.strokeStyle = s.color;
        ctx.lineWidth = 1.75;
        ctx.lineJoin = "round";
        ctx.beginPath();
        seg.forEach((p, i) => (i ? ctx.lineTo(x(p.t), y(p.v)) : ctx.moveTo(x(p.t), y(p.v))));
        ctx.stroke();
        if (seg.length === 1) { ctx.fillStyle = s.color; ctx.beginPath(); ctx.arc(x(seg[0].t), y(seg[0].v), 2, 0, 7); ctx.fill(); }
      }
    }
    canvas._geom = { x, y, pad, w, h, tMin, tMax, iw };
  }

  function attachTooltip(canvas, tip, getRows) {
    canvas.addEventListener("mousemove", (e) => {
      const g = canvas._geom;
      if (!g) return;
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      if (mx < g.pad.l || mx > g.w - g.pad.r) { tip.style.display = "none"; return; }
      const t = g.tMin + ((mx - g.pad.l) / g.iw) * (g.tMax - g.tMin);
      const html = getRows(t);
      if (!html) { tip.style.display = "none"; return; }
      tip.innerHTML = html;
      tip.style.display = "block";
      const left = Math.min(mx + 12, rect.width - tip.offsetWidth - 4);
      tip.style.left = `${Math.max(4, left)}px`;
      tip.style.top = "8px";
    });
    canvas.addEventListener("mouseleave", () => (tip.style.display = "none"));
  }

  const nearest = (arr, t, key = "ts") => {
    let best = null, bd = Infinity;
    for (const p of arr) { const d = Math.abs(p[key] - t); if (d < bd) { bd = d; best = p; } }
    return best;
  };

  function drawSpark(canvas, recent, warn) {
    const { ctx, w, h } = setupCanvas(canvas);
    const pts = recent.slice(-SPARK_POINTS);
    if (pts.length < 2) return;
    const vals = pts.map((r) => r.latency_ms).filter((v) => v != null);
    const max = Math.max(warn * 0.5, ...vals) * 1.15 || 1;
    const step = w / (SPARK_POINTS - 1);
    const off = (SPARK_POINTS - pts.length) * step;
    const color = css("--accent");
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    let pen = false;
    pts.forEach((r, i) => {
      const xx = off + i * step;
      if (r.latency_ms == null) {
        pen = false;
        ctx.save(); ctx.fillStyle = css("--bad"); ctx.fillRect(xx - 1, 0, 2, h); ctx.restore();
        return;
      }
      const yy = h - 2 - (r.latency_ms / max) * (h - 4);
      pen ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy);
      pen = true;
    });
    ctx.stroke();
    // warn threshold line
    if (warn < max) {
      const wy = h - 2 - (warn / max) * (h - 4);
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = css("--warn");
      ctx.globalAlpha = 0.6;
      ctx.beginPath(); ctx.moveTo(0, wy); ctx.lineTo(w, wy); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
    }
  }

  // ---------- target cards ----------
  function buildCard(t) {
    const el = $("#card-tpl").content.firstElementChild.cloneNode(true);
    el.dataset.id = t.id;
    $(".c-name", el).textContent = t.name;
    $(".c-endpoint", el).textContent = t.endpoint;
    $(".badge", el).textContent = t.type;
    const beats = $(".beats", el);
    for (let i = 0; i < BEATS; i++) beats.appendChild(document.createElement("i"));
    el.addEventListener("click", () => selectTarget(t.id));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectTarget(t.id); } });
    return el;
  }

  function renderCard(id) {
    const t = state.targets.get(id);
    if (!t) return;
    const el = t.el;
    const last = t.recent[t.recent.length - 1];
    const st = classify(last, t.meta.latency_warn_ms);
    el.dataset.state = st;
    $(".c-latency .v", el).textContent = last ? (last.ok ? fmtMs(last.latency_ms) : "DOWN") : "…";
    $(".c-latency .u", el).style.display = last && last.ok ? "" : "none";
    $(".c-detail", el).textContent = last ? `${fmtTime(last.ts)} · ${last.detail || ""}` : "waiting for first check";
    const s = t.stats || {};
    $(".s-up", el).textContent = fmtPct(s.uptime);
    $(".s-p95", el).textContent = s.p95_ms == null ? "—" : `${fmtMs(s.p95_ms)}ms`;
    $(".s-jit", el).textContent = s.jitter_ms == null ? "—" : `${fmtMs(s.jitter_ms)}ms`;
    $(".s-loss", el).textContent = fmtPct(s.loss);
    const cells = $(".beats", el).children;
    const beats = t.recent.slice(-BEATS);
    const offset = BEATS - beats.length;
    for (let i = 0; i < BEATS; i++) {
      const r = beats[i - offset];
      cells[i].className = r ? classify(r, t.meta.latency_warn_ms) : "";
      cells[i].title = r ? `${fmtTime(r.ts)} — ${r.ok ? fmtMs(r.latency_ms) + " ms" : r.detail}` : "";
    }
    drawSpark($(".spark", el), t.recent, t.meta.latency_warn_ms);
  }

  // ---------- KPIs ----------
  function renderKpis() {
    const all = [...state.targets.values()];
    const withData = all.filter((t) => t.recent.length);
    const up = withData.filter((t) => t.recent[t.recent.length - 1].ok).length;
    const degraded = withData.filter((t) => classify(t.recent[t.recent.length - 1], t.meta.latency_warn_ms) === "warn").length;
    $("#k-up").textContent = withData.length ? `${up}/${all.length}` : "—";
    $("#k-up-sub").textContent = degraded ? `${degraded} degraded` : withData.length ? "all healthy" : "";
    $("#k-up").style.color = withData.length && up < withData.length ? css("--bad") : "";

    const avgs = all.map((t) => t.stats?.avg_ms).filter((v) => v != null);
    $("#k-lat").textContent = avgs.length ? `${fmtMs(avgs.reduce((a, b) => a + b, 0) / avgs.length)} ms` : "—";
    const jit = all.map((t) => t.stats?.jitter_ms).filter((v) => v != null);
    $("#k-lat-sub").textContent = jit.length ? `jitter ${fmtMs(jit.reduce((a, b) => a + b, 0) / jit.length)} ms` : "";

    const samples = all.reduce((a, t) => a + (t.stats?.samples || 0), 0);
    const lost = all.reduce((a, t) => a + ((t.stats?.loss || 0) / 100) * (t.stats?.samples || 0), 0);
    $("#k-loss").textContent = samples ? fmtPct((lost / samples) * 100) : "—";
    $("#k-loss-sub").textContent = samples ? `${samples.toLocaleString()} checks · ${labelWindow()}` : "";
    $("#k-loss").style.color = samples && lost / samples > 0.02 ? css("--warn") : "";

    const last = state.bandwidth[state.bandwidth.length - 1];
    if (last) {
      $("#k-rx").textContent = fmtBps(last.rx_bps);
      $("#k-tx").textContent = fmtBps(last.tx_bps);
      $("#k-rx-sub").textContent = `${fmtBytes(last.rx_total)} total`;
      $("#k-tx-sub").textContent = `${fmtBytes(last.tx_total)} total`;
    }
  }
  const labelWindow = () => ({ 15: "15m", 60: "1h", 360: "6h", 1440: "24h" })[state.minutes];

  // ---------- bandwidth chart ----------
  function renderBandwidth() {
    const bw = state.bandwidth;
    const now = Date.now() / 1000;
    drawSeries($("#bw-chart"), [
      { points: bw.map((s) => ({ t: s.ts, v: s.rx_bps })), color: css("--rx"), fill: true },
      { points: bw.map((s) => ({ t: s.ts, v: s.tx_bps })), color: css("--tx"), fill: true },
    ], { yFmt: fmtBps, tMin: now - 300, tMax: now });
  }

  // ---------- detail ----------
  async function selectTarget(id) {
    state.selected = state.selected === id ? null : id;
    document.querySelectorAll(".card").forEach((c) => c.classList.toggle("selected", c.dataset.id === state.selected));
    const panel = $("#detail");
    if (!state.selected) { panel.classList.add("hidden"); return; }
    panel.classList.remove("hidden");
    await loadDetail();
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function loadDetail() {
    const id = state.selected;
    if (!id) return;
    const res = await fetch(`/api/targets/${encodeURIComponent(id)}/history?minutes=${state.minutes}`);
    if (!res.ok) return;
    state.detailHistory = await res.json();
    renderDetail();
  }

  function renderDetail() {
    const t = state.targets.get(state.selected);
    if (!t) return;
    $("#d-title").textContent = `${t.meta.name} — ${t.meta.endpoint}`;
    const s = t.stats || {};
    const rows = [
      ["Uptime", fmtPct(s.uptime)], ["Avg", s.avg_ms == null ? "—" : `${fmtMs(s.avg_ms)} ms`],
      ["p95", s.p95_ms == null ? "—" : `${fmtMs(s.p95_ms)} ms`], ["Min / Max", s.min_ms == null ? "—" : `${fmtMs(s.min_ms)} / ${fmtMs(s.max_ms)}`],
      ["Jitter", s.jitter_ms == null ? "—" : `${fmtMs(s.jitter_ms)} ms`], ["Loss", fmtPct(s.loss)],
      ["Samples", (s.samples || 0).toLocaleString()], ["Interval", `${t.meta.interval}s`],
    ];
    $("#d-stats").innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");

    const hist = state.detailHistory;
    const now = Date.now() / 1000;
    drawSeries($("#d-chart"), [
      { points: hist.map((r) => ({ t: r.ts, v: r.ok ? r.latency_ms : null })), color: css("--accent"), fill: true },
    ], {
      yFmt: (v) => `${fmtMs(v)} ms`,
      tMin: now - state.minutes * 60, tMax: now,
      markers: hist.filter((r) => !r.ok).map((r) => ({ t: r.ts, color: css("--bad") })),
    });

    const fails = hist.filter((r) => !r.ok).slice(-15).reverse();
    $("#d-events").innerHTML = fails.length
      ? fails.map((r) => `<tr><td>${new Date(r.ts * 1000).toLocaleString()}</td><td>${escapeHtml(r.detail || "failed")}</td></tr>`).join("")
      : `<tr><td colspan="2" class="muted">No failures in the last ${labelWindow()} 🎉</td></tr>`;
  }
  const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  // ---------- data loading ----------
  async function loadTargets(initial = false) {
    const res = await fetch(`/api/targets?minutes=${state.minutes}`);
    const list = await res.json();
    const grid = $("#targets");
    for (const t of list) {
      let entry = state.targets.get(t.id);
      if (!entry) {
        entry = { meta: t, stats: t.stats, recent: [], el: buildCard(t) };
        state.targets.set(t.id, entry);
        grid.appendChild(entry.el);
      }
      entry.meta = t;
      entry.stats = t.stats;
    }
    if (initial) {
      // seed sparklines/heartbeats with the most recent history
      await Promise.all(list.map(async (t) => {
        const h = await fetch(`/api/targets/${encodeURIComponent(t.id)}/history?minutes=15`).then((r) => r.json());
        state.targets.get(t.id).recent = h.slice(-SPARK_POINTS);
      }));
    }
    state.targets.forEach((_, id) => renderCard(id));
    renderKpis();
    if (state.selected) renderDetail();
  }

  function connect() {
    const live = $("#live");
    const es = new EventSource("/api/stream");
    es.onopen = () => { live.className = "live on"; $(".label", live).textContent = "live"; };
    es.onerror = () => { live.className = "live off"; $(".label", live).textContent = "reconnecting"; };
    es.addEventListener("check", (e) => {
      const r = JSON.parse(e.data);
      const t = state.targets.get(r.target_id);
      if (!t) return;
      t.recent.push(r);
      if (t.recent.length > SPARK_POINTS) t.recent.shift();
      renderCard(r.target_id);
      renderKpis();
      if (state.selected === r.target_id) { state.detailHistory.push(r); renderDetail(); }
    });
    es.addEventListener("bandwidth", (e) => {
      state.bandwidth.push(JSON.parse(e.data));
      if (state.bandwidth.length > 300) state.bandwidth.shift();
      renderBandwidth();
      renderKpis();
    });
  }

  // ---------- controls ----------
  document.querySelectorAll(".window button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".window button").forEach((x) => x.classList.toggle("active", x === b));
      state.minutes = Number(b.dataset.min);
      loadTargets();
      loadDetail();
    })
  );
  $("#d-close").addEventListener("click", () => selectTarget(state.selected));

  const savedTheme = (() => { try { return localStorage.getItem("netpulse-theme"); } catch { return null; } })();
  const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
  document.documentElement.dataset.theme = savedTheme || (prefersLight ? "light" : "dark");
  $("#theme").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("netpulse-theme", next); } catch {}
    redrawAll();
  });

  function redrawAll() {
    state.targets.forEach((_, id) => renderCard(id));
    renderBandwidth();
    if (state.selected) renderDetail();
  }
  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(redrawAll, 100); });

  attachTooltip($("#bw-chart"), $("#bw-tip"), (t) => {
    const s = nearest(state.bandwidth, t);
    return s && `<div class="muted">${fmtTime(s.ts)}</div><div style="color:${css("--rx")}">↓ <b>${fmtBps(s.rx_bps)}</b></div><div style="color:${css("--tx")}">↑ <b>${fmtBps(s.tx_bps)}</b></div>`;
  });
  attachTooltip($("#d-chart"), $("#d-tip"), (t) => {
    const r = nearest(state.detailHistory, t);
    return r && `<div class="muted">${fmtTime(r.ts)}</div><div><b>${r.ok ? fmtMs(r.latency_ms) + " ms" : "DOWN"}</b> · ${escapeHtml(r.detail || "")}</div>`;
  });

  // ---------- boot ----------
  (async () => {
    fetch("/api/system").then((r) => r.json()).then((s) => {
      const iface = s.interfaces[0];
      $("#host").textContent = `${s.hostname}${iface ? ` · ${iface.name} ${iface.ipv4[0]}` : ""}`;
    }).catch(() => {});
    state.bandwidth = await fetch("/api/bandwidth").then((r) => r.json()).catch(() => []);
    await loadTargets(true);
    renderBandwidth();
    connect();
    setInterval(() => loadTargets(), 30000); // refresh aggregate stats
  })();
})();
