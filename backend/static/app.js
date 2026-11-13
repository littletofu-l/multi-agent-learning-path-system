const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const AGENTS = [
  { key: "intent", name: "Intent Agent", role: "识别学习目标与当前基础" },
  { key: "knowledge", name: "Knowledge Agent", role: "检索领域知识体系" },
  { key: "path", name: "Path Agent", role: "规划阶段化学习路径" },
  { key: "visual", name: "Visual Agent", role: "生成可视化结构数据" },
  { key: "review", name: "Review Agent", role: "检查完整性并给出修订建议" },
];

const STAGE = {
  beginner: { name: "入门", color: "#4cc9f0" },
  basic: { name: "基础", color: "#7b61ff" },
  advanced: { name: "进阶", color: "#f7a531" },
  practice: { name: "实战", color: "#3ddc97" },
  buffer: { name: "缓冲", color: "#56617a" },
};

const state = { data: null, charts: {}, rendered: false, progressKey: "" };
const LEVEL_STAGE = { 1: "beginner", 2: "basic", 3: "advanced", 4: "practice" };
let edit = null;
let queue = [];
let finalData = null;
let pumping = false;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 4200);
}

function renderAgents() {
  $("#agents").innerHTML = AGENTS.map(
    (a, i) => `
    <div class="agent" data-key="${a.key}">
      <span class="agent-index">0${i + 1}</span>
      <div class="agent-name">${a.name}</div>
      <div class="agent-role">${a.role}</div>
      <div class="agent-status"><i></i><span>waiting</span></div>
      <div class="agent-detail"></div>
    </div>`
  ).join("");
}

function resetAgents() {
  $$(".agent").forEach((card) => {
    card.className = "agent";
    $(".agent-status span", card).textContent = "waiting";
    $(".agent-detail", card).textContent = "";
  });
}

function setAgent(key, status, detail = "", elapsed = 0) {
  const card = $(`.agent[data-key="${key}"]`);
  if (!card) return;
  card.className = `agent ${status}`;
  $(".agent-status span", card).textContent =
    status === "running" ? "running" : status === "done" ? (elapsed ? `${elapsed} ms` : "done") : status;
  if (detail) $(".agent-detail", card).textContent = detail;
}

async function fetchHealth() {
  const badge = $("#llmBadge");
  try {
    const res = await fetch("/api/health");
    const health = await res.json();
    const online = health.llm_enabled;
    badge.className = `badge ${online ? "online" : "offline"}`;
    badge.title = online
      ? `已连接 ${health.llm_base_url} · ${health.llm_model}`
      : "未检测到可用 LLM，当前使用内置知识库 + 规则引擎，功能完整可跑通";
    $("span", badge).textContent = health.llm_enabled
      ? `LLM · ${health.llm_model}`
      : health.llm_mode.replace(/（.*$/, "").replace("规则引擎", "规则引擎模式");
  } catch (err) {
    badge.className = "badge offline";
    $("span", badge).textContent = "后端未连接";
  }
}

function setPipelineStatus(text) {
  $("#pipelineStatus").textContent = text;
}

function applyEvent(evt) {
  if (evt.type === "agent") {
    setAgent(evt.key, evt.status, evt.detail || "", evt.elapsed_ms || 0);
  } else if (evt.type === "error") {
    setPipelineStatus("生成失败");
    toast(evt.message || "生成失败");
  } else if (evt.type === "result") {
    finalData = evt.data;
  }
}

async function pump() {
  if (pumping) return;
  pumping = true;
  while (queue.length) {
    applyEvent(queue.shift());
    await sleep(300);
  }
  pumping = false;
  if (finalData && queue.length === 0 && !state.rendered) {
    state.rendered = true;
    renderResult(finalData);
  }
}

function enqueue(evt) {
  queue.push(evt);
  pump();
}

let generateAbort = null;

async function generate() {
  const goal = $("#goalInput").value.trim();
  if (goal.length < 2) {
    toast("请先输入一个学习目标（至少 2 个字）");
    return;
  }
  const payload = {
    goal,
    level: $("#levelInput").value,
    weeks: Number($("#weeksInput").value) || 12,
    hours_per_week: Number($("#hoursInput").value) || 10,
  };

  if (generateAbort) generateAbort.abort(); // 中止上一次未完成的流，避免事件交错
  if (edit) closeEditor(); // 编辑器开着时重新生成：丢弃旧编辑状态
  const controller = new AbortController();
  generateAbort = controller;

  const btn = $("#submitBtn");
  btn.disabled = true;
  $("span", btn).textContent = "多 Agent 协作中…";
  queue = [];
  finalData = null;
  state.rendered = false;
  resetAgents();
  $("#results").hidden = true;
  setPipelineStatus("运行中…");

  try {
    const res = await fetch("/api/generate/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx = buffer.indexOf("\n\n");
      while (idx >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data:"));
        if (line) {
          try {
            enqueue(JSON.parse(line.slice(5).trim()));
          } catch (err) {
            console.warn("bad event", chunk);
          }
        }
        idx = buffer.indexOf("\n\n");
      }
    }
  } catch (err) {
    if (err.name === "AbortError") return; // 被新一轮生成取代，静默退出
    setPipelineStatus("生成失败");
    toast(`请求失败：${err.message}`);
  } finally {
    if (generateAbort === controller) {
      generateAbort = null;
      btn.disabled = false;
      $("span", btn).textContent = "生成学习路径";
    }
  }
}

function renderResult(data, opts = {}) {
  const activeTab = document.querySelector("#tabs button.active")?.dataset.tab || "graph";
  state.data = data;
  const intent = data.intent;
  $("#resultDomain").textContent = `${intent.domain_label} · ${intent.level}`;
  $("#resultTopic").textContent = intent.topic;
  $("#resultTarget").textContent = `${intent.target}　${intent.profile}`;

  const nodes = data.path.nodes;
  const stats = [
    { value: nodes.length, unit: "个", label: "知识点" },
    { value: data.path.total_hours, unit: "h", label: "总学时" },
    { value: data.path.total_weeks, unit: "周", label: "规划周期" },
    { value: data.visual.links.length, unit: "条", label: "依赖关系" },
    { value: data.review.score, unit: "分", label: "路径质量" },
  ];
  $("#statRow").innerHTML = stats
    .map(
      (s) => `<div class="stat"><div class="value">${esc(s.value)}<small>${s.unit}</small></div>
      <div class="label">${s.label}</div></div>`
    )
    .join("");

  state.progressKey = `lp:${intent.domain}:${intent.topic}`;
  renderRoadmap(data);
  renderWeekCards(data);
  renderChecklist(data);
  renderReview(data);

  $("#results").hidden = false;
  switchTab(opts.keepTab ? activeTab : "graph");

  const elapsed = (data.elapsed_ms / 1000).toFixed(1);
  setPipelineStatus(`完成 · 用时 ${elapsed}s · ${data.llm_mode}`);
  const badge = $("#llmBadge");
  badge.className = `badge ${data.llm_mode.startsWith("LLM") ? "online" : "offline"}`;
  $("span", badge).textContent = data.llm_mode;

  if (!opts.keepScroll) {
    setTimeout(() => {
      $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
    }, 120);
  }
}

function renderGraph(data) {
  const host = $("#graphChart");
  if (typeof echarts === "undefined") {
    host.innerHTML = '<p class="muted">ECharts 资源未加载，无法渲染图谱。</p>';
    return;
  }
  const stages = ["beginner", "basic", "advanced", "practice"];
  const groups = { beginner: [], basic: [], advanced: [], practice: [] };
  data.visual.nodes.forEach((n) => (groups[n.stage] || groups.beginner).push(n));
  stages.forEach((key) => groups[key].sort((a, b) => a.week - b.week || a.level - b.level));

  const maxColumn = Math.max(...stages.map((s) => groups[s].length), 1);
  host.style.height = `${Math.max(560, maxColumn * 100 + 150)}px`;
  const width = host.clientWidth || 860;
  const colGap = Math.max(200, (width - 170) / 3);
  const positions = new Map();
  stages.forEach((key, ci) => {
    groups[key].forEach((node, ri) => {
      positions.set(node.id, { x: 85 + ci * colGap, y: 86 + ri * 100 });
    });
  });

  const chart = state.charts.graph || echarts.init(host);
  state.charts.graph = chart;
  const nameOf = new Map(data.visual.nodes.map((n) => [n.id, n.name]));

  chart.setOption(
    {
      backgroundColor: "transparent",
      animationDuration: 700,
      tooltip: {
        backgroundColor: "rgba(10,15,26,0.94)",
        borderColor: "rgba(255,255,255,0.14)",
        textStyle: { color: "#e9eefc", fontSize: 12 },
        formatter: (p) => {
          if (p.dataType === "edge") return `${esc(nameOf.get(p.data.source))} → ${esc(nameOf.get(p.data.target))}`;
          const n = p.data.raw;
          if (!n) return "";
          return [
            `<strong>${esc(n.name)}</strong>`,
            `<span style="color:${STAGE[n.stage]?.color || "#8b97b3"}">${esc(n.stage_name)} · 第 ${n.week} 周 · ${n.hours}h</span>`,
            n.summary ? `<div style="max-width:260px;color:#b9c4dc;margin-top:4px">${esc(n.summary)}</div>` : "",
          ].join("<br/>");
        },
      },
      legend: { show: false },
      graphic: stages.map((key, ci) => ({
        type: "text",
        left: 85 + ci * colGap,
        top: 8,
        style: {
          text: `${STAGE[key].name} · ${groups[key].length}`,
          fill: STAGE[key].color,
          opacity: 0.85,
          fontSize: 11,
          fontFamily: "monospace",
          textAlign: "center",
        },
        z: 10,
        silent: true,
      })),
      series: [
        {
          type: "graph",
          layout: "none",
          roam: true,
          draggable: true,
          symbol: "circle",
          edgeSymbol: ["none", "arrow"],
          edgeSymbolSize: 7,
          categories: stages.map((s) => ({ name: STAGE[s].name, itemStyle: { color: STAGE[s].color } })),
          label: {
            show: true,
            position: "bottom",
            distance: 7,
            color: "#c8d2e8",
            fontSize: 10,
            lineHeight: 12,
            width: 88,
            overflow: "break",
            formatter: (p) => (p.data.raw.name.length > 12 ? `${p.data.raw.name.slice(0, 11)}…` : p.data.raw.name),
          },
          lineStyle: { color: "rgba(140,160,200,0.32)", width: 1.2, curveness: 0.08 },
          emphasis: { focus: "adjacency", lineStyle: { color: "rgba(76,201,240,0.85)", width: 2 } },
          data: data.visual.nodes.map((n) => {
            const pos = positions.get(n.id) || { x: 0, y: 0 };
            return {
              id: n.id,
              name: n.name,
              x: pos.x,
              y: pos.y,
              category: stages.indexOf(n.stage) >= 0 ? stages.indexOf(n.stage) : 0,
              symbolSize: 18 + Math.min(20, n.hours * 1.0),
              itemStyle: {
                color: STAGE[n.stage]?.color || "#8b97b3",
                borderColor: "rgba(7,11,18,0.9)",
                borderWidth: 2,
                shadowBlur: 14,
                shadowColor: `${STAGE[n.stage]?.color || "#8b97b3"}66`,
              },
              raw: n,
            };
          }),
          links: data.visual.links.map((l) => ({ ...l })),
        },
      ],
    },
    true
  );

  chart.off("click");
  chart.on("click", (params) => {
    if (params.dataType === "node" && params.data.raw) {
      showNodeDetail(params.data.raw);
      highlightInMainGraph(params.data.raw.id);
    }
  });
}

/** 以某节点为中心的局部依赖子图：上游（前置链）+ 下游（解锁链）。 */
function relatedSubgraph(nodeId) {
  const links = state.data?.visual.links || [];
  const nodesById = new Map((state.data?.visual.nodes || []).map((n) => [n.id, n]));
  const prereqOf = new Map(); // target -> sources[]
  const unlockOf = new Map(); // source -> targets[]
  for (const l of links) {
    if (!prereqOf.has(l.target)) prereqOf.set(l.target, []);
    prereqOf.get(l.target).push(l.source);
    if (!unlockOf.has(l.source)) unlockOf.set(l.source, []);
    unlockOf.get(l.source).push(l.target);
  }
  const bfs = (start, adj) => {
    const dist = new Map();
    let frontier = [start];
    let d = 0;
    while (frontier.length) {
      d += 1;
      const next = [];
      for (const id of frontier) {
        for (const nb of adj.get(id) || []) {
          if (nb !== start && !dist.has(nb)) {
            dist.set(nb, d);
            next.push(nb);
          }
        }
      }
      frontier = next;
    }
    return dist;
  };
  const up = bfs(nodeId, prereqOf);
  const down = bfs(nodeId, unlockOf);
  const ids = new Set([nodeId, ...up.keys(), ...down.keys()]);
  const subLinks = links.filter((l) => ids.has(l.source) && ids.has(l.target));
  return { up, down, subLinks, nodesById, unlockOf };
}

function showNodeDetail(node) {
  const sub = relatedSubgraph(node.id);
  const { up, down, nodesById } = sub;
  const deps = node.depends_on || [];
  const unlocks = (sub.unlockOf.get(node.id) || [])
    .map((id) => nodesById.get(id))
    .filter(Boolean);
  const maxUp = Math.max(0, ...up.values());
  const maxDown = Math.max(0, ...down.values());
  const chainText =
    up.size || down.size
      ? `前置链 ${up.size} 点（最深 ${maxUp} 层） → 当前 → 解锁链 ${down.size} 点（最深 ${maxDown} 层）`
      : "该知识点与其他点暂无依赖连接，是独立节点。";

  $("#nodeDetail").innerHTML = `
    <p class="eyebrow">NODE INSIGHT</p>
    <h4>${esc(node.name)}</h4>
    <div class="tag-row">
      <span class="tag stage-${node.stage}">${esc(node.stage_name)}阶段</span>
      <span class="tag">${esc(node.category)}</span>
      <span class="tag">L${node.level}</span>
    </div>
    <dl>
      <dt>起始周</dt><dd>第 ${node.week} 周（约 ${node.hours} 学时）</dd>
      ${node.summary ? `<dt>学习要点</dt><dd>${esc(node.summary)}</dd>` : ""}
      <dt>直接前置</dt><dd>${deps.length ? deps.map((d) => esc(nodesById.get(d)?.name || d)).join("、") : "无，可直接开始"}</dd>
      <dt>直接解锁</dt><dd>${unlocks.length ? unlocks.map((n) => esc(n.name)).join("、") : "暂无后续知识点依赖它"}</dd>
      ${node.resource ? `<dt>推荐资源</dt><dd>${esc(node.resource)}</dd>` : ""}
    </dl>
    <div class="topo-head">
      <strong>相关知识点拓扑</strong>
      <span class="muted">点击图中节点切换</span>
    </div>
    <div id="nodeTopology" class="node-topo" aria-label="局部依赖拓扑图"></div>
    <p class="hint">${esc(chainText)}</p>`;

  renderNodeTopology(node, sub);
}

function renderNodeTopology(center, { up, down, subLinks, nodesById }) {
  const host = $("#nodeTopology");
  if (!host) return;
  if (typeof echarts === "undefined") {
    host.innerHTML = '<p class="muted">ECharts 未加载，无法渲染拓扑。</p>';
    return;
  }
  state.charts.node?.dispose();
  state.charts.node = null;

  // 列布局：最深层前置 … 直接前置 | 当前 | 直接后续 … 最深层后续
  const columns = [];
  const byDist = (distMap) => {
    const buckets = new Map();
    for (const [id, d] of distMap) {
      if (!buckets.has(d)) buckets.set(d, []);
      buckets.get(d).push(id);
    }
    return buckets;
  };
  const upBuckets = byDist(up);
  const downBuckets = byDist(down);
  for (let d = Math.max(0, ...up.values()); d >= 1; d -= 1) {
    columns.push({ key: `up-${d}`, title: `前置 · ${d} 层外`, ids: upBuckets.get(d) || [] });
  }
  columns.push({ key: "center", title: "当前", ids: [center.id] });
  for (let d = 1; d <= Math.max(0, ...down.values()); d += 1) {
    columns.push({ key: `down-${d}`, title: `解锁 · ${d} 层外`, ids: downBuckets.get(d) || [] });
  }
  columns.forEach((col) =>
    col.ids.sort((a, b) => {
      const na = nodesById.get(a);
      const nb = nodesById.get(b);
      return (na?.week || 0) - (nb?.week || 0) || (na?.level || 0) - (nb?.level || 0);
    })
  );

  const maxRows = Math.max(1, ...columns.map((c) => c.ids.length));
  const rowH = 56;
  const topPad = 34;
  host.style.height = `${Math.max(230, maxRows * rowH + topPad + 40)}px`;
  const width = host.clientWidth || 320;
  const colW = Math.max(108, (width - 20) / columns.length);
  const midY = (maxRows * rowH) / 2 + topPad;

  const positions = new Map();
  columns.forEach((col, ci) => {
    col.ids.forEach((id, ri) => {
      positions.set(id, {
        x: 10 + colW / 2 + ci * colW,
        y: midY + (ri - (col.ids.length - 1) / 2) * rowH,
      });
    });
  });

  const chart = echarts.init(host);
  state.charts.node = chart;
  chart.setOption({
    backgroundColor: "transparent",
    animationDuration: 500,
    tooltip: {
      backgroundColor: "rgba(10,15,26,0.94)",
      borderColor: "rgba(255,255,255,0.14)",
      textStyle: { color: "#e9eefc", fontSize: 12 },
      formatter: (p) => {
        if (p.dataType === "edge") {
          return `${esc(nodesById.get(p.data.source)?.name || p.data.source)} → ${esc(nodesById.get(p.data.target)?.name || p.data.target)}`;
        }
        const n = p.data.raw;
        return n
          ? `<strong>${esc(n.name)}</strong><br/><span style="color:${STAGE[n.stage]?.color || "#8b97b3"}">${esc(n.stage_name)} · 第 ${n.week} 周 · ${n.hours}h</span>`
          : "";
      },
    },
    graphic: columns.map((col, ci) => ({
      type: "text",
      left: 10 + ci * colW,
      top: 8,
      style: {
        text: col.title,
        fill: col.key === "center" ? STAGE[center.stage]?.color || "#fff" : "#56617a",
        opacity: col.key === "center" ? 1 : 0.8,
        fontSize: 10,
        fontFamily: "monospace",
        textAlign: "center",
      },
      z: 10,
      silent: true,
      // 居中偏移
      dx: colW / 2,
    })),
    series: [
      {
        type: "graph",
        layout: "none",
        roam: true,
        edgeSymbol: ["none", "arrow"],
        edgeSymbolSize: 6,
        data: [...positions.keys()].map((id) => {
          const n = nodesById.get(id);
          const isCenter = id === center.id;
          const pos = positions.get(id);
          return {
            id,
            name: n?.name || id,
            x: pos.x,
            y: pos.y,
            symbolSize: isCenter ? 30 : 14 + Math.min(10, (n?.hours || 6) * 0.5),
            itemStyle: {
              color: STAGE[n?.stage]?.color || "#8b97b3",
              borderColor: isCenter ? "#ffffff" : "rgba(7,11,18,0.9)",
              borderWidth: isCenter ? 2.5 : 1.5,
              shadowBlur: isCenter ? 18 : 8,
              shadowColor: `${STAGE[n?.stage]?.color || "#8b97b3"}${isCenter ? "cc" : "44"}`,
            },
            label: {
              show: true,
              position: "bottom",
              distance: 4,
              color: isCenter ? "#fff" : "#a8b3cc",
              fontSize: isCenter ? 11 : 9.5,
              fontWeight: isCenter ? 600 : 400,
              width: colW - 16,
              overflow: "truncate",
            },
            raw: n,
          };
        }),
        links: subLinks.map((l) => ({
          source: l.source,
          target: l.target,
          lineStyle: {
            color: l.target === center.id || l.source === center.id ? "rgba(76,201,240,0.55)" : "rgba(140,160,200,0.25)",
            width: l.target === center.id || l.source === center.id ? 1.6 : 1,
            curveness: 0.06,
          },
        })),
        emphasis: { focus: "adjacency", label: { color: "#fff" } },
      },
    ],
  });

  chart.on("click", (params) => {
    if (params.dataType === "node" && params.data.raw && params.data.raw.id !== center.id) {
      showNodeDetail(params.data.raw);
      highlightInMainGraph(params.data.raw.id);
    }
  });
}

/** 主图谱上高亮某节点及其邻接（再次点击其他节点前保持）。 */
function highlightInMainGraph(nodeId) {
  const main = state.charts.graph;
  if (!main) return;
  const name = (state.data?.visual.nodes || []).find((n) => n.id === nodeId)?.name;
  if (!name) return;
  main.dispatchAction({ type: "downplay", seriesIndex: 0 });
  main.dispatchAction({ type: "highlight", seriesIndex: 0, name });
}

function renderRoadmap(data) {
  const nodesByStage = {};
  data.path.nodes.forEach((n) => (nodesByStage[n.stage] ||= []).push(n));

  $("#roadmap").innerHTML = data.path.stages
    .map((stage) => {
      const nodes = (nodesByStage[stage.key] || []).sort((a, b) => a.seq - b.seq);
      return `
      <div class="stage-col">
        <header>
          <div class="stage-kicker"><i style="background:${stage.color}"></i>STAGE ${String(data.path.stages.indexOf(stage) + 1).padStart(2, "0")}</div>
          <h4 style="color:${stage.color}">${esc(stage.name)}</h4>
          <div class="stage-meta">第 ${stage.start_week}–${stage.end_week} 周 · ${stage.hours}h · ${nodes.length} 个知识点</div>
          <div class="stage-desc">${esc(stage.desc)}</div>
        </header>
        ${nodes
          .map(
            (n) => `
          <div class="stage-node" title="${esc(n.summary)}">
            <span class="seq">${String(n.seq).padStart(2, "0")}</span>
            <div>
              <div class="node-name">${esc(n.name)}</div>
              <div class="node-meta">第 ${n.start_week} 周起 · ${n.hours}h${n.depends_on.length ? " · 有前置" : ""}</div>
            </div>
          </div>`
          )
          .join("")}
      </div>`;
    })
    .join("");

  const rationale = data.path.rationale || [];
  const milestones = data.path.milestones || [];
  $("#rationale").innerHTML = `
    <strong>Path Agent 排序依据</strong>
    <ul>${rationale.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    ${
      milestones.length
        ? `<strong style="display:block;margin-top:10px">阶段里程碑</strong>
           <ul>${milestones.map((m) => `<li>第 ${m.week} 周 · ${esc(m.stage)}：${esc(m.deliverable)}</li>`).join("")}</ul>`
        : ""
    }`;
}

function renderGantt(data) {
  const host = $("#ganttChart");
  if (typeof echarts === "undefined") return;
  const items = data.visual.gantt;
  host.style.height = `${Math.max(420, items.length * 27 + 110)}px`;
  const chart = state.charts.gantt || echarts.init(host);
  state.charts.gantt = chart;
  const names = items.map((i) => i.name);
  const maxWeek = data.path.total_weeks;

  chart.setOption(
    {
      backgroundColor: "transparent",
      animationDuration: 600,
      grid: { left: 200, right: 40, top: 30, bottom: 46 },
      tooltip: {
        backgroundColor: "rgba(10,15,26,0.94)",
        borderColor: "rgba(255,255,255,0.14)",
        textStyle: { color: "#e9eefc", fontSize: 12 },
        formatter: (p) => {
          const row = items[p.value[0]];
          return `<strong>${esc(row.name)}</strong><br/>第 ${row.start_week}–${row.end_week} 周 · ${row.hours}h<br/><span style="color:${STAGE[row.stage]?.color}">${STAGE[row.stage]?.name}</span>`;
        },
      },
      xAxis: {
        type: "value",
        min: 1,
        max: maxWeek,
        interval: Math.max(1, Math.ceil(maxWeek / 12)),
        axisLabel: { color: "#8b97b3", fontSize: 10, formatter: "{value}周" },
        axisLine: { lineStyle: { color: "rgba(255,255,255,0.12)" } },
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
        name: "学习周",
        nameTextStyle: { color: "#56617a", fontSize: 10 },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: names,
        axisLabel: { color: "#c8d2e8", fontSize: 11, width: 170, overflow: "truncate" },
        axisLine: { lineStyle: { color: "rgba(255,255,255,0.12)" } },
        axisTick: { show: false },
      },
      series: [
        {
          type: "custom",
          encode: { x: [1, 2], y: 0 },
          data: items.map((item, i) => ({
            value: [i, item.start_week, item.end_week, item.hours],
            itemStyle: { color: STAGE[item.stage]?.color || "#8b97b3" },
          })),
          renderItem(params, api) {
            const row = api.value(0);
            const start = api.coord([api.value(1), row]);
            const end = api.coord([api.value(2), row]);
            const height = Math.max(8, api.size([0, 1])[1] * 0.46);
            const width = Math.max(end[0] - start[0], 4);
            return {
              type: "rect",
              shape: { x: start[0], y: start[1] - height / 2, width, height, r: 4 },
              style: { fill: api.visual("color"), opacity: 0.9 },
            };
          },
        },
      ],
    },
    true
  );
}

function renderWeekCards(data) {
  $("#weekGrid").innerHTML = data.path.weekly_plan
    .map(
      (w) => `
      <div class="week-card">
        <span class="week-no">WEEK ${String(w.week).padStart(2, "0")}</span>
        <span class="week-hours">${w.hours}h</span>
        <div class="week-stage" style="color:${STAGE[w.stage]?.color || "#8b97b3"}">${esc(w.stage_name)}</div>
        <div class="week-focus">${w.focus.map((f) => esc(f)).join(" / ")}</div>
      </div>`
    )
    .join("");
}

function progressStore(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "{}");
  } catch {
    return {};
  }
}

function renderChecklist(data) {
  const store = progressStore(state.progressKey);
  const nodesByStage = {};
  data.path.nodes.forEach((n) => (nodesByStage[n.stage] ||= []).push(n));

  $("#checklist").innerHTML = data.path.stages
    .map((stage) => {
      const nodes = (nodesByStage[stage.key] || []).sort((a, b) => a.seq - b.seq);
      return `
      <div class="check-group">
        <h4><i style="background:${stage.color}"></i>${esc(stage.name)}阶段
          <span class="muted">${nodes.length} 个知识点 · ${stage.hours}h</span></h4>
        ${nodes
          .map(
            (n) => `
          <label class="check-item ${store[n.id] ? "checked" : ""}" data-id="${n.id}">
            <input type="checkbox" ${store[n.id] ? "checked" : ""} />
            <span class="item-name">${esc(n.name)}<small>${esc(n.category)}${n.resource ? ` · ${esc(n.resource)}` : ""}</small></span>
            <span class="item-week">第 ${n.start_week}–${n.end_week} 周</span>
            <span class="item-hours">${n.hours}h</span>
          </label>`
          )
          .join("")}
      </div>`;
    })
    .join("");

  $$("#checklist .check-item input").forEach((input) => {
    input.addEventListener("change", (event) => {
      const item = event.target.closest(".check-item");
      const store2 = progressStore(state.progressKey);
      store2[item.dataset.id] = event.target.checked;
      localStorage.setItem(state.progressKey, JSON.stringify(store2));
      item.classList.toggle("checked", event.target.checked);
      updateProgress();
    });
  });
  updateProgress();
}

function updateProgress() {
  const total = $$("#checklist .check-item").length;
  const done = $$("#checklist .check-item input:checked").length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  $("#progressFill").style.width = `${pct}%`;
  $("#progressText").textContent = `学习进度 ${pct}%（${done} / ${total} 个知识点）`;
}

function renderReview(data) {
  const review = data.review;
  const ring = $("#scoreRing");
  const color = review.score >= 90 ? "#3ddc97" : review.score >= 75 ? "#4cc9f0" : "#f7a531";
  ring.style.setProperty("--score", review.score);
  ring.style.setProperty("--ring", color);
  $("#scoreValue").textContent = review.score;
  $("#reviewSummary").textContent = review.summary;
  $("#reviewMeta").textContent = `${review.passed ? "通过校验" : "需要修订"} · 共 ${review.issues.length} 个问题 · 审查来源：${review.source === "llm" ? "LLM + 规则" : "规则引擎"}`;

  const severityText = { high: "高", medium: "中", low: "低" };
  // 用完整知识库映射名称：「缺失前置」的关联点可能不在当前路径里
  const nameOf = new Map((data.knowledge?.points || data.path.nodes).map((p) => [p.id, p.name]));
  $("#issueList").innerHTML = review.issues.length
    ? review.issues
        .map(
          (issue) => `
      <div class="issue">
        <span class="severity ${issue.severity}">${severityText[issue.severity]} · ${esc(issue.type)}</span>
        <div class="issue-body">
          <strong>${esc(issue.message)}</strong>
          ${issue.suggestion ? `<p class="suggestion">${esc(issue.suggestion)}</p>` : ""}
          ${issue.related && issue.related.length ? `<p>关联：${issue.related.map((r) => esc(nameOf.get(r) || r)).join("、")}</p>` : ""}
          <div class="issue-actions">${issueActions(issue, data)}</div>
        </div>
      </div>`
        )
        .join("")
    : '<div class="issue"><span class="severity low">通过</span><div class="issue-body"><strong>未发现结构性问题，路径可直接执行。</strong></div></div>';

  const fixes = review.fixes || [];
  $("#fixList").innerHTML = fixes.length
    ? `<strong>Review Agent 已自动修订</strong><ul>${fixes.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`
    : `<strong>审查结论</strong><ul>
        <li>依赖完整性：所有纳入节点的前置知识均已满足或已标注。</li>
        <li>阶段梯度：入门 → 基础 → 进阶 → 实战，与你的每周投入匹配。</li>
        <li>可执行性：按学习清单逐项推进，并用每周计划跟踪节奏。</li>
      </ul>`;
}

function switchTab(tab) {
  $$("#tabs button").forEach((b) => {
    const active = b.dataset.tab === tab;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", String(active));
  });
  ["graph", "roadmap", "gantt", "list", "review"].forEach((key) => {
    $(`#panel-${key}`).hidden = key !== tab;
  });
  if (tab === "graph" && state.data) {
    renderGraph(state.data);
    setTimeout(() => state.charts.graph?.resize(), 30);
  }
  if (tab === "gantt" && state.data) {
    renderGantt(state.data);
    setTimeout(() => state.charts.gantt?.resize(), 30);
  }
}

function exportJson() {
  if (!state.data) return;
  const blob = new Blob([JSON.stringify(state.data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `learning-path-${state.data.intent.domain}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function clamp(value, min, max) {
  const num = Number(value);
  if (!Number.isFinite(num)) return min;
  return Math.min(max, Math.max(min, num));
}

function issueActions(issue, data) {
  const buttons = [];
  if (issue.type === "周期超限") {
    const need = clamp(Math.ceil(data.path.total_hours / Math.max(1, data.intent.duration_weeks)), 1, 80);
    if (need > data.intent.hours_per_week) {
      buttons.push(`<button class="tiny" type="button" data-action="pace" data-hours="${need}">每周投入提到 ${need}h</button>`);
    }
  }
  const focus = issue.related && issue.related.length ? issue.related[0] : "";
  const label = issue.type === "单点负荷" ? "拆分 / 调整" : "手动调整";
  buttons.push(`<button class="tiny" type="button" data-action="edit" data-focus="${esc(focus)}">${label}</button>`);
  return buttons.join("");
}

function toPoint(p) {
  return {
    id: p.id,
    name: p.name,
    category: p.category || "自定义",
    level: Number(p.level) || 1,
    summary: p.summary || "",
    prerequisites: p.prerequisites || [],
    hours: Number(p.hours) || 6,
    resource: p.resource || "",
  };
}

function openEditor(focusId = null) {
  if (!state.data) return;
  const { intent, path, knowledge } = state.data;
  const seqOf = new Map(path.nodes.map((n) => [n.id, n.seq]));
  const pool = knowledge.points.map((p) => ({ ...p, custom: false }));
  const selected = pool
    .filter((p) => seqOf.has(p.id))
    .sort((a, b) => seqOf.get(a.id) - seqOf.get(b.id));

  edit = {
    weeks: intent.duration_weeks,
    hours: intent.hours_per_week,
    pool,
    selected,
    focusId,
    returnFocus: document.activeElement,
  };
  $("#editWeeks").value = edit.weeks;
  $("#editHours").value = edit.hours;
  $("#editModal").hidden = false;
  document.body.classList.add("modal-open");
  renderEditor();

  setTimeout(() => {
    const row = focusId ? $(`#editSelected .edit-row[data-id="${focusId}"]`) : null;
    if (row) row.scrollIntoView({ block: "center" });
    const input = row ? $('input[data-hours]', row) : $("#editHours");
    input?.focus();
    input?.select?.();
  }, 60);
}

function closeEditor() {
  const target = edit?.returnFocus;
  edit = null;
  $("#editModal").hidden = true;
  document.body.classList.remove("modal-open");
  if (target instanceof HTMLElement && document.contains(target)) target.focus();
}

function renderEditor() {
  if (!edit) return;
  const totalHours = edit.selected.reduce((sum, p) => sum + p.hours, 0);
  const needed = totalHours ? Math.ceil(totalHours / Math.max(1, edit.hours)) : 0;
  $("#editSummary").textContent = `已选 ${edit.selected.length} 个知识点 · 合计 ${totalHours}h · 按每周 ${edit.hours}h 需 ${needed} 周`;
  $("#editHint").textContent =
    needed > edit.weeks
      ? `当前投入需要 ${needed} 周，超出目标周期 ${edit.weeks} 周，应用后会自动延长`
      : "调整后会重新排期并再次运行 Review Agent";

  $("#editSelected").innerHTML = edit.selected.length
    ? edit.selected
        .map((p) => {
          const stage = STAGE[LEVEL_STAGE[p.level] || "basic"];
          return `<div class="edit-row ${edit.focusId === p.id ? "focus" : ""}" data-id="${esc(p.id)}">
            <span class="dot" style="background:${stage?.color || "#8b97b3"}"></span>
            <div class="name">${esc(p.name)}<small>${esc(stage?.name || "基础")} · L${p.level}${p.custom ? " · 自定义" : ""}</small></div>
            <input type="number" min="1" max="60" value="${p.hours}" data-hours aria-label="${esc(p.name)}学时" />
            <button class="remove" type="button" data-remove aria-label="移出路径">×</button>
          </div>`;
        })
        .join("")
    : '<p class="muted">至少保留一个知识点。</p>';

  const chosen = new Set(edit.selected.map((p) => p.id));
  const available = edit.pool.filter((p) => !chosen.has(p.id));
  $("#editAvailable").innerHTML = available.length
    ? available
        .map((p) => `<button class="point-add" type="button" data-add="${esc(p.id)}">+ ${esc(p.name)} <b>${p.hours}h</b></button>`)
        .join("")
    : '<p class="muted">知识库中的知识点已全部纳入路径。</p>';
}

function addCustomPoint() {
  if (!edit) return;
  const name = $("#newName").value.trim();
  if (!name) {
    toast("请先输入知识点名称");
    $("#newName").focus();
    return;
  }
  const point = {
    id: `custom-${Date.now().toString(36)}`,
    name,
    category: $("#newCategory").value.trim() || "自定义",
    level: Number($("#newLevel").value) || 1,
    summary: "",
    prerequisites: [],
    hours: clamp($("#newHours").value, 1, 60),
    resource: "",
    custom: true,
  };
  edit.pool.push(point);
  edit.selected.push(point);
  edit.focusId = point.id;
  $("#newName").value = "";
  $("#newCategory").value = "";
  renderEditor();
  const row = $(`#editSelected .edit-row[data-id="${point.id}"]`);
  row?.scrollIntoView({ block: "center" });
  $("#newName").focus();
}

async function requestAdjust({ weeks, hours, knowledge, selectedIds }) {
  const res = await fetch("/api/adjust", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      intent: state.data.intent,
      knowledge,
      selected_ids: selectedIds,
      weeks,
      hours_per_week: hours,
    }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${detail.slice(0, 120)}`);
  }
  const result = await res.json();
  const merged = {
    ...state.data,
    intent: updateIntent(state.data.intent, weeks, hours),
    knowledge,
    path: result.path,
    visual: result.visual,
    review: result.review,
    elapsed_ms: result.elapsed_ms,
  };
  renderResult(merged, { keepTab: true, keepScroll: true });
  return merged;
}

function updateIntent(intent, weeks, hours) {
  const pattern = /计划\s*\d+\s*周内每周投入\s*\d+\s*小时/;
  const profile = pattern.test(intent.profile)
    ? intent.profile.replace(pattern, `计划 ${weeks} 周内每周投入 ${hours} 小时`)
    : `${intent.level}学习者，计划 ${weeks} 周内每周投入 ${hours} 小时。`;
  return { ...intent, duration_weeks: weeks, hours_per_week: hours, profile };
}

async function applyEditor() {
  if (!edit) return;
  if (!edit.selected.length) {
    toast("至少保留一个知识点");
    return;
  }
  const btn = $("#applyEditsBtn");
  btn.disabled = true;
  $("span", btn).textContent = "重新规划中…";
  try {
    const hoursById = new Map(edit.selected.map((p) => [p.id, p.hours]));
    const basePoints = state.data.knowledge.points.map((p) => ({
      ...p,
      hours: hoursById.get(p.id) ?? p.hours,
    }));
    const baseIds = new Set(basePoints.map((p) => p.id));
    const custom = edit.selected.filter((p) => p.custom && !baseIds.has(p.id)).map(toPoint);
    const knowledge = {
      ...state.data.knowledge,
      points: [...basePoints, ...custom],
    };
    await requestAdjust({
      weeks: clamp(edit.weeks, 1, 104),
      hours: clamp(edit.hours, 1, 80),
      knowledge,
      selectedIds: edit.selected.map((p) => p.id),
    });
    closeEditor();
    toast("已按你的调整重新排期并完成复审");
  } catch (err) {
    toast(`调整失败：${err.message}`);
  } finally {
    btn.disabled = false;
    $("span", btn).textContent = "应用调整并重新审查";
  }
}

async function applyPaceFix(hours) {
  if (!state.data) return;
  try {
    await requestAdjust({
      weeks: state.data.intent.duration_weeks,
      hours: clamp(hours, 1, 80),
      knowledge: state.data.knowledge,
      selectedIds: state.data.path.nodes.map((n) => n.id),
    });
    toast("已按新的每周投入重新排期");
  } catch (err) {
    toast(`调整失败：${err.message}`);
  }
}

function bindEvents() {
  $("#goalForm").addEventListener("submit", (e) => {
    e.preventDefault();
    generate();
  });

  $("#goalInput").addEventListener("input", (e) => {
    $("#charCount").textContent = `${e.target.value.length} / 300`;
  });

  $$("#examples .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      $("#goalInput").value = chip.dataset.goal;
      $("#charCount").textContent = `${chip.dataset.goal.length} / 300`;
      if (chip.dataset.week) $("#weeksInput").value = chip.dataset.week;
      if (chip.dataset.hours) $("#hoursInput").value = chip.dataset.hours;
      $("#goalInput").focus();
    });
  });

  $$("#tabs button").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  $("#exportBtn").addEventListener("click", exportJson);

  $("#editBtn").addEventListener("click", () => openEditor());
  $$("#editModal [data-close]").forEach((el) => el.addEventListener("click", closeEditor));

  $("#editWeeks").addEventListener("input", (e) => {
    if (!edit) return;
    const value = Number(e.target.value);
    if (!Number.isFinite(value) || value < 1) return;
    edit.weeks = Math.min(104, value);
    renderEditor();
  });

  $("#editHours").addEventListener("input", (e) => {
    if (!edit) return;
    const value = Number(e.target.value);
    if (!Number.isFinite(value) || value < 1) return;
    edit.hours = Math.min(80, value);
    renderEditor();
  });

  $("#editSelected").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-remove]");
    if (!btn || !edit) return;
    const id = btn.closest(".edit-row")?.dataset.id;
    edit.selected = edit.selected.filter((p) => p.id !== id);
    if (edit.focusId === id) edit.focusId = null;
    renderEditor();
  });

  $("#editSelected").addEventListener("change", (e) => {
    const input = e.target.closest("input[data-hours]");
    if (!input || !edit) return;
    const id = input.closest(".edit-row")?.dataset.id;
    const point = edit.selected.find((p) => p.id === id);
    if (!point) return;
    point.hours = clamp(input.value, 1, 60);
    renderEditor();
  });

  $("#editAvailable").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-add]");
    if (!btn || !edit) return;
    const point = edit.pool.find((p) => p.id === btn.dataset.add);
    if (!point) return;
    edit.selected.push(point);
    edit.focusId = point.id;
    renderEditor();
    $(`#editSelected .edit-row[data-id="${point.id}"]`)?.scrollIntoView({ block: "center" });
  });

  $("#addPointBtn").addEventListener("click", addCustomPoint);
  $("#newName").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addCustomPoint();
    }
  });
  $("#applyEditsBtn").addEventListener("click", applyEditor);

  $("#issueList").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    if (btn.dataset.action === "pace") applyPaceFix(Number(btn.dataset.hours));
    else if (btn.dataset.action === "edit") openEditor(btn.dataset.focus || null);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && edit) closeEditor();
  });

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      state.charts.graph?.resize();
      state.charts.gantt?.resize();
      state.charts.node?.resize();
    }, 180);
  });
}

renderAgents();
bindEvents();
fetchHealth();
