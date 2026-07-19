# k8s-cluster-monitor

An in-cluster Kubernetes test tool with a web dashboard: runs read-only
health / network / security / storage-backup / observability checks on a
schedule or on demand, and shows results as tables and trend graphs.

Grew out of a one-off Claude Code audit of this cluster (see
`../../.claude/skills/k8s-*-check/SKILL.md` for the original prose specs this
tool's check modules were ported from). Those skills remain the way to run a
manual, human-reviewed audit — including the resilience/chaos tests this
in-cluster tool deliberately does not automate.

## Status

Deployed and running in the `k8s-cluster-monitor` namespace, image
`registry.local.cloud:5000/k8s-cluster-monitor:v0.1.2`. Schedule: every 6
hours (`0 */6 * * *`), all 5 domains. See `deploy/README.md` for the full
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

1. Edit code, re-run `pytest`.
2. Bump the image tag (semantic version, never `:latest`) in
   `backend/Dockerfile` usage and `deploy/k8s/08-deployment.yaml`.
3. `docker build -f backend/Dockerfile -t registry.local.cloud:5000/k8s-cluster-monitor:vX.Y.Z .`
   from this directory, then `docker push`.
4. `kubectl apply -f deploy/k8s/08-deployment.yaml` (after editing the image
   tag in that file) or `kubectl -n k8s-cluster-monitor set image deployment/k8s-cluster-monitor k8s-cluster-monitor=registry.local.cloud:5000/k8s-cluster-monitor:vX.Y.Z`.
