# k8s-cluster-monitor

An in-cluster Kubernetes test tool with a web dashboard: runs read-only
health / network / security / storage-backup / observability / performance
checks on a schedule or on demand, and shows results as tables and trend
graphs. Also includes an active, human-triggered load-test feature (disabled
by default) for a small allow-list of in-cluster targets — see "Load
testing" below.

Grew out of a one-off Claude Code audit of this cluster (see
`../../.claude/skills/k8s-*-check/SKILL.md` for the original prose specs this
tool's check modules were ported from). Those skills remain the way to run a
manual, human-reviewed audit — including the resilience/chaos tests this
in-cluster tool deliberately does not automate.

## Status

Deployed and running in the `k8s-cluster-monitor` namespace, image
`registry.local.cloud:5000/k8s-cluster-monitor:v0.2.5`. Schedule: every 6
hours (`0 */6 * * *`), all 6 domains. See `deploy/README.md` for the full
deploy history, including a Cilium-specific NetworkPolicy gotcha hit and
fixed during rollout (standard `NetworkPolicy` `ipBlock` rules silently
don't match Cilium's `host`/`kube-apiserver` entities on this cluster — the
shipped `deploy/k8s/04-networkpolicy.yaml` is a `CiliumNetworkPolicy` for
that reason, not a portability oversight).

## Accessing the dashboard

**https://test2.studiobasa.com/k8stest/** — exposed via the cluster's
shared Gateway, same pattern as Headlamp/Grafana/etc (`deploy/k8s/10-httproute.yaml`).
Log in with the Basic Auth username/password (change it any time from the
**Settings tab** — see "Changing the password" below). Note the trailing
slash — `/k8stest` without it 302-redirects to the slash form (needed
because, unlike Grafana/Headlamp, this app isn't natively subpath-aware; the
frontend uses relative asset/API paths instead, which only resolve
correctly under the trailing-slash URL — see the comment block in
`deploy/k8s/10-httproute.yaml` for the full mechanism).

**Port-forward (no cluster/DNS dependency, useful for local debugging):**

```bash
kubectl -n k8s-cluster-monitor port-forward svc/k8s-cluster-monitor 8080:80
```

Then open `http://localhost:8080` (no trailing-slash subtlety at the root
path). `Ctrl+C` to stop it when done.

### Changing the password

Settings tab → "비밀번호 변경" — requires the current password, so this is
self-service and needs no cluster access. The credential lives in SQLite
(hashed), not read from the K8s Secret on every request; the Secret is only
used to seed the very first boot. See `backend/app/auth.py`'s docstring and
`deploy/README.md` for the reasoning and the lockout-recovery procedure.

### Load testing

**부하 테스트** tab — generates real GET traffic against a small, explicit
allow-list of in-cluster Services (`demo-app`, `gitea`, `model-factory`,
`os-monitor`; see `backend/app/load_test.py`'s `ALLOWED_TARGETS`). No
arbitrary hostnames or external URLs — this is a cluster-internal testing
feature, not a general-purpose load generator, and the egress
`CiliumNetworkPolicy` structurally can't reach outside the cluster anyway.

- Ships **disabled by default** (`MONITOR_LOADTEST_ENABLED`, see
  `deploy/README.md` to toggle) — **currently enabled** on the live
  deployment, verified with one real run against `demo-app/web:80`.
- Hard server-side caps (not just UI limits): duration ≤ 60s, concurrency ≤
  50, total requests ≤ 5,000 per run, GET-only, one load test at a time.
- Runs in-process (the monitor pod itself issues the requests) rather than
  spawning a separate Job — this app's ClusterRole has zero
  create/update/patch/delete verbs anywhere, and that stays true for this
  feature too (see `backend/app/load_test.py`'s module docstring for why
  spawning a Job was rejected).
- The form requires confirming the resolved target + params before
  sending — this generates real traffic against a real (if pre-approved)
  workload, not a read-only check.
- Results also appear as a summary Finding (`domain=load_test`) in the
  unified findings view, in addition to the dedicated history table.

## Structure

- `backend/` — FastAPI app (API + serves the static frontend), check modules,
  SQLite persistence, APScheduler. See the docstrings in `backend/app/checks/*.py`
  for what each domain checks and why `secrets` are excluded.
- `frontend/static/` — plain HTML/JS dashboard, no build step, no CDN
  dependencies (hand-rolled SVG charts in `js/charts.js`).
- `deploy/k8s/` — manifests + **`deploy/README.md`** (RBAC rationale, apply
  order, and the gates to pause at before affecting the shared cluster).

## Local development

```bash
cd backend
python3 -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest   # unit tests, fully mocked — no cluster needed

# Local smoke test against the real cluster (uses your existing kubeconfig):
BASIC_AUTH_USERNAME=admin BASIC_AUTH_PASSWORD=admin \
  MONITOR_DB_PATH=/tmp/dev.db ./.venv/bin/uvicorn app.main:app --reload
```

Note: the `observability` check calls Prometheus/Alertmanager/Grafana/Loki
via in-cluster Service DNS (e.g. `prometheus-stack-kube-prom-prometheus.monitoring.svc.cluster.local`),
which only resolves/routes from *inside* the cluster. Running it locally
will time out on that domain specifically — expected, not a bug; it's why
this tool needs to actually run as a pod rather than a local script.

## Making changes after this initial deploy

Deploys are now **GitOps via ArgoCD** — a push to `main` is the deploy. The
old `kubectl apply` / `set image` path is gone: `selfHeal` reverts any manual
change back to git. See `deploy/README.md` ("ArgoCD / GitOps") for the full
rationale.

1. Edit code, re-run `pytest`. Bump the frontend `?v=` params in
   `frontend/static/index.html` if you touched `frontend/static/`.
2. Bump the image tag (semantic version, never `:latest`) in
   `deploy/k8s/08-deployment.yaml`.
3. `docker build -f backend/Dockerfile -t registry.local.cloud:5000/k8s-cluster-monitor:vX.Y.Z .`
   from this directory, then `docker push` (image build/push is still manual —
   ArgoCD reconciles manifests, not images).
4. `git commit` + `git push`. ArgoCD detects the new image tag in
   `08-deployment.yaml` and rolls it out automatically. Watch it with
   `kubectl -n argocd get application k8s-cluster-monitor` and
   `kubectl -n k8s-cluster-monitor rollout status deployment/k8s-cluster-monitor`.
