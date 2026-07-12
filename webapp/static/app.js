/* Telemetry AI Dashboard 前端：session/圈選擇 + 三張同步 uPlot 圖 + 區段表 */
"use strict";

// 圖表顏色一律讀自 CSS 變數，才能跟著亮／暗主題自動切換
function readColors() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n, fallback) => (cs.getPropertyValue(n).trim() || fallback);
  return {
    a: v("--series-a", "#3987e5"), b: v("--series-b", "#199e70"),
    ink: v("--ink", "#ffffff"), ink2: v("--ink-2", "#c3c2b7"),
    muted: v("--muted", "#898781"), grid: v("--grid", "#2c2c2a"),
    baseline: v("--baseline", "#383835"),
    loss: v("--loss", "#e66767"), gain: v("--gain", "#0ca30c"),
  };
}
let COLORS = readColors();
const SYNC_KEY = "acc-telemetry";

const $ = (id) => document.getElementById(id);
let charts = [];
let currentZones = [];
let mapState = null;       // 地圖繪製狀態（游標點用）
let lastMapArgs = null;    // 最後一次 renderMap 的參數（resize 重繪用）
let syncingScale = false;  // 防止 x 軸縮放同步遞迴
let lastData = null;       // 最近一次 /api/compare 的回應（重建圖表用）
let speedMode = "overlay"; // "overlay" = 兩線疊圖, "diff" = 差值
let isSingle = false;      // 單圈分析模式
let coachCtx = { a: 0, b: 0 };  // 對話所屬的圈（b=0 表單圈）

function zoomTo(startPct, endPct) {
  if (!charts.length) return;
  // 微分段／區段列在別的分頁，縮放的是通道圖——自動切過去才看得到
  switchDashTab("channels");
  const padding = (endPct - startPct) * 0.3; // 前後多留 30% 脈絡
  charts[0].setScale("x", { min: Math.max(0, startPct - padding),
                            max: Math.min(100, endPct + padding) });
}

function fmtLap(ms) {
  if (ms == null || ms <= 0) return "--:--.---";
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const milli = ms % 1000;
  return `${m}:${String(s).padStart(2, "0")}.${String(milli).padStart(3, "0")}`;
}

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || data.message || res.statusText);
  return data;
}

/* ---------- 選單 ---------- */

// 每個遊戲的圖標：SVG glyph + 品牌色。text-only 的 <option> 用 tag 字串。
const GAME_META = {
  acc:     { tag: "ACC", color: "#3987e5", glyph: "wheel" },
  iracing: { tag: "iR",  color: "#e0503a", glyph: "flag" },
  f1_25:   { tag: "F1",  color: "#d4462a", glyph: "car" },
};

const GAME_TAG = Object.fromEntries(
  Object.entries(GAME_META).map(([k, v]) => [k, v.tag]));

const GLYPHS = {
  wheel: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="2.4" fill="currentColor" stroke="none"/>' +
         '<path d="M12 9.6V4M9.9 13.2 5 16M14.1 13.2 19 16"/>',
  flag: '<path d="M6 21V4"/><path d="M6 5h11l-2 3 2 3H6" fill="currentColor" stroke="none"/>',
  car: '<path d="M3 14h18M5 14l1.6-4.2A2 2 0 0 1 8.5 8.5h7a2 2 0 0 1 1.9 1.3L19 14"/>' +
       '<circle cx="7.5" cy="16.5" r="1.6" fill="currentColor" stroke="none"/>' +
       '<circle cx="16.5" cy="16.5" r="1.6" fill="currentColor" stroke="none"/>',
};

function gameIcon(game, withTag = true) {
  const m = GAME_META[game] || { tag: (game || "?").toUpperCase().slice(0, 3),
                                 color: "#898781", glyph: "wheel" };
  const svg = `<svg class="game-glyph" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="1.6" stroke-linecap="round"
    stroke-linejoin="round">${GLYPHS[m.glyph] || GLYPHS.wheel}</svg>`;
  return `<span class="game-badge" style="--gb:${m.color}">${svg}${
    withTag ? `<span>${m.tag}</span>` : ""}</span>`;
}

function sessionLabel(s) {
  const tag = GAME_TAG[s.game] || s.game || "?";
  const base = s.label || `${s.track || "?"} · ${s.car_model || "?"}`;
  return `#${s.session_id} [${tag}] ${base} · ${s.lap_count} laps`;
}

/* ---------- 首頁 / 導覽 ---------- */

let currentView = "home";
let allSessions = [];
let pausedTraining = null;   // /api/train/progress：暫停中的訓練（可續傳）

async function init() {
  currentView = "home";
  $("home-view").hidden = false;
  $("dashboard-view").hidden = true;
  await renderHome();
}

async function renderHome() {
  const [sessions, pbs, trainings, prog] = await Promise.all([
    fetchJSON("/api/sessions"),
    fetchJSON("/api/personal-bests").catch(() => []),
    fetchJSON("/api/trainings").catch(() => []),
    fetchJSON("/api/train/progress").catch(() => ({ exists: false })),
  ]);
  pausedTraining = prog && prog.exists ? prog : null;
  allSessions = sessions;
  const withLaps = sessions.filter((s) => s.lap_count > 0);

  // 個人最佳
  const pbEl = $("personal-bests");
  pbEl.innerHTML = pbs.length
    ? pbs.map((p) => `<div class="pb-cell">
        <div class="pb-track">${p.track || "?"}</div>
        <div class="pb-time">${fmtLap(p.best_ms)}</div>
        <div class="pb-game">${gameIcon(p.game)} · ${p.sessions} 場</div>
      </div>`).join("")
    : '<div class="pb-empty">還沒有有效圈紀錄。</div>';

  // session 卡片（新到舊）
  const cards = $("session-cards");
  if (!withLaps.length) {
    cards.innerHTML = '<div class="pb-empty">還沒有 session——按「開始錄製」進遊戲跑幾圈。</div>';
  } else {
    cards.innerHTML = withLaps.slice().reverse().map((s) => {
      const name = s.label || `${s.track || "?"} · ${s.car_model || "?"}`;
      const when = (s.started_at || "").replace("T", " ");
      return `<div class="session-card" data-id="${s.session_id}">
        ${gameIcon(s.game, false)}
        <div class="sc-main"><div class="sc-track">${name}</div>
          <div class="sc-sub">${s.lap_count} 圈 · ${when}</div></div>
        <span class="sc-best">${fmtLap(s.best_ms)}</span>
      </div>`;
    }).join("");
    for (const card of cards.querySelectorAll(".session-card")) {
      card.onclick = () => openSession(Number(card.dataset.id));
    }
  }

  // 訓練紀錄
  const sec = $("train-hist-sec");
  if (trainings.length) {
    sec.hidden = false;
    $("training-history").innerHTML = trainings.map((t) => {
      const total = t.score != null ? t.score : "—";
      const color = t.score >= 80 ? "var(--gain)" : t.score >= 50 ? "var(--ink)" : "var(--loss)";
      return `<div class="th-row">
        <span class="th-score" style="color:${color}">${total}</span>
        <span class="th-meta">${t.kind} · ${t.track || "?"} · ${(t.created_at || "").replace("T", " ")}</span>
      </div>`;
    }).join("");
  } else {
    sec.hidden = true;
  }
}

async function openSession(sessionId) {
  currentView = "dashboard";
  $("home-view").hidden = true;
  $("dashboard-view").hidden = false;
  const withLaps = allSessions.filter((s) => s.lap_count > 0);
  const sel = $("session-select");
  sel.innerHTML = withLaps.map((s) =>
    `<option value="${s.session_id}">${sessionLabel(s)}</option>`).join("");
  sel.value = String(sessionId);
  sel.onchange = () => loadSession(Number(sel.value));
  await loadSession(sessionId);
}

function goHome() {
  init().catch(() => {});
}

function setupNav() {
  $("back-home").onclick = goHome;
}

/* ---------- session 管理 ---------- */

let deleteArmed = null;

function setupSessionActions() {
  $("rename-btn").onclick = () => {
    const row = $("rename-row");
    row.hidden = !row.hidden;
    if (!row.hidden) $("rename-input").focus();
  };
  $("rename-save").onclick = async () => {
    const id = $("session-select").value;
    await fetchJSON(`/api/sessions/${id}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: $("rename-input").value }),
    });
    $("rename-row").hidden = true;
    $("rename-input").value = "";
    await refreshSessions(Number(id));   // 更新選單標籤，留在原 session
  };
  $("delete-btn").onclick = async () => {
    const btn = $("delete-btn");
    if (deleteArmed === null) {           // 第一次點：進入確認狀態
      btn.textContent = "確認刪除？";
      btn.classList.add("confirm");
      deleteArmed = setTimeout(() => {
        btn.textContent = "刪除";
        btn.classList.remove("confirm");
        deleteArmed = null;
      }, 3000);
      return;
    }
    clearTimeout(deleteArmed);
    deleteArmed = null;
    btn.textContent = "刪除";
    btn.classList.remove("confirm");
    const id = $("session-select").value;
    await fetchJSON(`/api/sessions/${id}`, { method: "DELETE" });
    goHome();   // 刪掉當前 session → 回首頁
  };
}

async function refreshSessions(keepId) {
  const sessions = await fetchJSON("/api/sessions");
  allSessions = sessions;
  const withLaps = sessions.filter((s) => s.lap_count > 0);
  const sel = $("session-select");
  sel.innerHTML = withLaps.map((s) =>
    `<option value="${s.session_id}">${sessionLabel(s)}</option>`).join("");
  const ids = withLaps.map((s) => String(s.session_id));
  sel.value = ids.includes(String(keepId)) ? String(keepId) : ids[ids.length - 1];
}

/* ---------- 錄製控制 ---------- */

let recordPoll = null;
let lastLapsSaved = 0;

function setRecordUI(st) {
  const btn = $("record-btn");
  const box = $("record-status");
  const isTrain = st.mode === "train";
  const active = st.phase === "waiting" || st.phase === "recording";
  // 訓練進行時，錄製卡改為唯讀提示（同一個錄製服務不能兩用）
  if (active && isTrain) {
    btn.textContent = "訓練進行中";
    btn.classList.add("armed");
    btn.disabled = true;
    box.textContent = "555 訓練使用中——見右側面板";
    return;
  }
  btn.disabled = false;
  if (st.phase === "waiting") {
    btn.textContent = "■ 停止";
    btn.classList.add("armed");
    box.textContent = "等待遊戲進入賽道…（支援 ACC / iRacing / F1 25，自動偵測）";
  } else if (st.phase === "recording") {
    btn.textContent = "■ 停止錄製";
    btn.classList.add("armed");
    box.innerHTML = `<span class="rec-dot"></span>REC [${st.game_name || ""}] ${st.track || ""}\n` +
      `Lap ${st.current_lap} @ ${st.spline_pct}% · ${st.current_time}\n` +
      `已存 ${st.laps_saved} 圈` +
      `${st.opp_laps ? ` · 對手 ${st.opp_laps} 圈` : ""}` +
      `${st.last_lap ? " · " + st.last_lap : ""}`;
  } else if (st.phase === "error") {
    btn.textContent = "● 開始錄製";
    btn.classList.remove("armed");
    box.textContent = "錄製錯誤：" + st.error;
  } else {
    btn.textContent = "● 開始錄製";
    btn.classList.remove("armed");
    box.textContent = st.message || "自動偵測 ACC / iRacing / F1 25";
  }
}

async function pollRecordStatus() {
  const st = await fetchJSON("/api/record/status");
  const active = st.phase === "waiting" || st.phase === "recording";
  const isTrain = st.mode === "train";

  // 訓練模式 → 專注畫面（完成後 recording 仍在跑，active 仍 true，續顯示得分）
  if (isTrain && active) {
    if (!inFocus) showFocus();
    renderFocus(st);
  } else if (inFocus && !isTrain) {
    exitFocus();               // 訓練被外部結束 → 離開專注畫面
    goHome();
  }

  if (!inFocus) {
    setRecordUI(st);
    renderTrainPanel(st);
  }

  if (active) {
    if ((st.laps_saved || 0) !== lastLapsSaved) {
      lastLapsSaved = st.laps_saved || 0;
      if (currentView === "home" && !inFocus) await renderHome();
    }
    recordPoll = setTimeout(pollRecordStatus, 1000);
  } else {
    recordPoll = null;
    if (lastLapsSaved > 0 || st.session_id) {
      lastLapsSaved = 0;
      if (currentView === "home" && !inFocus) await renderHome();
    }
  }
}

function setupRecording() {
  $("record-btn").onclick = async () => {
    const st = await fetchJSON("/api/record/status");
    if (st.phase === "waiting" || st.phase === "recording") {
      await fetchJSON("/api/record/stop", { method: "POST" });
      if (recordPoll) clearTimeout(recordPoll);
      recordPoll = null;
      await pollRecordStatus();
    } else {
      await fetchJSON("/api/record/start", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "record" }),
      });
      lastLapsSaved = 0;
      pollRecordStatus();
    }
  };
  pollRecordStatus();   // 啟動時同步一次（app 重開時錄製可能仍在跑）
}

let lapsCache = [];        // 目前 session 全部圈（含對手）
let bestLapId = null;
let currentLapA = null;    // 拖到 A 欄的 lap_id
let currentLapB = null;    // 拖到 B 欄的 lap_id（null = 單圈模式）

function lapById(id) {
  return lapsCache.find((l) => l.lap_id === id) || null;
}

// 車手清單：我在最前，對手依「最快圈」排序（名次＝速度快慢）
function driverGroups() {
  const mine = lapsCache.filter((l) => l.is_complete && !l.driver);
  const oppMap = {};
  for (const l of lapsCache.filter((l) => l.is_complete && l.driver)) {
    (oppMap[l.driver] = oppMap[l.driver] || []).push(l);
  }
  const best = (ls) => Math.min(...ls
    .filter((l) => l.is_valid && l.lap_time_ms).map((l) => l.lap_time_ms), Infinity);
  const opps = Object.entries(oppMap)
    .map(([name, ls]) => ({ name, laps: ls, best: best(ls) }))
    .sort((a, b) => a.best - b.best);
  return { mine, opps };
}

async function loadSession(sessionId) {
  const { laps, best_lap_id } = await fetchJSON(`/api/laps/${sessionId}`);
  lapsCache = laps;
  bestLapId = best_lap_id;
  const { mine } = driverGroups();

  renderDriverSelect();
  renderPalette();
  renderLapTrend(laps, best_lap_id);

  if (!mine.length && !laps.some((l) => l.is_complete)) {
    $("dashboard").style.display = "none";
    $("empty-state").style.display = "";
    $("empty-state").textContent = "此 session 沒有完整圈可分析。";
    return;
  }
  // 預設：A = 我的最快圈；B = 我最近另一完整圈（只有一圈 → 單圈）
  const others = mine.filter((l) => l.lap_id !== best_lap_id);
  currentLapA = best_lap_id ?? (mine[0] && mine[0].lap_id) ??
    (laps.find((l) => l.is_complete) || {}).lap_id ?? null;
  currentLapB = others.length ? others[others.length - 1].lap_id : null;
  if (currentLapA == null) {
    $("dashboard").style.display = "none";
    $("empty-state").style.display = "";
    $("empty-state").textContent = "此 session 沒有完整圈可分析。";
    return;
  }
  await compare();
}

function renderDriverSelect() {
  const { mine, opps } = driverGroups();
  const sel = $("driver-select");
  const prev = sel.value;
  let html = mine.length ? '<option value="__mine">我的圈</option>' : "";
  opps.forEach((o, i) =>
    html += `<option value="${o.name}">${i + 1}. ${o.name}${
      isFinite(o.best) ? "（最快 " + fmtLap(o.best) + "）" : ""}</option>`);
  sel.innerHTML = html;
  if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
  sel.onchange = renderPalette;
}

// 目前選中車手的圈，做成可拖曳卡片
function renderPalette() {
  const who = $("driver-select").value || "__mine";
  const laps = who === "__mine"
    ? lapsCache.filter((l) => l.is_complete && !l.driver)
    : lapsCache.filter((l) => l.is_complete && l.driver === who);
  $("lap-palette").innerHTML = laps.map((l) => {
    const badges = [];
    if (l.lap_id === bestLapId) badges.push('<span class="badge best">BEST</span>');
    if (!l.is_valid) badges.push('<span class="badge invalid">INV</span>');
    const slot = l.lap_id === currentLapA ? "in-a"
      : l.lap_id === currentLapB ? "in-b" : "";
    return `<div class="lap-chip ${slot}" draggable="true" data-lapid="${l.lap_id}">
      <span>Lap ${l.lap_number} · ${fmtLap(l.lap_time_ms)}</span>
      <span class="badges">${badges.join("")}</span></div>`;
  }).join("") || '<div class="palette-hint">此車手沒有完整圈</div>';
  for (const chip of $("lap-palette").querySelectorAll(".lap-chip")) {
    chip.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/lapid", chip.dataset.lapid);
      e.dataTransfer.effectAllowed = "copy";
    });
    // 雙擊快速指派：優先填 A，A 有了填 B
    chip.addEventListener("dblclick", () => {
      const id = Number(chip.dataset.lapid);
      if (currentLapA == null || currentLapA === id) assignSlot("a", id);
      else assignSlot("b", id);
    });
  }
}

function slotChipHTML(id) {
  const l = lapById(id);
  if (!l) return "拖曳圈到此";
  const who = l.driver ? l.driver : "我";
  return `<div class="slot-chip">
    <div class="slot-chip-main">${who} · Lap ${l.lap_number}</div>
    <div class="slot-chip-time">${fmtLap(l.lap_time_ms)}${l.is_valid ? "" : " (無效)"}</div>
    <button class="slot-clear" data-slot-clear="${l.lap_id}">✕</button></div>`;
}

function renderSlots() {
  $("slot-a-content").innerHTML = currentLapA ? slotChipHTML(currentLapA) : "拖曳圈到此";
  $("slot-b-content").innerHTML = currentLapB ? slotChipHTML(currentLapB) : "拖曳圈到此（留空＝單圈）";
  $("slot-a").classList.toggle("filled", !!currentLapA);
  $("slot-b").classList.toggle("filled", !!currentLapB);
  for (const btn of document.querySelectorAll(".slot-clear")) {
    btn.onclick = (e) => {
      e.stopPropagation();
      if (Number(btn.dataset.slotClear) === currentLapA) clearSlot("a");
      else clearSlot("b");
    };
  }
}

async function assignSlot(slot, id) {
  const prevA = currentLapA, prevB = currentLapB;
  if (slot === "a") {
    if (currentLapB === id) currentLapB = null;   // 同一圈不能同時在兩欄
    currentLapA = id;
  } else {
    if (currentLapA === id) return;               // B 不能等於 A
    currentLapB = id;
  }
  const ok = await compare();
  if (!ok && (prevA !== currentLapA || prevB !== currentLapB)) {
    // 這組圈無法比較（常見：對手圈資料不完整）→ 還原，保留原本可用的畫面
    const msg = lastCompareError || "這兩圈無法比較";
    currentLapA = prevA; currentLapB = prevB;
    if (prevA != null) await compare();
    showCompareToast(msg);
  }
}

let compareToastTimer = null;
function showCompareToast(msg) {
  const el = $("compare-toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(compareToastTimer);
  compareToastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

async function clearSlot(slot) {
  if (slot === "a") {
    // A 清空 → 把 B 提上來當 A（維持至少一圈）
    currentLapA = currentLapB;
    currentLapB = null;
  } else {
    currentLapB = null;
  }
  if (currentLapA == null) { renderSlots(); renderPalette(); return; }
  await compare();
}

function setupDragDrop() {
  for (const slot of ["a", "b"]) {
    const el = $("slot-" + slot);
    el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("drag-over"); });
    el.addEventListener("dragleave", () => el.classList.remove("drag-over"));
    el.addEventListener("drop", (e) => {
      e.preventDefault();
      el.classList.remove("drag-over");
      const id = Number(e.dataTransfer.getData("text/lapid"));
      if (id) assignSlot(slot, id);
    });
  }
  $("slot-swap").onclick = async () => {
    if (!currentLapB) return;
    [currentLapA, currentLapB] = [currentLapB, currentLapA];
    await compare();
  };
}

/* ---------- 圖表 ---------- */

function zonesPlugin(withLabels = false) {
  // 在資料層下方畫出「損失最大」煞車區段的灰底；withLabels 時標上區段編號
  return {
    hooks: {
      drawClear: (u) => {
        const worst = isSingle
          ? currentZones   // 單圈：全部區段都淡淡標出
          : [...currentZones]
              .sort((x, y) => Math.abs(y.time_lost_s) - Math.abs(x.time_lost_s))
              .slice(0, 3);
        const ctx = u.ctx;
        ctx.save();
        for (const z of worst) {
          const x0 = u.valToPos(z.start_pct, "x", true);
          const x1 = u.valToPos(z.end_pct, "x", true);
          ctx.fillStyle = "rgba(137, 135, 129, 0.10)";
          ctx.fillRect(x0, u.bbox.top, x1 - x0, u.bbox.height);
          if (withLabels) {
            ctx.fillStyle = COLORS.muted;
            ctx.font = `${11 * devicePixelRatio}px system-ui`;
            ctx.textAlign = "center";
            ctx.fillText(`#${z.index}`, (x0 + x1) / 2,
                         u.bbox.top + 13 * devicePixelRatio);
          }
        }
        ctx.restore();
      },
    },
  };
}

function axisOpts(labelSize) {
  return {
    stroke: COLORS.muted,
    grid: { stroke: COLORS.grid, width: 1 },
    ticks: { stroke: COLORS.baseline, width: 1 },
    size: labelSize,
    font: "11px system-ui",
  };
}

function makeChart(elId, height, series, data, extra = {}) {
  const el = $(elId);
  el.innerHTML = "";
  const opts = {
    width: el.clientWidth || 800,
    height,
    scales: { x: { time: false } },
    cursor: {
      sync: { key: SYNC_KEY },
      points: { size: 7 },
    },
    legend: { live: true },
    series: [
      { label: "位置%", value: (u, v) => (v == null ? "-" : v.toFixed(1) + "%") },
      ...series,
    ],
    axes: [axisOpts(40), axisOpts(50)],
    plugins: [zonesPlugin(elId === "chart-speed")],
    hooks: {
      setCursor: [(u) => drawMapCursor(u.cursor.idx)],
      setScale: [(u, key) => {           // 拖曳縮放時同步所有圖的 x 軸
        if (key !== "x" || syncingScale) return;
        const { min, max } = u.scales.x;
        if (min == null || max == null) return; // 圖表初始化中，勿廣播無效值
        syncingScale = true;
        try {
          for (const c of charts) if (c !== u) c.setScale("x", { min, max });
        } finally {
          syncingScale = false;
        }
      }],
    },
    ...extra,
  };
  const chart = new uPlot(opts, data, el);
  chart._baseHeight = height;      // 放大後還原用
  charts.push(chart);
  return chart;
}

function destroyCharts() {
  charts.forEach((c) => c.destroy());
  charts = [];
}

function seriesPair(labelA, labelB, { dash, width = 1.2 } = {}) {
  const v = (u, val) => (val == null ? "-" : val.toFixed(1));
  return [
    { label: labelA, stroke: COLORS.a, width, dash, value: v, points: { show: false } },
    { label: labelB, stroke: COLORS.b, width, dash, value: v, points: { show: false } },
  ];
}

/* ---------- 賽道地圖 ---------- */

function renderMap(d, single = false) {
  lastMapArgs = { d, single };
  const card = $("map-card");
  const spanOK = d.map_x && d.map_y &&
    (Math.max(...d.map_x) - Math.min(...d.map_x) > 10 ||
     Math.max(...d.map_y) - Math.min(...d.map_y) > 10);
  if (!spanOK) {   // 無座標通道，或座標沒有實際移動（舊資料全 0）
    card.style.display = "none";
    mapState = null;
    return;
  }
  card.querySelector(".unit").textContent = single
    ? "顏色 = 車速（深色快），游標連動" : "紅 = B 損失路段，游標連動";
  card.style.display = "";
  const canvas = $("track-map");
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) {   // 容器尚不可見（防禦，不讓地圖炸掉整個視圖）
    card.style.display = "none";
    mapState = null;
    return;
  }
  canvas.width = rect.width * devicePixelRatio;
  canvas.height = rect.height * devicePixelRatio;

  const xs = d.map_x, ys = d.map_y;
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const pad = 24 * devicePixelRatio;
  const scale = Math.min(
    (canvas.width - pad * 2) / (maxX - minX || 1),
    (canvas.height - pad * 2) / (maxY - minY || 1));
  const ox = (canvas.width - (maxX - minX) * scale) / 2;
  const oy = (canvas.height - (maxY - minY) * scale) / 2;
  const px = (i) => ox + (xs[i] - minX) * scale;
  const py = (i) => canvas.height - (oy + (ys[i] - minY) * scale);

  mapState = { px, py, n: xs.length, canvas };

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.lineWidth = 3.5 * devicePixelRatio;
  ctx.lineCap = "round";
  if (single) {
    // 單圈：以車速著色（淺→深藍）
    const sMin = Math.min(...d.speed), sMax = Math.max(...d.speed);
    const lerp = (a, b, t) => Math.round(a + (b - a) * t);
    for (let i = 1; i < xs.length; i++) {
      const t = sMax > sMin ? (d.speed[i] - sMin) / (sMax - sMin) : 0;
      ctx.strokeStyle = `rgb(${lerp(158, 16, t)},${lerp(197, 66, t)},${lerp(244, 129, t)})`;
      ctx.beginPath();
      ctx.moveTo(px(i - 1), py(i - 1));
      ctx.lineTo(px(i), py(i));
      ctx.stroke();
    }
  } else {
    // 比較：依局部 delta 斜率著色：紅 = B 在此路段損失，綠 = 賺，灰 = 打平
    const TH = 0.004; // 每格 4ms 以內視為打平
    for (let i = 1; i < xs.length; i++) {
      const dd = d.delta_s[i] - d.delta_s[i - 1];
      ctx.strokeStyle = dd > TH ? COLORS.loss : dd < -TH ? COLORS.gain : COLORS.baseline;
      ctx.beginPath();
      ctx.moveTo(px(i - 1), py(i - 1));
      ctx.lineTo(px(i), py(i));
      ctx.stroke();
    }
  }
  // 起點標記
  ctx.fillStyle = COLORS.ink2;
  ctx.beginPath();
  ctx.arc(px(0), py(0), 5 * devicePixelRatio, 0, Math.PI * 2);
  ctx.fill();
  ctx.font = `${11 * devicePixelRatio}px system-ui`;
  ctx.fillText("S/F", px(0) + 8 * devicePixelRatio, py(0) - 6 * devicePixelRatio);

  mapState.base = ctx.getImageData(0, 0, canvas.width, canvas.height);
}

function drawMapCursor(idx) {
  if (!mapState) return;
  const { px, py, n, canvas, base } = mapState;
  const ctx = canvas.getContext("2d");
  ctx.putImageData(base, 0, 0);
  if (idx == null || idx < 0 || idx >= n) return;
  ctx.fillStyle = COLORS.ink;
  ctx.strokeStyle = COLORS.a;
  ctx.lineWidth = 3 * devicePixelRatio;
  ctx.beginPath();
  ctx.arc(px(idx), py(idx), 6 * devicePixelRatio, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
}

/* ---------- 微分段 / 胎溫 / 圈速趨勢 ---------- */

function renderMicrosectors(ms) {
  const maxAbs = Math.max(0.02, ...ms.map((m) => Math.abs(m.delta_s)));
  $("microsectors").innerHTML = ms.map((m) => {
    const frac = Math.min(1, Math.abs(m.delta_s) / maxAbs);
    const color = m.delta_s > 0.005 ? COLORS.loss : m.delta_s < -0.005 ? COLORS.gain : null;
    const bg = color
      ? `${color}${Math.round(40 + frac * 175).toString(16).padStart(2, "0")}`
      : "";
    return `<div class="ms" ${bg ? `style="background:${bg}"` : ""}
      data-start="${m.start_pct}" data-end="${m.end_pct}"
      data-tip="${m.start_pct}–${m.end_pct}%  ${m.delta_s >= 0 ? "+" : ""}${m.delta_s.toFixed(3)}s（點擊放大）"></div>`;
  }).join("");
  for (const cell of document.querySelectorAll("#microsectors .ms")) {
    cell.onclick = () => zoomTo(Number(cell.dataset.start), Number(cell.dataset.end));
  }
}

const TYRE_ORDER = [["FL", 0], ["FR", 1], ["RL", 2], ["RR", 3]];

function tyreTempColor(t) {
  if (t == null) return COLORS.muted;
  if (t < 70) return COLORS.a;          // 過冷
  if (t <= 90) return COLORS.gain;      // 工作區間
  if (t <= 100) return "#c98500";       // 偏熱
  return COLORS.loss;                   // 過熱
}

function renderTyres(tyres, single = false) {
  const card = $("tyre-card");
  if (!tyres.a && !tyres.b) { card.style.display = "none"; return; }
  card.style.display = "";
  const colB = $("tyres-b").parentElement;
  colB.style.display = single ? "none" : "";
  for (const [id, ty] of [["tyres-a", tyres.a], ["tyres-b", tyres.b]]) {
    $(id).innerHTML = ty
      ? TYRE_ORDER.map(([w, i]) =>
          `<div class="tyre-cell"><div class="w">${w}</div>
           <div class="t" style="color:${tyreTempColor(ty.temp[i])}">${ty.temp[i].toFixed(0)}°</div>
           <div class="p">${ty.pressure[i].toFixed(1)} psi</div></div>`).join("")
      : '<div class="tyre-note">此圈無資料</div>';
  }
  $("tyre-note").textContent = "工作區間約 70–90°C（乾胎）";
}

function renderLapTrend(laps, bestId) {
  const done = laps.filter((l) => l.is_complete && l.lap_time_ms && !l.driver);
  if (done.length < 2) { $("lap-trend").innerHTML = ""; return; }
  const times = done.map((l) => l.lap_time_ms);
  const min = Math.min(...times), max = Math.max(...times);
  $("lap-trend").innerHTML = done.map((l) => {
    const h = max === min ? 100 : 30 + 70 * (l.lap_time_ms - min) / (max - min);
    const cls = l.lap_id === bestId ? "best" : l.is_valid ? "" : "invalid";
    return `<div class="bar ${cls}" style="height:${h.toFixed(0)}%"
      data-tip="Lap ${l.lap_number} · ${fmtLap(l.lap_time_ms)}"></div>`;
  }).join("");
}

let lastCompareError = "";

async function compare() {
  const a = currentLapA;
  const b = currentLapB;
  isSingle = !b;
  renderSlots();
  renderPalette();      // 更新卡片上的 in-a / in-b 標記
  // 先顯示 dashboard 再渲染：地圖 canvas 需要量得到實際尺寸
  $("empty-state").style.display = "none";
  $("dashboard").style.display = "";
  setSingleUI(isSingle);
  try {
    if (isSingle) await singleView(a);
    else await compareView(a, b);
  } catch (err) {
    lastCompareError = err.message;
    $("empty-state").style.display = "";
    $("empty-state").textContent = "載入失敗：" + err.message;
    $("dashboard").style.display = "none";
    return false;
  }
  await loadChat(a, isSingle ? 0 : b);
  return true;
}

function setSingleUI(single) {
  $("microsectors").closest(".chart-card").style.display = single ? "none" : "";
  $("chart-delta").closest(".chart-card").style.display = single ? "none" : "";
  $("speed-mode").style.display = single ? "none" : "";
  $("coach-subtitle").textContent =
    single ? "基於這一圈的遙測分析" : "基於目前比較的兩圈遙測";
}

async function compareView(a, b) {
  const d = await fetchJSON(`/api/compare?a=${a}&b=${b}`);
  currentZones = d.zones;

  $("tile-a-label").innerHTML = '<span class="dot dot-a"></span>參考圈 A';
  $("tile-b-label").innerHTML = '<span class="dot dot-b"></span>比較圈 B';
  $("tile-delta-label").textContent = "總差 (B−A)";
  $("tile-worst-label").textContent = "最大損失";
  $("tile-a").textContent = fmtLap(d.lap_a.lap_time_ms);
  $("tile-a").className = "tile-value";
  $("tile-b").textContent = fmtLap(d.lap_b.lap_time_ms);
  const td = $("tile-delta");
  td.textContent = (d.total_delta_s >= 0 ? "+" : "") + d.total_delta_s.toFixed(3) + "s";
  td.className = "tile-value " + (d.total_delta_s >= 0 ? "loss" : "gain");
  const worst = [...d.zones].sort((x, y) => y.time_lost_s - x.time_lost_s)[0];
  $("tile-worst").innerHTML = worst
    ? `${worst.corner ? worst.corner.split(" (")[0] : "#" + worst.index} ` +
      `<span class="sub">@ ${worst.start_pct}% · +${worst.time_lost_s.toFixed(2)}s</span>`
    : "–";

  lastData = d;
  buildCharts(d);
  renderMap(d, false);
  renderMicrosectors(d.microsectors);
  renderTyres({ a: d.tyres_a, b: d.tyres_b }, false);
  renderZones(d.zones, worst);
}

async function singleView(a) {
  const d = await fetchJSON(`/api/lap?id=${a}`);
  currentZones = d.zones;

  $("tile-a-label").innerHTML = '<span class="dot dot-a"></span>圈速';
  $("tile-b-label").textContent = "極速";
  $("tile-delta-label").textContent = "全圈最低速";
  $("tile-worst-label").textContent = "煞車區段";
  $("tile-a").textContent = fmtLap(d.lap.lap_time_ms);
  $("tile-a").className = "tile-value " + (d.lap.is_valid ? "" : "loss");
  $("tile-b").textContent = `${d.top_speed} km/h`;
  const td = $("tile-delta");
  td.textContent = `${d.min_speed} km/h`;
  td.className = "tile-value";
  $("tile-worst").innerHTML = `${d.zones.length} <span class="sub">個</span>`;

  lastData = null;
  buildSingleCharts(d);
  renderMap(d, true);
  renderTyres({ a: d.tyres, b: null }, true);
  renderZonesSingle(d.zones);
}

function buildCharts(d) {
  destroyCharts();
  const x = d.grid_pct;

  if (speedMode === "diff") {
    const sd = d.speed_a.map((v, i) =>
      v == null || d.speed_b[i] == null ? null : d.speed_b[i] - v);
    const sneg = sd.map((v) => (v != null && v < 0 ? v : null));  // B 較慢
    const spos = sd.map((v) => (v != null && v >= 0 ? v : null)); // B 較快
    makeChart("chart-speed", 260, [
      { label: "速度差 B−A", stroke: COLORS.ink2, width: 1.4,
        value: (u, v) => (v == null ? "-" : (v >= 0 ? "+" : "") + v.toFixed(1)),
        points: { show: false } },
      { label: "B 較慢", stroke: "transparent", fill: "rgba(230,103,103,0.22)",
        value: () => "", points: { show: false } },
      { label: "B 較快", stroke: "transparent", fill: "rgba(12,163,12,0.22)",
        value: () => "", points: { show: false } },
    ], [x, sd, sneg, spos]);
  } else {
    makeChart("chart-speed", 260,
      seriesPair(`A ${d.lap_a.label}`, `B ${d.lap_b.label}`),
      [x, d.speed_a, d.speed_b]);
  }

  makeChart("chart-pedal", 190, [
    ...seriesPair("油門 A", "油門 B"),
    ...seriesPair("煞車 A", "煞車 B", { dash: [5, 4] }),
  ], [x, d.throttle_a, d.throttle_b, d.brake_a, d.brake_b],
    { scales: { x: { time: false }, y: { range: [0, 105] } } });

  makeChart("chart-steering", 160,
    seriesPair("方向盤 A", "方向盤 B"),
    [x, d.steering_a, d.steering_b],
    { scales: { x: { time: false }, y: { range: [-1.05, 1.05] } } });

  const stepped = uPlot.paths && uPlot.paths.stepped
    ? uPlot.paths.stepped({ align: 1 }) : undefined;
  makeChart("chart-gear", 140, [
    { label: "檔位 A", stroke: COLORS.a, width: 1.2, paths: stepped,
      value: (u, v) => (v == null ? "-" : String(v)), points: { show: false } },
    { label: "檔位 B", stroke: COLORS.b, width: 1.2, paths: stepped,
      value: (u, v) => (v == null ? "-" : String(v)), points: { show: false } },
  ], [x, d.gear_a, d.gear_b]);

  const dpos = d.delta_s.map((v) => (v >= 0 ? v : null));
  const dneg = d.delta_s.map((v) => (v < 0 ? v : null));
  makeChart("chart-delta", 200, [
    { label: "Δt", stroke: COLORS.ink2, width: 1.4,
      value: (u, v) => (v == null ? "-" : (v >= 0 ? "+" : "") + v.toFixed(3) + "s"),
      points: { show: false } },
    { label: "落後", stroke: "transparent", fill: "rgba(230,103,103,0.18)",
      value: () => "", points: { show: false } },
    { label: "領先", stroke: "transparent", fill: "rgba(12,163,12,0.18)",
      value: () => "", points: { show: false } },
  ], [x, d.delta_s, dpos, dneg]);

  // 建立時 layout 可能尚未定，補一次尺寸校正
  requestAnimationFrame(() => {
    for (const c of charts) {
      const el = c.root.parentElement;
      if (el.clientWidth && Math.abs(el.clientWidth - c.width) > 2) {
        c.setSize({ width: el.clientWidth, height: c.height });
      }
    }
  });
}

function setupSpeedMode() {
  $("speed-mode").onclick = () => {
    speedMode = speedMode === "overlay" ? "diff" : "overlay";
    $("speed-mode").textContent = speedMode === "overlay" ? "顯示差值" : "顯示疊圖";
    if (lastData && !isSingle) buildCharts(lastData);
  };
}

function buildSingleCharts(d) {
  destroyCharts();
  const x = d.grid_pct;
  const v = (u, val) => (val == null ? "-" : val.toFixed(1));

  makeChart("chart-speed", 260, [
    { label: d.lap.label, stroke: COLORS.a, width: 1.2, value: v,
      points: { show: false } },
  ], [x, d.speed]);

  makeChart("chart-pedal", 190, [
    { label: "油門", stroke: COLORS.a, width: 1.2, value: v, points: { show: false } },
    { label: "煞車", stroke: COLORS.loss, width: 1.2, dash: [5, 4], value: v,
      points: { show: false } },
  ], [x, d.throttle, d.brake],
    { scales: { x: { time: false }, y: { range: [0, 105] } } });

  makeChart("chart-steering", 160, [
    { label: "方向盤", stroke: COLORS.a, width: 1.2, value: v, points: { show: false } },
  ], [x, d.steering],
    { scales: { x: { time: false }, y: { range: [-1.05, 1.05] } } });

  const stepped = uPlot.paths && uPlot.paths.stepped
    ? uPlot.paths.stepped({ align: 1 }) : undefined;
  makeChart("chart-gear", 140, [
    { label: "檔位", stroke: COLORS.a, width: 1.2, paths: stepped,
      value: (u, val) => (val == null ? "-" : String(val)), points: { show: false } },
  ], [x, d.gear]);

  requestAnimationFrame(() => {
    for (const c of charts) {
      const el = c.root.parentElement;
      if (el.clientWidth && Math.abs(el.clientWidth - c.width) > 2) {
        c.setSize({ width: el.clientWidth, height: c.height });
      }
    }
  });
}

function bindZoneRowClicks(tbody) {
  for (const row of tbody.querySelectorAll("tr")) {
    row.onclick = () => zoomTo(Number(row.dataset.start), Number(row.dataset.end));
  }
}

function renderZonesSingle(zones) {
  $("zones-title").innerHTML =
    '煞車區段 <span class="unit">單圈絕對數據，依賽道順序（點擊列可放大）</span>';
  $("zones-head").innerHTML =
    "<th>彎道</th><th>位置</th><th>煞車點</th><th>入彎速度</th>" +
    "<th>彎中最低速</th><th>出口速度</th>";
  const tbody = $("zones-table").querySelector("tbody");
  tbody.innerHTML = zones.map((z) => `<tr
      data-start="${z.start_pct}" data-end="${z.end_pct}" title="點擊放大此區段">
      <td>${z.corner || "#" + z.index}</td>
      <td>${z.start_pct}–${z.end_pct}%</td>
      <td>${z.brake_on_pct}%</td>
      <td>${z.entry_speed} km/h</td>
      <td>${z.min_speed} km/h</td>
      <td>${z.exit_speed} km/h</td>
    </tr>`).join("");
  bindZoneRowClicks(tbody);
}

function renderZones(zones, worst) {
  $("zones-title").innerHTML =
    '煞車區段分析 <span class="unit">依損失排序，含出彎後直線</span>';
  $("zones-head").innerHTML =
    "<th>彎道</th><th>位置</th><th>損失</th><th>進彎 / 出彎</th><th>煞車點差</th>" +
    "<th>彎中最低速 A / B</th><th>出口速度 A / B</th>";
  const tbody = $("zones-table").querySelector("tbody");
  tbody.innerHTML = [...zones]
    .sort((x, y) => y.time_lost_s - x.time_lost_s)
    .map((z) => {
      const brakeDiff = (z.brake_on_a_pct != null && z.brake_on_b_pct != null)
        ? z.brake_on_b_pct - z.brake_on_a_pct : null;
      const brakeTxt = brakeDiff == null || Math.abs(brakeDiff) < 0.05 ? "≈同"
        : `B ${brakeDiff < 0 ? "早" : "晚"} ${Math.abs(brakeDiff).toFixed(2)}%`;
      const lostCls = z.time_lost_s > 0.03 ? "loss" : z.time_lost_s < -0.03 ? "gain" : "";
      const ph = (v) => (v >= 0 ? "+" : "") + v.toFixed(2);
      return `<tr class="${z === worst ? "worst" : ""}"
        data-start="${z.start_pct}" data-end="${z.end_pct}" title="點擊放大此區段">
        <td>${z.corner || "#" + z.index}</td>
        <td>${z.start_pct}–${z.end_pct}%</td>
        <td class="${lostCls}">${z.time_lost_s >= 0 ? "+" : ""}${z.time_lost_s.toFixed(3)}s</td>
        <td>${ph(z.entry_loss_s)} / ${ph(z.exit_loss_s)}</td>
        <td>${brakeTxt}</td>
        <td>${z.min_speed_a} / ${z.min_speed_b} km/h</td>
        <td>${z.exit_speed_a} / ${z.exit_speed_b} km/h</td>
      </tr>`;
    }).join("");
  bindZoneRowClicks(tbody);
}

let resizeTimer = null;
window.addEventListener("resize", () => {
  resizeAllCharts();
  // 地圖是點陣 canvas，尺寸變了必須重繪，否則被瀏覽器拉伸變形
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (lastMapArgs) renderMap(lastMapArgs.d, lastMapArgs.single);
  }, 150);
});

// 依每張圖所在卡片是否放大，設回對應高度（放大 → 大高度；否則 → 原始高度）。
// 統一走這裡，避免用 c.height（放大後會殘留）。
function resizeAllCharts() {
  for (const c of charts) {
    const el = c.root.parentElement;
    const maxed = c.root.closest(".chart-card.card-max");
    // 讀 clientWidth 會強制同步 reflow，故此時已反映放大後的寬度
    c.setSize({ width: el.clientWidth,
                height: maxed ? maxCardHeight() : (c._baseHeight || 260) });
  }
}

// 系統亮／暗主題切換：重讀顏色並重建目前圖表
if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    COLORS = readColors();
    if (lastData) (isSingle ? buildSingleCharts : buildCharts)(lastData);
  });
}

/* ---------- 單張圖放大檢視 ---------- */

// 放大時可用的圖高：視窗高扣掉卡片邊距與標題
function maxCardHeight() { return Math.max(280, window.innerHeight * 0.94 - 90); }

let maxedCard = null;
let cardBackdrop = null;

function applyCardResize(card) {
  // 同步執行——不依賴 rAF（某些環境 rAF 會被節流／不觸發）
  resizeAllCharts();
  if (card.querySelector("#track-map") && lastMapArgs) {
    renderMap(lastMapArgs.d, lastMapArgs.single);
  }
}

function closeMaxedCard() {
  if (!maxedCard) return;
  const card = maxedCard;
  maxedCard = null;
  card.classList.remove("card-max");
  if (cardBackdrop) { cardBackdrop.remove(); cardBackdrop = null; }
  applyCardResize(card);
}

function openMaxedCard(card) {
  if (maxedCard === card) { closeMaxedCard(); return; }
  closeMaxedCard();                        // 一次只放大一張
  cardBackdrop = document.createElement("div");
  cardBackdrop.className = "card-backdrop";
  cardBackdrop.onclick = closeMaxedCard;
  document.body.appendChild(cardBackdrop);
  card.classList.add("card-max");
  maxedCard = card;
  applyCardResize(card);
}

/* ---------- 儀表板分頁 ---------- */

function setupDashTabs() {
  const nav = document.getElementById("dash-tabs");
  if (!nav) return;
  nav.addEventListener("click", (e) => {
    const btn = e.target.closest(".dash-tab");
    if (!btn) return;
    switchDashTab(btn.dataset.panel);
  });
}

function switchDashTab(name) {
  for (const b of document.querySelectorAll(".dash-tab"))
    b.classList.toggle("active", b.dataset.panel === name);
  for (const p of document.querySelectorAll(".tab-panel"))
    p.classList.toggle("active", p.dataset.panel === name);
  // 通道圖的 uPlot 是在面板隱藏（寬度 0）時建立的，切過來要重算尺寸
  if (name === "channels") resizeAllCharts();
}

// 為每張圖表卡片加上放大鈕（只加一次）
function setupCardMaximize() {
  const main = document.querySelector("#dashboard");
  if (!main) return;
  for (const card of main.querySelectorAll(".chart-card")) {
    const h2 = card.querySelector("h2");
    if (!h2 || h2.querySelector(".card-max-btn")) continue;
    const btn = document.createElement("button");
    btn.className = "card-max-btn";
    btn.title = "放大檢視";
    btn.textContent = "⤢";
    btn.onclick = () => openMaxedCard(card);
    h2.appendChild(btn);
  }
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeMaxedCard();
});

/* ---------- AI 教練 ---------- */

let coachHistory = [];   // [{role, content}]，切換比較圈時清空
let coachBusy = false;

function coachAddMsg(role, text, cls = "") {
  const div = document.createElement("div");
  div.className = `coach-msg ${cls || role}`;
  div.textContent = text;
  $("coach-messages").appendChild(div);
  $("coach-messages").scrollTop = $("coach-messages").scrollHeight;
  return div;
}

function coachGreeting() {
  coachAddMsg("assistant",
    isSingle
      ? "我看過這一圈的遙測了。單圈沒有參考圈可比，但煞車點與各彎速度都在——可以直接問我。"
      : "我看過這兩圈的遙測了。可以直接問我，或點下面的快速提問。");
}

async function loadChat(a, b) {
  coachCtx = { a, b: b || 0 };
  $("coach-messages").innerHTML = "";
  let messages = [];
  try {
    const h = await fetchJSON(`/api/coach/history?a=${a}&b=${b || 0}`);
    messages = h.messages || [];
  } catch (err) { /* 歷史載入失敗不影響使用 */ }
  coachHistory = messages;
  if (messages.length) {
    for (const m of messages) coachAddMsg(m.role, m.content);
  } else {
    coachGreeting();
  }
}

async function coachSend(text) {
  text = text.trim();
  if (!text || coachBusy) return;
  coachBusy = true;
  $("coach-send").disabled = true;
  coachAddMsg("user", text);
  coachHistory.push({ role: "user", content: text });
  const thinking = coachAddMsg("assistant", "教練分析中…", "assistant thinking");
  try {
    const res = await fetch("/api/coach", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ a: coachCtx.a, b: coachCtx.b || null,
                             messages: coachHistory }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      thinking.remove();
      coachAddMsg("assistant", data.error || "發生錯誤", "error");
      coachHistory.pop();       // 失敗的問題不留在歷史裡
      return;
    }
    // 串流：邊收邊把文字填進泡泡（1-2 秒就開始出字，不用乾等）
    thinking.remove();
    const bubble = coachAddMsg("assistant", "", "assistant");
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let reply = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      reply += decoder.decode(value, { stream: true });
      bubble.textContent = reply;
      $("coach-messages").scrollTop = $("coach-messages").scrollHeight;
    }
    coachHistory.push({ role: "assistant", content: reply });
  } catch (err) {
    thinking.remove();
    coachAddMsg("assistant", "連線失敗：" + err.message, "error");
    coachHistory.pop();
  } finally {
    coachBusy = false;
    $("coach-send").disabled = false;
  }
}

function setupCoach() {
  $("coach-send").onclick = () => {
    const input = $("coach-input");
    coachSend(input.value);
    input.value = "";
  };
  $("coach-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) {
      coachSend(e.target.value);
      e.target.value = "";
    }
  });
  for (const btn of document.querySelectorAll("#coach-quick .quick-q")) {
    btn.onclick = () => coachSend(btn.textContent);
  }
  $("coach-clear").onclick = async () => {
    if (coachBusy) return;
    try {
      await fetchJSON(`/api/coach/history?a=${coachCtx.a}&b=${coachCtx.b || 0}`,
                      { method: "DELETE" });
    } catch (err) { /* 沒有歷史也沒關係 */ }
    coachHistory = [];
    $("coach-messages").innerHTML = "";
    coachGreeting();
  };
}

/* ---------- 設定 ---------- */

async function loadSettings() {
  const s = await fetchJSON("/api/settings");
  $("setting-model").value = s.coach_model;
  const status = $("setting-key-status");
  if (s.api_key_set) {
    status.textContent = `已設定（${s.api_key_hint}）— 輸入新值可覆蓋`;
  } else if (s.env_key_set) {
    status.textContent = "使用環境變數 ANTHROPIC_API_KEY 中的金鑰";
  } else {
    status.textContent = "尚未設定。到 console.anthropic.com 取得 API 金鑰。";
  }
}

/* ---------- 555 訓練 UI ---------- */

const STAGES = [
  ["baseline", "基準"], ["beat", "超越"],
  ["set_target", "目標"], ["achieve", "達標"], ["done", "完成"],
];

function parseTimeToMs(str) {
  str = String(str).trim();
  let m = str.match(/^(\d+):(\d+(?:\.\d+)?)$/);   // M:SS.mmm
  if (m) return Math.round((Number(m[1]) * 60 + Number(m[2])) * 1000);
  const sec = Number(str);                         // 純秒數
  return Number.isFinite(sec) ? Math.round(sec * 1000) : null;
}

function dots5(filled) {
  let h = "";
  for (let i = 0; i < 5; i++) {
    h += `<div class="dot5 ${i < filled ? "filled" : ""}">${i < filled ? "✓" : i + 1}</div>`;
  }
  return h;
}

function renderTrainPanel(st) {
  const panel = $("train-panel");
  const startBtn = $("train-start");
  const active = (st.phase === "waiting" || st.phase === "recording");
  const t = st.training;

  if (!active || st.mode !== "train" || !t) {
    // 非訓練狀態：顯示提示 + 「開始」；若剛完成則保留分數在訓練紀錄區
    startBtn.disabled = active && st.mode !== "train";  // 一般錄製中不能開訓練
    if (active && st.mode !== "train") {
      startBtn.textContent = "開始";
      panel.innerHTML = '<div class="train-hint">錄製進行中，停止後才能開始訓練。</div>';
      return;
    }
    if (pausedTraining && pausedTraining.state) {
      // 有暫停中的訓練 → 主鍵改「繼續」，另附「放棄重來」
      const ps = pausedTraining.state;
      const when = (pausedTraining.updated_at || "").replace("T", " ");
      startBtn.textContent = "繼續訓練";
      panel.innerHTML =
        `<div class="train-resume">
           <div class="tr-badge">暫停中</div>
           <div class="tr-info"><b>${ps.stage_label}</b> · ${ps.requirement}</div>
           <div class="train-hint">${pausedTraining.track || "?"} · 上次 ${when}</div>
           <button id="train-discard" class="mini-btn danger">放棄，重新開始</button>
         </div>`;
      $("train-discard").onclick = async () => {
        await fetchJSON("/api/train/discard", { method: "POST" }).catch(() => {});
        pausedTraining = null;
        renderTrainPanel(st);
      };
      return;
    }
    startBtn.textContent = "開始";
    panel.innerHTML =
      '<div class="train-hint">連續 5 圈零失誤 → 5 圈超越均速 → 設目標 → 達標 5 圈</div>';
    return;
  }

  startBtn.textContent = "停止";
  startBtn.disabled = false;

  // 階段指示
  let pips = STAGES.map(([key, label]) => {
    const idx = STAGES.findIndex((s) => s[0] === t.stage);
    const myIdx = STAGES.findIndex((s) => s[0] === key);
    const cls = key === t.stage ? "active" : myIdx < idx ? "done" : "";
    return `<div class="stage-pip ${cls}">${label}</div>`;
  }).join("");

  let body = `<div class="stage-row">${pips}</div>
    <div class="train-req">${t.requirement}</div>`;

  if (t.stage === "baseline") {
    body += `<div class="train-progress">${dots5(t.baseline_progress)}</div>`;
  } else if (t.stage === "beat") {
    body += `<div class="train-progress">${dots5(t.beat_progress)}</div>`;
  } else if (t.stage === "set_target") {
    const sugSec = (t.suggested_target / 1000).toFixed(3);
    body += `<div class="train-target-row">
      <input id="train-target-input" value="${sugSec}" placeholder="秒數，如 88.5 或 1:28.5">
      <button id="train-target-set" class="mini-btn">設定</button></div>
      <div class="train-hint">建議 ${fmtLap(t.suggested_target)}（超越均速快 0.5 秒）</div>`;
  } else if (t.stage === "achieve") {
    body += `<div class="train-progress">${dots5(t.achieve_progress)}</div>
      <div class="train-hint">目標 ${fmtLap(t.target_ms)} · 已嘗試 ${t.achieve_attempts} 圈</div>`;
  } else if (t.stage === "done" && t.score) {
    const s = t.score;
    body += `<div class="train-score">
      <div class="sc-item sc-total"><div class="sc-num">${s.total}</div><div class="sc-lbl">總分</div></div>
      <div class="sc-item"><div class="sc-num">${s.consistency}</div><div class="sc-lbl">一致性</div></div>
      <div class="sc-item"><div class="sc-num">${s.improvement}</div><div class="sc-lbl">進步</div></div>
      <div class="sc-item"><div class="sc-num">${s.ambition}</div><div class="sc-lbl">企圖心</div></div>
      <div class="sc-item"><div class="sc-num">${s.efficiency}</div><div class="sc-lbl">效率</div></div>
    </div>`;
  }

  // 最近幾圈
  if (t.recent && t.recent.length) {
    body += '<div class="train-recent">' + t.recent.map((r) => {
      const cls = r.good ? "lap-ok" : "lap-bad";
      const time = r.time_ms ? fmtLap(r.time_ms) : "無效";
      return `<span class="${cls}">${time}</span>`;
    }).join(" · ") + "</div>";
  }

  panel.innerHTML = body;

  if (t.stage === "set_target") {
    $("train-target-set").onclick = async () => {
      const ms = parseTimeToMs($("train-target-input").value);
      if (!ms) return;
      await fetchJSON("/api/train/target", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ms }),
      }).catch(() => {});
    };
  }
}

/* ---------- 555 專注畫面 ---------- */

let inFocus = false;

// 當前圈時間平滑跳動：每次輪詢拿到 current_lap_ms 當基準，
// rAF 用 performance.now() 內插到 ~60fps；連兩次輪詢數值沒變 = 遊戲暫停，凍結。
let tickBaseMs = 0, tickBaseAt = 0, tickRunning = false, tickLastPollMs = null;

function fmtLapMsRaw(ms) {
  ms = Math.max(0, Math.floor(ms));
  const m = Math.floor(ms / 60000), s = Math.floor((ms % 60000) / 1000), mi = ms % 1000;
  return `${m}:${String(s).padStart(2, "0")}.${String(mi).padStart(3, "0")}`;
}

function tickLoop() {
  if (!tickRunning) return;
  const el = $("focus-cur-time");
  if (el && !$("focus-view").hidden) {
    el.textContent = fmtLapMsRaw(tickBaseMs + (performance.now() - tickBaseAt));
  }
  requestAnimationFrame(tickLoop);
}

function syncTicker(ms) {
  if (ms == null) { stopTicker(); return; }
  const stalled = tickLastPollMs !== null && ms === tickLastPollMs;
  tickLastPollMs = ms;
  tickBaseMs = ms;
  tickBaseAt = performance.now();
  if (stalled) {                       // 暫停/停在 pit → 凍結顯示，不繼續跑
    stopTicker();
    $("focus-cur-time").textContent = fmtLapMsRaw(ms);
  } else if (!tickRunning) {
    tickRunning = true;
    requestAnimationFrame(tickLoop);
  }
}

function stopTicker() { tickRunning = false; }

function showFocus() {
  inFocus = true;
  $("home-view").hidden = true;
  $("dashboard-view").hidden = true;
  $("focus-view").hidden = false;
}

function exitFocus() {
  inFocus = false;
  stopTicker();
  tickLastPollMs = null;
  $("focus-view").hidden = true;
  $("focus-target").removeAttribute("data-shown");
}

function renderFocus(st) {
  const t = st.training;
  $("focus-stage").textContent = "555 訓練" + (t ? " · " + t.stage_label : "");
  $("focus-req").textContent = t ? t.requirement : "等待進入賽道…";
  // 平滑跳動：有原始毫秒就交給 ticker 內插，否則退回輪詢的格式化字串
  if (st.phase === "recording" && st.current_lap_ms != null) {
    syncTicker(st.current_lap_ms);
  } else {
    stopTicker();
    $("focus-cur-time").textContent = st.current_time || "--:--.---";
  }
  $("focus-lapcount").textContent = `已完成 ${st.laps_saved || 0} 圈`;

  // 5 圈進度點（僅在有連續要求的階段）
  let prog = null;
  if (t) {
    if (t.stage === "baseline") prog = t.baseline_progress;
    else if (t.stage === "beat") prog = t.beat_progress;
    else if (t.stage === "achieve") prog = t.achieve_progress;
  }
  $("focus-progress").innerHTML = prog === null ? "" : dots5(prog);

  // 設定目標輸入（用 data-shown 避免每秒重建把輸入清掉）
  const tgt = $("focus-target");
  if (t && t.stage === "set_target") {
    if (!tgt.dataset.shown) {
      tgt.innerHTML =
        `<input id="focus-target-input" value="${(t.suggested_target / 1000).toFixed(3)}">` +
        `<button id="focus-target-set" class="mini-btn">設定目標</button>`;
      tgt.dataset.shown = "1";
      $("focus-target-set").onclick = async () => {
        const ms = parseTimeToMs($("focus-target-input").value);
        if (ms) {
          await fetch("/api/train/target", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ms }),
          }).catch(() => {});
        }
      };
    }
    tgt.hidden = false;
  } else {
    tgt.hidden = true;
    tgt.removeAttribute("data-shown");
  }

  // 完成得分
  const sc = $("focus-score");
  if (t && t.stage === "done" && t.score) {
    sc.hidden = false;
    sc.innerHTML =
      `<div class="fs-total">${t.score.total}</div>` +
      `<div class="fs-sub">一致性 ${t.score.consistency} · 進步 ${t.score.improvement}` +
      ` · 企圖心 ${t.score.ambition} · 效率 ${t.score.efficiency}</div>`;
    $("focus-stop").textContent = "完成 · 返回首頁";
  } else {
    sc.hidden = true;
    $("focus-stop").textContent = "停止";
  }

  // 圈速清單（新的在下）
  if (t && t.recent) {
    $("focus-laps").innerHTML = t.recent.map((r) => {
      const bad = !r.good;
      return `<div class="focus-lap-row ${bad ? "bad" : ""}">
        <span>Lap ${r.n}</span>
        <span class="fl-time">${r.time_ms ? fmtLap(r.time_ms) : "無效"}</span>
        <span class="fl-tag">${bad ? "✗ 失誤" : "✓"}</span></div>`;
    }).join("");
  }
}

async function stopTraining() {
  await fetchJSON("/api/record/stop", { method: "POST" }).catch(() => {});
  if (recordPoll) clearTimeout(recordPoll);
  recordPoll = null;
  exitFocus();
  goHome();
}

function setupTraining() {
  $("train-start").onclick = async () => {
    const st = await fetchJSON("/api/record/status");
    const active = st.phase === "waiting" || st.phase === "recording";
    if (active && st.mode === "train") {
      await stopTraining();
    } else if (!active) {
      const resume = !!pausedTraining;   // 有暫停進度 → 續傳
      await fetchJSON("/api/record/start", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "train", resume }),
      });
      lastLapsSaved = 0;
      showFocus();          // 立刻切到專注畫面
      pollRecordStatus();
    }
  };
  $("focus-stop").onclick = stopTraining;
}

/* ---------- 設定 modal ---------- */

function openSettings() {
  $("settings-overlay").hidden = false;
  $("setting-result").textContent = "";
  loadSettings().catch(() => {});
}

function setupSettings() {
  $("settings-btn").onclick = openSettings;
  $("dash-settings-btn").onclick = openSettings;
  $("settings-close").onclick = () => { $("settings-overlay").hidden = true; };
  $("settings-overlay").onclick = (e) => {
    if (e.target === $("settings-overlay")) $("settings-overlay").hidden = true;
  };
  $("setting-save").onclick = async () => {
    const result = $("setting-result");
    const body = { coach_model: $("setting-model").value };
    const key = $("setting-api-key").value.trim();
    if (key) body.api_key = key;           // 留空 = 不動既有金鑰
    try {
      await fetchJSON("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      $("setting-api-key").value = "";
      result.className = "setting-status ok";
      result.textContent = "已儲存";
      await loadSettings().catch(() => {});
    } catch (err) {
      result.className = "setting-status err";
      result.textContent = "儲存失敗：" + err.message;
    }
  };
  $("setting-test").onclick = async () => {
    const result = $("setting-result");
    result.className = "setting-status";
    result.textContent = "測試中…";
    try {
      const r = await fetchJSON("/api/settings/test", { method: "POST" });
      result.className = "setting-status " + (r.ok ? "ok" : "err");
      result.textContent = r.message;
    } catch (err) {
      result.className = "setting-status err";
      result.textContent = "測試失敗：" + err.message;
    }
  };
}

setupNav();
setupDragDrop();
setupSessionActions();
setupRecording();
setupTraining();
setupSpeedMode();
setupCoach();
setupSettings();
setupCardMaximize();
setupDashTabs();
init().catch((err) => {
  $("session-cards").innerHTML =
    '<div class="pb-empty">初始化失敗：' + err.message + "</div>";
});
