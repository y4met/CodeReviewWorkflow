// 状态
let graphData = null;
let pollTimer = null;
let currentRun = null;

const NW = 150, NH = 54; // 节点尺寸

// 事件中的节点名 -> 图上节点 id
const NODE_MAP = {
  coder: "coder",
  static_review: "static_review",
  runtime_test: "runtime_test",
  review: "decision",
  fixer: "fixer",
  workflow: "end",
};

// Tabs
document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".tabbody").forEach((t) => t.classList.add("hidden"));
    document.getElementById("tab-" + btn.dataset.tab).classList.remove("hidden");
  };
});

// 流程图
async function loadGraph() {
  graphData = await (await fetch("/api/graph")).json();
  const svg = document.getElementById("graph");
  svg.innerHTML = "";

  // 箭头
  const defs = el("defs", {});
  defs.innerHTML =
    '<marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">' +
    '<path d="M0,0 L8,3 L0,6 Z" fill="#94a3b8"/></marker>';
  svg.appendChild(defs);

  // 边
  graphData.edges.forEach((e) => {
    const a = graphData.nodes.find((n) => n.id === e.from);
    const b = graphData.nodes.find((n) => n.id === e.to);
    const p1 = center(a), p2 = center(b);
    let d;
    if (e.kind === "loop") {
      const mx = (p1.x + p2.x) / 2 - 60, my = (p1.y + p2.y) / 2 + 40;
      d = `M${p1.x},${p1.y} Q${mx},${my} ${p2.x},${p2.y}`;
    } else {
      d = `M${p1.x},${p1.y} L${p2.x},${p2.y}`;
    }
    svg.appendChild(el("path", { class: `edge ${e.kind}`, id: `edge-${e.from}-${e.to}`, d, "marker-end": "url(#arrow)" }));
    if (e.label) {
      const t = el("text", { class: "edge-label", x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 - 4 });
      t.textContent = e.label;
      svg.appendChild(t);
    }
  });

  // 节点
  graphData.nodes.forEach((n) => {
    const g = el("g", { class: `node ${n.type}`, id: `node-${n.id}`, transform: `translate(${n.x},${n.y})` });
    g.appendChild(el("rect", { width: NW, height: NH }));
    const lines = n.label.split("\n");
    lines.forEach((ln, i) => {
      const t = el("text", { x: NW / 2, y: NH / 2 + (i - (lines.length - 1) / 2) * 16 + 5 });
      t.textContent = ln;
      g.appendChild(t);
    });
    svg.appendChild(g);
  });

  // 根据所有节点边界动态计算 viewBox，避免节点超出画布
  const pad = 40;
  const minX = Math.min(...graphData.nodes.map((n) => n.x)) - pad;
  const minY = Math.min(...graphData.nodes.map((n) => n.y)) - pad;
  const maxX = Math.max(...graphData.nodes.map((n) => n.x)) + NW + pad;
  const maxY = Math.max(...graphData.nodes.map((n) => n.y)) + NH + pad;
  svg.setAttribute("viewBox", `${minX} ${minY} ${maxX - minX} ${maxY - minY}`);
}

function center(n) { return { x: n.x + NW / 2, y: n.y + NH / 2 }; }
function el(tag, attrs) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

// 根据事件更新节点状态
function updateNodeStatus(state) {
  const status = {};
  state.events.forEach((ev) => {
    const id = NODE_MAP[ev.node];
    if (!id) return;
    if (ev.kind === "node_start" || ev.kind === "branch_start") status[id] = "running";
    if (ev.kind === "node_end" || ev.kind === "branch_end") status[id] = ev.status;
  });
  if (state.status === "success") {
    status["end"] = state.review_passed ? "success" : "failed";
    status["decision"] = state.review_passed ? "success" : "failed";
  }
  graphData.nodes.forEach((n) => {
    const g = document.getElementById(`node-${n.id}`);
    g.classList.remove("running", "success", "failed");
    if (status[n.id]) g.classList.add(status[n.id]);
  });

  // 高亮最终走过的分支边
  const failEdge = document.getElementById("edge-decision-fixer");
  const okEdge = document.getElementById("edge-decision-end");
  if (failEdge) failEdge.classList.toggle("active", state.round >= 1 && !state.review_passed && state.status !== "success");
  if (okEdge) okEdge.classList.toggle("active", state.review_passed);
}

// 面板内容
function renderPanels(state) {
  const badge = document.getElementById("runState");
  const done = state.status === "success";
  let text = state.status;
  let cls = state.status === "created" ? "" : state.status;
  if (state.status === "running") {
    text = `running（轮次 ${state.round}）`;
  } else if (done) {
    if (state.review_passed) {
      text = "已通过并交付";
      cls = "success";
    } else {
      text = state.outcome === "max_rounds" ? "未通过（达到轮次上限）" : "未通过审查";
      cls = "failed";
    }
  }
  badge.textContent = text;
  badge.className = "badge " + cls;

  document.getElementById("log").innerHTML = state.events
    .map((ev) => {
      const cls = ev.kind === "router" ? "ev-router" : `ev-${ev.status || "info"}`;
      const dur = ev.duration_ms ? ` (${ev.duration_ms}ms)` : "";
      return `<span class="${cls}">[${ev.ts}] ${ev.node} · ${ev.kind}${ev.status ? "/" + ev.status : ""}${dur}</span> ${escapeHtml(ev.message)}`;
    })
    .join("\n");

  document.getElementById("code").textContent = state.code || "(无)";
  document.getElementById("test").textContent = state.test_output || "(无)";
  document.getElementById("issues").textContent = state.issues.length
    ? state.issues.map((i) => `[${i.severity}] ${i.desc}`).join("\n\n")
    : "(无)";

  document.getElementById("resumeBtn").disabled = state.status !== "failed";
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

// 轮询
async function poll(rid) {
  const r = await (await fetch(`/api/run/${rid}`)).json();
  currentRun = r;
  updateNodeStatus(r);
  renderPanels(r);
  if (r.status === "running") {
    pollTimer = setTimeout(() => poll(rid), 800);
  } else {
    loadRuns();
  }
}

// 发起运行
document.getElementById("runBtn").onclick = async () => {
  const requirement = document.getElementById("requirement").value.trim();
  if (!requirement) return alert("请填写需求");
  const body = {
    requirement,
    demo: document.getElementById("demo").checked,
    parallel: document.getElementById("parallel").checked,
    max_rounds: parseInt(document.getElementById("maxRounds").value),
  };
  const r = await (await fetch("/api/run", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  })).json();
  if (r.error) return alert(r.error);
  clearTimeout(pollTimer);
  poll(r.run_id);
};

// 恢复
document.getElementById("resumeBtn").onclick = async () => {
  if (!currentRun) return;
  const r = await (await fetch(`/api/run/${currentRun.run_id}/resume`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ demo: document.getElementById("demo").checked }),
  })).json();
  if (r.error) return alert(r.error);
  poll(r.run_id);
};

// 清除历史
document.getElementById("clearBtn").onclick = async () => {
  if (!confirm("确定清除全部历史运行记录？正在运行的记录会保留，此操作不可恢复。")) return;
  const r = await (await fetch("/api/runs", { method: "DELETE" })).json();
  if (r.error) return alert(r.error);

  if (currentRun && r.deleted_ids.includes(currentRun.run_id)) {
    currentRun = null;
    document.getElementById("runState").textContent = "就绪";
    document.getElementById("runState").className = "badge";
    document.getElementById("log").textContent = "(历史记录已清除)";
    document.getElementById("code").textContent = "(无)";
    document.getElementById("issues").textContent = "(无)";
    document.getElementById("test").textContent = "(无)";
    document.getElementById("resumeBtn").disabled = true;
  }
  await loadRuns();
  const extra = r.skipped.length ? `，保留 ${r.skipped.length} 条运行中记录` : "";
  alert(`已清除 ${r.deleted} 条历史记录${extra}`);
};

// 历史运行
async function loadRuns() {
  const runs = await (await fetch("/api/runs")).json();
  const box = document.getElementById("runs");
  box.innerHTML = "";
  if (!runs.length) {
    box.innerHTML = '<div class="empty">暂无历史记录</div>';
    return;
  }
  runs.forEach((r) => {
    const d = document.createElement("div");
    d.className = "run";
    d.innerHTML = `<span class="st ${r.status}">${r.status}</span> · 轮次${r.round}<br><span title="${r.requirement}">${r.requirement || ""}</span><br><small>${r.run_id}</small>`;
    d.onclick = () => poll(r.run_id);
    box.appendChild(d);
  });
}

loadGraph();
loadRuns();
