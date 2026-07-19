// Vanilla JS, no build step. Browsers handle HTTP Basic Auth natively (the
// backend returns WWW-Authenticate: Basic on 401), so fetch() calls to /api
// just need credentials included — no login form to write here.

const DOMAINS = ["health", "network", "security", "storage_backup", "observability"];
const DOMAIN_LABELS = {
  health: "헬스", network: "네트워크", security: "보안",
  storage_backup: "스토리지/백업", observability: "관측성",
};

async function api(path, opts) {
  const resp = await fetch(path, { credentials: "same-origin", ...opts });
  if (!resp.ok) throw new Error(`${path} -> ${resp.status}`);
  return resp.json();
}

function severityPill(sev) {
  return `<span class="sev-pill sev-${sev}">${sev}</span>`;
}

function fmtTime(epochSeconds) {
  if (!epochSeconds) return "-";
  return new Date(epochSeconds * 1000).toLocaleString("ko-KR");
}

// ---------- Tabs ----------
function initTabs() {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
}

function switchTab(tab) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.getElementById(`view-${tab}`).classList.add("active");

  if (tab === "overview") renderOverview();
  else if (DOMAINS.includes(tab)) renderDomainView(tab);
  else if (tab === "chaos") renderChaosTab();
  else if (tab === "settings") renderSettings();
}

// ---------- Overview ----------
async function renderOverview() {
  const cardsEl = document.getElementById("overview-cards");
  cardsEl.innerHTML = "로딩 중...";
  const latest = await api("api/runs/latest");

  cardsEl.innerHTML = "";
  for (const domain of DOMAINS) {
    const run = latest[domain];
    const card = document.createElement("div");
    card.className = "card";
    if (!run) {
      card.innerHTML = `<h3>${DOMAIN_LABELS[domain]}</h3><p class="muted">아직 실행 이력 없음</p>
        <div class="card-actions"><button data-domain="${domain}" class="run-btn btn-primary">지금 실행</button></div>`;
    } else {
      const total = run.critical_count + run.warning_count + run.info_count || 1;
      card.innerHTML = `
        <span class="status-badge status-${run.status}">${run.status}</span>
        <h3>${DOMAIN_LABELS[domain]}</h3>
        <div class="muted">마지막 실행: ${fmtTime(run.started_at)}</div>
        ${bar("critical", run.critical_count, total)}
        ${bar("warning", run.warning_count, total)}
        ${bar("info", run.info_count, total)}
        <div class="card-actions">
          <button data-domain="${domain}" class="run-btn btn-primary">지금 실행</button>
          <button data-domain="${domain}" class="view-btn">상세 보기</button>
        </div>`;
    }
    cardsEl.appendChild(card);
  }

  cardsEl.querySelectorAll(".run-btn").forEach((btn) =>
    btn.addEventListener("click", () => triggerRun(btn.dataset.domain, btn)));
  cardsEl.querySelectorAll(".view-btn").forEach((btn) =>
    btn.addEventListener("click", () => switchTab(btn.dataset.domain)));

  document.getElementById("run-all-btn").onclick = () => triggerRun("all", document.getElementById("run-all-btn"));

  await renderTrend();
}

function bar(sev, value, total) {
  const pct = Math.round((value / total) * 100);
  return `<div class="severity-bar-row">
    <span style="width:50px">${sev} ${value}</span>
    <div class="severity-bar-track"><div class="severity-bar-fill fill-${sev}" style="width:${pct}%"></div></div>
  </div>`;
}

async function triggerRun(domain, btn) {
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "실행 중...";
  try {
    await api(`api/runs/${domain}`, { method: "POST" });
    await renderOverview();
  } catch (e) {
    alert(`실행 실패: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function renderTrend() {
  const domain = document.getElementById("trend-domain").value;
  const days = document.getElementById("trend-days").value;
  const rows = await api(`api/findings/trend?days=${days}${domain ? `&domain=${domain}` : ""}`);

  const series = { critical: [], warning: [], info: [] };
  const labels = [];
  for (const row of rows) {
    series.critical.push(row.critical);
    series.warning.push(row.warning);
    series.info.push(row.info);
    labels.push(new Date(row.started_at * 1000).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" }));
  }
  const chartEl = document.getElementById("trend-chart");
  if (!rows.length) {
    chartEl.innerHTML = '<p class="muted">아직 데이터 없음 — 실행 이력이 쌓이면 표시됩니다.</p>';
    return;
  }
  lineChart(chartEl, series, labels);
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("trend-domain").addEventListener("change", renderTrend);
  document.getElementById("trend-days").addEventListener("change", renderTrend);
});

// ---------- Domain detail views ----------
async function renderDomainView(domain) {
  const view = document.getElementById(`view-${domain}`);
  view.innerHTML = `<h2>${DOMAIN_LABELS[domain]}</h2>
    <div class="toolbar">
      <button class="btn-primary run-now">지금 실행</button>
      <label>심각도 <select class="sev-filter">
        <option value="">전체</option>
        <option value="critical">critical</option>
        <option value="warning">warning</option>
        <option value="info">info</option>
      </select></label>
      <label><input type="checkbox" class="include-resolved"> 해결된 항목 포함</label>
    </div>
    <div class="domain-viz"></div>
    <table class="findings-table">
      <thead><tr><th>심각도</th><th>제목</th><th>네임스페이스</th><th>최초 발견</th><th>최근 확인</th><th></th></tr></thead>
      <tbody class="findings-body"></tbody>
    </table>`;

  view.querySelector(".run-now").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { await api(`api/runs/${domain}`, { method: "POST" }); await loadFindings(); }
    finally { e.target.disabled = false; }
  });
  view.querySelector(".sev-filter").addEventListener("change", loadFindings);
  view.querySelector(".include-resolved").addEventListener("change", loadFindings);

  async function loadFindings() {
    const sev = view.querySelector(".sev-filter").value;
    const includeResolved = view.querySelector(".include-resolved").checked;
    const findings = await api(
      `api/findings?domain=${domain}${sev ? `&severity=${sev}` : ""}${includeResolved ? "&include_resolved=true" : ""}`
    );
    const tbody = view.querySelector(".findings-body");
    tbody.innerHTML = "";
    for (const f of findings) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${severityPill(f.severity)}</td>
        <td>${f.title}
          ${f.evidence || f.reproduce_cmd ? `<div><button class="evidence-toggle">근거 보기</button>
            <div class="evidence-block">${f.evidence ? escapeHtml(f.evidence) + "<br>" : ""}${f.reproduce_cmd ? `<code>${escapeHtml(f.reproduce_cmd)}</code>` : ""}</div>
          </div>` : ""}
        </td>
        <td>${f.namespace || "-"}</td>
        <td>${fmtTime(f.first_seen)}</td>
        <td>${fmtTime(f.last_seen)}${f.resolved_at ? " (해결됨)" : ""}</td>
        <td></td>`;
      tbody.appendChild(tr);
    }
    tbody.querySelectorAll(".evidence-toggle").forEach((btn) =>
      btn.addEventListener("click", () => btn.nextElementSibling.classList.toggle("open")));
  }

  await renderDomainViz(domain, view.querySelector(".domain-viz"));
  await loadFindings();
}

async function renderDomainViz(domain, container) {
  if (domain === "health") {
    const findings = await api("api/findings?domain=health&include_resolved=false");
    const nodeIssues = {};
    findings.filter((f) => f.check_id === "node_pressure" || f.check_id === "node_not_ready").forEach((f) => {
      nodeIssues[f.resource] = (nodeIssues[f.resource] || 0) + 1;
    });
    if (Object.keys(nodeIssues).length) {
      const box = document.createElement("div");
      box.className = "chart-box";
      stackedBarChart(box, Object.keys(nodeIssues), { issues: Object.values(nodeIssues) }, { issues: "#dc2626" });
      container.appendChild(box);
    }
  } else if (domain === "observability") {
    const findings = await api("api/findings?domain=observability&include_resolved=false");
    const downCount = findings.filter((f) => f.check_id === "prometheus_target_down").length;
    if (downCount) {
      const box = document.createElement("div");
      box.className = "chart-box";
      box.innerHTML = `<p class="muted">Down 타겟 ${downCount}건 (전체 findings 테이블 참고)</p>`;
      container.appendChild(box);
    }
  } else if (domain === "storage_backup") {
    const findings = await api("api/findings?domain=storage_backup&include_resolved=false");
    const stale = findings.find((f) => f.check_id === "velero_backup_stale");
    const none = findings.find((f) => f.check_id === "velero_no_successful_backup");
    if (none) {
      const box = document.createElement("div");
      box.className = "chart-box";
      box.innerHTML = '<p style="color:var(--critical)">유효 백업 없음 — RPO 게이지 계산 불가 (긴급)</p>';
      container.appendChild(box);
    } else if (stale) {
      const box = document.createElement("div");
      box.className = "chart-box";
      thresholdGauge(box, { value: 48, max: 48, label: "마지막 성공 백업 경과", unit: "h" });
      container.appendChild(box);
    }
  }
}

// ---------- Chaos tab (static, read-only) ----------
const CHAOS_TESTS = [
  { name: "파드 삭제 → 자가치유 확인", target: "임의 워크로드 파드 1개", impact: "레플리카 2개 이상이면 무중단, 1개면 순단", status: "manual — k8s-resilience-test 스킬로 사람이 직접 승인 후 실행" },
  { name: "노드 cordon+drain → 재스케줄링 확인", target: "워커 노드 1대 (컨트롤플레인 제외)", impact: "해당 노드 파드 일시 재시작", status: "manual — k8s-resilience-test 스킬로 사람이 직접 승인 후 실행" },
  { name: "PodDisruptionBudget 위반 테스트", target: "PDB 설정된 워크로드", impact: "정상이라면 드레인이 차단됨 (보호 확인)", status: "manual — k8s-resilience-test 스킬로 사람이 직접 승인 후 실행" },
  { name: "HPA 부하 테스트", target: "HPA 설정된 서비스", impact: "일시적 리소스 사용량 증가, 스케일 아웃 확인", status: "manual — k8s-resilience-test 스킬로 사람이 직접 승인 후 실행" },
  { name: "LoadBalancer/Gateway 일부 백엔드 재시작", target: "다중 레플리카 서비스의 파드 일부", impact: "나머지 정상 파드로 트래픽 유지되는지 확인", status: "manual — k8s-resilience-test 스킬로 사람이 직접 승인 후 실행" },
];

function renderChaosTab() {
  const tbody = document.getElementById("chaos-table-body");
  tbody.innerHTML = CHAOS_TESTS.map((t) => `
    <tr><td>${t.name}</td><td>${t.target}</td><td>${t.impact}</td><td class="muted">${t.status}</td></tr>
  `).join("");
}

// ---------- Settings ----------
function describeCron(cron) {
  if (!cron) return "스케줄 비활성화됨";
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return `원시 표현식: ${cron}`;
  const [min, hour, dom, mon, dow] = parts;
  if (min !== "*" && hour === "*" && dom === "*" && mon === "*" && dow === "*") return `매시 ${min}분`;
  if (min !== "*" && hour !== "*" && dom === "*" && mon === "*" && dow === "*") return `매일 ${hour}:${min.padStart(2, "0")}`;
  if (min !== "*" && hour !== "*" && dom === "*" && mon === "*" && dow !== "*") return `매주 (요일코드 ${dow}) ${hour}:${min.padStart(2, "0")}`;
  return `원시 표현식: ${cron} (직접 해석 필요)`;
}

async function renderSettings() {
  const schedule = await api("api/schedule");
  document.getElementById("cron-input").value = schedule.cron || "";
  document.getElementById("cron-preview").textContent = describeCron(schedule.cron);
  document.querySelectorAll(".domain-checkbox").forEach((cb) => {
    cb.checked = schedule.enabled_domains.includes(cb.value);
  });

  document.getElementById("cron-input").oninput = (e) => {
    document.getElementById("cron-preview").textContent = describeCron(e.target.value);
  };

  document.getElementById("settings-form").onsubmit = async (e) => {
    e.preventDefault();
    const cron = document.getElementById("cron-input").value.trim() || null;
    const enabled_domains = Array.from(document.querySelectorAll(".domain-checkbox:checked")).map((cb) => cb.value);
    const statusEl = document.getElementById("settings-status");
    statusEl.textContent = "저장 중...";
    try {
      await api("api/schedule", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cron, enabled_domains }) });
      statusEl.textContent = "저장됨";
    } catch (err) {
      statusEl.textContent = `실패: ${err.message}`;
    }
  };
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

initTabs();
renderOverview();
