// Vanilla JS, no build step. Browsers handle HTTP Basic Auth natively (the
// backend returns WWW-Authenticate: Basic on 401), so fetch() calls to /api
// just need credentials included — no login form to write here.

const DOMAINS = ["health", "network", "security", "storage_backup", "observability", "performance"];
const DOMAIN_LABELS = {
  health: "헬스", network: "네트워크", security: "보안",
  storage_backup: "스토리지/백업", observability: "관측성", performance: "성능",
  load_test: "부하 테스트",
};
// Distinct per-domain line colors for the "도메인별" trend breakdown.
const DOMAIN_COLORS = {
  health: "#16a34a", network: "#2563eb", security: "#dc2626",
  storage_backup: "#9333ea", observability: "#d97706", performance: "#0891b2",
  load_test: "#64748b",
};

async function api(path, opts) {
  const resp = await fetch(path, { credentials: "same-origin", ...opts });
  if (!resp.ok) {
    // Session expired or missing → drop back to the login gate.
    if (resp.status === 401) showLogin("세션이 만료되었습니다. 다시 로그인하세요.");
    let detail = "";
    try { detail = (await resp.json()).detail || ""; } catch (e) { /* body wasn't JSON, ignore */ }
    throw new Error(`${path} -> ${resp.status}${detail ? `: ${detail}` : ""}`);
  }
  return resp.json();
}

// ---------- Auth / session gate ----------
let currentUser = null;

function showLogin(errorText) {
  document.getElementById("app").hidden = true;
  document.getElementById("tabs").hidden = true;
  document.getElementById("user-area").hidden = true;
  document.getElementById("login-overlay").hidden = false;
  document.getElementById("login-error").textContent = errorText || "";
  document.getElementById("login-username").focus();
}

function showApp(username) {
  currentUser = username;
  document.getElementById("login-overlay").hidden = true;
  document.getElementById("tabs").hidden = false;
  document.getElementById("user-area").hidden = false;
  document.getElementById("app").hidden = false;
  document.getElementById("current-user").textContent = `${username} 님`;
  initTabs();
  renderOverview();
}

async function logout() {
  try { await fetch("api/logout", { method: "POST", credentials: "same-origin" }); } catch (e) { /* ignore */ }
  location.reload();  // fresh state → bootstrap() will show the login gate
}

async function bootstrap() {
  document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = document.getElementById("login-username").value.trim();
    const password = document.getElementById("login-password").value;
    const err = document.getElementById("login-error");
    err.textContent = "로그인 중...";
    try {
      const resp = await fetch("api/login", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!resp.ok) {
        let detail = "사용자명 또는 비밀번호가 올바르지 않습니다";
        try { detail = (await resp.json()).detail || detail; } catch (e2) { /* ignore */ }
        err.textContent = detail;
        return;
      }
      const { username: who } = await resp.json();
      document.getElementById("login-password").value = "";
      err.textContent = "";
      showApp(who);
    } catch (ex) {
      err.textContent = `로그인 실패: ${ex.message}`;
    }
  });
  document.getElementById("logout-btn").addEventListener("click", logout);

  // Resume an existing session if the cookie is still valid.
  try {
    const resp = await fetch("api/session", { credentials: "same-origin" });
    if (resp.ok) {
      const { username } = await resp.json();
      showApp(username);
      return;
    }
  } catch (e) { /* fall through to the login gate */ }
  showLogin();
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
  // Clicking the title returns to the main (overview) screen.
  const home = document.getElementById("home-link");
  if (home) home.addEventListener("click", () => switchTab("overview"));
}

function switchTab(tab) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.getElementById(`view-${tab}`).classList.add("active");

  if (tab === "overview") renderOverview();
  else if (DOMAINS.includes(tab)) renderDomainView(tab);
  else if (tab === "load_test") renderLoadTestTab();
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
  const domainSel = document.getElementById("trend-domain").value;
  const days = document.getElementById("trend-days").value;
  const mode = document.getElementById("trend-breakdown").value; // "severity" | "domain"
  const chartEl = document.getElementById("trend-chart");

  // 심각도별: honor the domain dropdown (filter to one domain, or all summed).
  // 도메인별: always fetch every domain — the dropdown is ignored (see caption).
  const useDomainFilter = mode === "severity" && domainSel;
  const rows = await api(`api/findings/trend?days=${days}${useDomainFilter ? `&domain=${domainSel}` : ""}`);

  if (!rows.length) {
    chartEl.innerHTML = '<p class="muted">아직 데이터 없음 — 실행 이력이 쌓이면 표시됩니다.</p>';
    return;
  }

  const dayLabel = (ts) => new Date(ts * 1000).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });
  // Bucket by day so multiple runs on the same day collapse to one x point
  // (rows arrive sorted by started_at ascending).
  const dayOrder = [];
  for (const r of rows) {
    const d = dayLabel(r.started_at);
    if (!dayOrder.includes(d)) dayOrder.push(d);
  }
  const dayIndex = Object.fromEntries(dayOrder.map((d, i) => [d, i]));
  const zeros = () => dayOrder.map(() => 0);

  if (mode === "domain") {
    // One line per domain = total findings (critical+warning+info) that day.
    const byDomain = {};
    for (const r of rows) {
      if (!byDomain[r.domain]) byDomain[r.domain] = zeros();
      byDomain[r.domain][dayIndex[dayLabel(r.started_at)]] += r.critical + r.warning + r.info;
    }
    const order = [...DOMAINS, ...Object.keys(byDomain).filter((d) => !DOMAINS.includes(d))];
    const series = {};
    const labels = {};
    order.filter((d) => byDomain[d]).forEach((d) => {
      series[d] = byDomain[d];
      labels[d] = DOMAIN_LABELS[d] || d;
    });
    lineChart(chartEl, series, dayOrder, labels, DOMAIN_COLORS);
  } else {
    // Severity lines, summed per day across whatever rows matched the filter.
    const series = { critical: zeros(), warning: zeros(), info: zeros() };
    for (const r of rows) {
      const i = dayIndex[dayLabel(r.started_at)];
      series.critical[i] += r.critical;
      series.warning[i] += r.warning;
      series.info[i] += r.info;
    }
    lineChart(chartEl, series, dayOrder, {
      critical: "심각 (critical)", warning: "경고 (warning)", info: "정보 (info)",
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("trend-domain").addEventListener("change", renderTrend);
  document.getElementById("trend-days").addEventListener("change", renderTrend);
  document.getElementById("trend-breakdown").addEventListener("change", renderTrend);
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

// ---------- Chaos tab ----------
// NOTE: The "실행" button here NEVER mutates the cluster. On confirm it reveals
// the copy-ready kubectl commands below (mirrored from the k8s-resilience-test
// skill), which a human then decides whether to run. This app's ClusterRole
// has no create/update/patch/delete verbs (deploy/k8s/02-clusterrole.yaml), so
// there is intentionally no server endpoint to wire this to — the safety net is
// structural, not just UI copy. Placeholders (<pod-name>, <node>, <svc>, <ns>)
// are left literal on purpose: the operator fills in the concrete target, which
// forces a deliberate choice rather than a one-click destructive action.
const CHAOS_TESTS = [
  {
    name: "파드 삭제 → 자가치유 확인",
    target: "임의 워크로드 파드 1개",
    impact: "레플리카 2개 이상이면 무중단, 1개면 순단",
    status: "manual — k8s-resilience-test 스킬로 사람이 직접 승인 후 실행",
    commands: [
      "kubectl delete pod <pod-name> -n <ns>",
      "kubectl get pods -n <ns> -w   # 재생성 확인 후 Ctrl-C",
    ],
    note: "재생성 시간과 최종 상태(Running/CrashLoopBackOff 등)를 기록하세요.",
  },
  {
    name: "노드 cordon+drain → 재스케줄링 확인",
    target: "워커 노드 1대 (컨트롤플레인 제외)",
    impact: "해당 노드 파드 일시 재시작",
    status: "manual — k8s-resilience-test 스킬로 사람이 직접 승인 후 실행",
    commands: [
      "kubectl cordon <node>",
      "kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=120s",
      "kubectl get pods -A -o wide   # 재스케줄링 확인",
      "kubectl uncordon <node>   # 원상복구",
    ],
    note: "컨트롤플레인 노드(playcekubectr01~03)는 쿼럼 손실 위험이 있어 드레인 대상에서 제외하세요.",
  },
  {
    name: "PodDisruptionBudget 위반 테스트",
    target: "PDB 설정된 워크로드",
    impact: "정상이라면 드레인이 차단됨 (보호 확인)",
    status: "manual — k8s-resilience-test 스킬로 사람이 직접 승인 후 실행",
    commands: [
      "kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=120s",
    ],
    note: "minAvailable을 위반하는 수의 파드를 동시에 드레인 시도합니다. 드레인이 차단되면(타임아웃/에러) PDB가 정상 보호 중이라는 뜻이므로 성공으로 기록하고 강제하지 마세요.",
  },
  {
    name: "HPA 부하 테스트",
    target: "HPA 설정된 서비스",
    impact: "일시적 리소스 사용량 증가, 스케일 아웃 확인",
    status: "manual — k8s-resilience-test 스킬로 사람이 직접 승인 후 실행",
    commands: [
      "kubectl run load-gen --rm -it --restart=Never --image=busybox -- \\",
      "  /bin/sh -c \"while true; do wget -q -O- http://<svc>.<ns>.svc.cluster.local; done\"",
      "kubectl get hpa -w   # 스케일 아웃 반응 시간 기록",
    ],
    note: "테스트 종료 시 부하 생성 파드를 확실히 종료하세요(--rm 또는 수동 delete).",
  },
  {
    name: "LoadBalancer/Gateway 일부 백엔드 재시작",
    target: "다중 레플리카 서비스의 파드 일부",
    impact: "나머지 정상 파드로 트래픽 유지되는지 확인",
    status: "manual — k8s-resilience-test 스킬로 사람이 직접 승인 후 실행",
    commands: [
      "kubectl delete pod <backend-pod> -n <ns>   # 일부 백엔드만",
    ],
    note: "모든 백엔드를 동시에 삭제(완전 중단)하지 마세요. 나머지 정상 파드로 트래픽이 유지되는지 관찰합니다.",
  },
];

function renderChaosTab() {
  const tbody = document.getElementById("chaos-table-body");
  tbody.innerHTML = CHAOS_TESTS.map((t, i) => `
    <tr>
      <td>${t.name}</td><td>${t.target}</td><td>${t.impact}</td>
      <td class="muted">${t.status}</td>
      <td><button type="button" class="btn-danger chaos-run-btn" data-index="${i}">실행</button></td>
    </tr>
  `).join("");
  tbody.querySelectorAll(".chaos-run-btn").forEach((btn) => {
    btn.addEventListener("click", () => openChaosModal(Number(btn.dataset.index)));
  });
}

// ---------- Chaos confirmation modal (never mutates the cluster) ----------
function openChaosModal(index) {
  const t = CHAOS_TESTS[index];
  if (!t) return;
  const overlay = document.getElementById("chaos-modal");

  document.getElementById("chaos-modal-name").textContent = t.name;
  document.getElementById("chaos-modal-target").textContent = t.target;
  document.getElementById("chaos-modal-impact").textContent = t.impact;

  const cmdText = t.commands.join("\n");
  document.getElementById("chaos-modal-cmd-pre").textContent = cmdText;
  document.getElementById("chaos-modal-note").textContent = t.note || "";

  // Reset to the pre-confirm state each time it opens.
  const commandsEl = document.getElementById("chaos-modal-commands");
  commandsEl.hidden = true;
  const confirmBtn = document.getElementById("chaos-confirm-btn");
  confirmBtn.disabled = false;

  confirmBtn.onclick = () => {
    // Reveal the commands. No fetch(), no cluster change — by design.
    commandsEl.hidden = false;
    confirmBtn.disabled = true;
  };
  document.getElementById("chaos-cancel-btn").onclick = closeChaosModal;
  document.getElementById("chaos-copy-btn").onclick = () => copyChaosCommands(cmdText);
  overlay.onclick = (e) => { if (e.target === overlay) closeChaosModal(); };

  overlay.hidden = false;
}

function closeChaosModal() {
  document.getElementById("chaos-modal").hidden = true;
}

async function copyChaosCommands(text) {
  const btn = document.getElementById("chaos-copy-btn");
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = "복사됨 ✓";
  } catch (e) {
    // Clipboard API needs a secure context / permission; fall back to select-all.
    const pre = document.getElementById("chaos-modal-cmd-pre");
    const range = document.createRange();
    range.selectNodeContents(pre);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    btn.textContent = "직접 복사하세요 (Ctrl+C)";
  }
  setTimeout(() => { btn.textContent = "복사"; }, 2000);
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

  document.getElementById("pw-current-user").textContent = currentUser || "";
  await refreshUserList();

  document.getElementById("adduser-form").onsubmit = async (e) => {
    e.preventDefault();
    const statusEl = document.getElementById("adduser-status");
    const username = document.getElementById("adduser-username").value.trim();
    const password = document.getElementById("adduser-password").value;
    if (password.length < 8) {
      statusEl.textContent = "비밀번호는 8자 이상이어야 합니다.";
      return;
    }
    statusEl.textContent = "추가 중...";
    try {
      await api("api/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      statusEl.textContent = `사용자 '${username}' 추가됨`;
      document.getElementById("adduser-username").value = "";
      document.getElementById("adduser-password").value = "";
      await refreshUserList();
    } catch (err) {
      statusEl.textContent = err.message.includes("409")
        ? "이미 존재하는 사용자명입니다."
        : `실패: ${err.message}`;
    }
  };

  document.getElementById("password-form").onsubmit = async (e) => {
    e.preventDefault();
    const statusEl = document.getElementById("password-status");
    const current_password = document.getElementById("current-password").value;
    const new_password = document.getElementById("new-password").value;
    const confirm = document.getElementById("new-password-confirm").value;

    if (new_password !== confirm) {
      statusEl.textContent = "새 비밀번호가 일치하지 않습니다.";
      return;
    }
    if (new_password.length < 8) {
      statusEl.textContent = "새 비밀번호는 8자 이상이어야 합니다.";
      return;
    }

    statusEl.textContent = "변경 중...";
    try {
      await api("api/auth/password", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password, new_password }),
      });
      statusEl.textContent = "변경됨 (현재 로그인은 그대로 유지됩니다).";
      e.target.reset();
    } catch (err) {
      statusEl.textContent = err.message.includes("401")
        ? "현재 비밀번호가 올바르지 않습니다."
        : `실패: ${err.message}`;
    }
  };
}

async function refreshUserList() {
  try {
    const { users } = await api("api/users");
    document.getElementById("user-list").textContent = users.join(", ");
  } catch (e) { /* 401 handled by api(); ignore here */ }
}

// ---------- Load test ----------
const LOAD_TEST_P95_WARNING_MS = 1000; // mirrors backend/app/load_test.py's P95_WARNING_MS

let _loadtestTargets = null;

async function fetchLoadtestTargets() {
  if (!_loadtestTargets) _loadtestTargets = await api("api/loadtest/targets");
  return _loadtestTargets;
}

function populateLoadtestSelects(targets) {
  const nsSelect = document.getElementById("lt-namespace");
  const svcSelect = document.getElementById("lt-service");
  const portSelect = document.getElementById("lt-port");

  nsSelect.innerHTML = Object.keys(targets.allowed_targets)
    .map((ns) => `<option value="${ns}">${ns}</option>`).join("");

  function refreshPorts() {
    const ns = nsSelect.value;
    const svc = svcSelect.value;
    const ports = (targets.allowed_targets[ns] || {})[svc] || [];
    portSelect.innerHTML = ports.map((p) => `<option value="${p}">${p}</option>`).join("");
  }
  function refreshServices() {
    const ns = nsSelect.value;
    const services = targets.allowed_targets[ns] || {};
    svcSelect.innerHTML = Object.keys(services).map((s) => `<option value="${s}">${s}</option>`).join("");
    refreshPorts();
  }

  nsSelect.onchange = refreshServices;
  svcSelect.onchange = refreshPorts;
  refreshServices();

  document.getElementById("lt-concurrency").max = targets.max_concurrency;
  document.getElementById("lt-duration").max = targets.max_duration_seconds;
}

async function renderLoadTestTab() {
  const targets = await fetchLoadtestTargets();
  document.getElementById("loadtest-disabled-notice").style.display = targets.enabled ? "none" : "block";
  populateLoadtestSelects(targets);
  await loadLoadtestHistory();

  document.getElementById("loadtest-form").onsubmit = async (e) => {
    e.preventDefault();
    const statusEl = document.getElementById("loadtest-status");
    const resultEl = document.getElementById("loadtest-result");

    const namespace = document.getElementById("lt-namespace").value;
    const service = document.getElementById("lt-service").value;
    const port = parseInt(document.getElementById("lt-port").value, 10);
    const path = document.getElementById("lt-path").value || "/";
    const concurrency = parseInt(document.getElementById("lt-concurrency").value, 10);
    const duration_seconds = parseFloat(document.getElementById("lt-duration").value);

    const summary = `concurrency ${concurrency} × ${duration_seconds}s × GET against ${namespace}/${service}:${port}${path}`;
    if (!confirm(`다음 조건으로 부하 테스트를 실행합니다:\n\n${summary}\n\n실제 트래픽이 발생합니다. 계속할까요?`)) {
      return;
    }

    statusEl.textContent = "실행 중...";
    resultEl.style.display = "none";
    try {
      const result = await api("api/loadtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ namespace, service, port, path, concurrency, duration_seconds }),
      });
      statusEl.textContent = "완료";
      renderLoadtestResult(resultEl, result);
      await loadLoadtestHistory();
    } catch (err) {
      statusEl.textContent = `실패: ${err.message}`;
    }
  };
}

function renderLoadtestResult(container, r) {
  container.style.display = "block";
  const errorRate = r.total_requests ? r.error_count / r.total_requests : 0;
  container.innerHTML = `
    <p><strong>${escapeHtml(r.target)}</strong></p>
    <p>요청 ${r.total_requests}건, 에러 ${r.error_count}건 (${(errorRate * 100).toFixed(1)}%), achieved RPS ${r.achieved_rps}</p>
    <p>p50 ${r.p50_ms ?? "-"}ms / p95 ${r.p95_ms ?? "-"}ms / p99 ${r.p99_ms ?? "-"}ms</p>
  `;
  if (r.p95_ms != null) {
    const gaugeEl = document.createElement("div");
    container.appendChild(gaugeEl);
    thresholdGauge(gaugeEl, {
      value: r.p95_ms, max: Math.max(r.p95_ms, LOAD_TEST_P95_WARNING_MS), label: "p95 응답시간", unit: "ms",
    });
  }
}

async function loadLoadtestHistory() {
  const rows = await api("api/loadtest/history");
  const tbody = document.getElementById("loadtest-history-body");
  tbody.innerHTML = rows.map((r) => `
    <tr>
      <td>${fmtTime(r.started_at)}</td>
      <td>${escapeHtml(r.target)}</td>
      <td>${r.concurrency}</td>
      <td>${r.total_requests}</td>
      <td>${r.error_count}</td>
      <td>${r.p95_ms ?? "-"}ms</td>
      <td>${r.achieved_rps}</td>
      <td>${escapeHtml(r.triggered_by)}</td>
    </tr>
  `).join("");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

bootstrap();
