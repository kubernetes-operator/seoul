# Deploying k8s-cluster-monitor

## Current status: deployed

All three gates below are done. Namespace `k8s-cluster-monitor` is live,
image `registry.local.cloud:5000/k8s-cluster-monitor:v0.2.0`, schedule
`0 */6 * * *` (every 6 hours, now 6 domains including `performance`),
`CiliumNetworkPolicy` applied, and it's reachable at
`https://test2.studiobasa.com/k8stest/` via `deploy/k8s/10-httproute.yaml`
(v0.1.1 shipped the relative-path frontend change subpath routing needed;
v0.1.2 added self-service password change; v0.2.0 added the `performance`
domain and the load-test feature — see "Performance domain & load testing"
below). The steps below are kept as the reference procedure for
redeploying, recovering, or standing this up again elsewhere — not a to-do
list.

## ArgoCD / GitOps (current deploy method — read this first)

As of 2026-07-24 this app is managed by **ArgoCD** (namespace `argocd`,
v3.4.5), same convention as `os-monitor-prod`. The Application manifest lives
at `deploy/argocd/application.yaml`; it watches this repo's `main` branch and
auto-syncs `deploy/k8s/` into the `k8s-cluster-monitor` namespace with
`prune: true` + `selfHeal: true`.

**What this changes:**

- **Deploys now happen by pushing to `main`.** Edit manifests, `git push`,
  and ArgoCD applies the diff automatically. Do **not** `kubectl apply` /
  `kubectl set image` / `kubectl patch` by hand anymore — `selfHeal` reverts
  any out-of-band change back to git (that's the point). The GATE 1–3 steps
  below are now the **bootstrap/recovery reference**, not the routine path.
- **Images are still built and pushed manually** (there is no image CI).
  GitOps only reconciles manifests, so the release trigger is the image-tag
  bump you commit in `08-deployment.yaml`. Full flow: build → push → bump the
  tag in `08-deployment.yaml` (+ the `frontend` `?v=` params) → commit → push
  → ArgoCD rolls it out.
- **The real Basic Auth Secret is not tracked** (only
  `06-secret.example.yaml.template`, which ArgoCD ignores — it isn't `.yaml`).
  It was seeded out-of-band and ArgoCD never created it, so `prune`/`selfHeal`
  leave it alone.

Bootstrap the Application once (idempotent):

```bash
kubectl apply -f apps/k8s-cluster-monitor/deploy/argocd/application.yaml
kubectl -n argocd get application k8s-cluster-monitor
```

To pause GitOps temporarily (e.g. for a manual hotfix), disable auto-sync,
then re-enable by re-applying the manifest:

```bash
kubectl -n argocd patch application k8s-cluster-monitor --type merge \
  -p '{"spec":{"syncPolicy":{"automated":null}}}'
```

## Performance domain & load testing (v0.2.0)

- `performance` is a normal read-only domain (like `health`/`network`/etc)
  — no new RBAC, no new NetworkPolicy egress, automatically scheduled with
  the rest. Queries Prometheus for CPU throttling, memory-near-limit,
  node saturation, and (opportunistically, only for namespaces that expose
  `http_request_duration_seconds`) app p95 latency / 5xx error rate.
- The load-test feature ships **disabled by default** in code, but is
  **enabled on this deployment** — `MONITOR_LOADTEST_ENABLED: "true"` in
  `05-configmap.yaml` (2026-07-19 operator request; verified with one real
  run: `demo-app/web:80`, concurrency 2, 5s cap — hit the 5,000-total-requests
  ceiling in ~2.7s, 0 errors, p95 1.2ms, `demo-app` pods unaffected).
  Under ArgoCD this value in git is the desired state; it was previously
  flipped by a live `kubectl patch` that predated the GitOps cutover and has
  since been reconciled into `05-configmap.yaml`.
  **To toggle it, change git — do not `kubectl patch` (selfHeal reverts it):**
  ```bash
  # edit MONITOR_LOADTEST_ENABLED in deploy/k8s/05-configmap.yaml, then:
  git commit -am "toggle load-test" && git push        # ArgoCD syncs the ConfigMap
  kubectl -n k8s-cluster-monitor rollout restart deployment/k8s-cluster-monitor
  ```
  (the rollout restart is still needed — the value is read into the process
  environment at startup, so a ConfigMap change alone doesn't take effect on
  the running pod.)
- Targets are a hardcoded allow-list (`backend/app/load_test.py`'s
  `ALLOWED_TARGETS`) — `demo-app`, `gitea`, `model-factory`, `os-monitor`,
  each with specific service:port pairs confirmed live via
  `kubectl get endpoints -n <ns> <svc>` (not guessed — the Grafana
  Service-port-vs-container-port mismatch earlier in this file's history is
  exactly why that check matters; these four happened to match, but it was
  verified). Adding a fifth target namespace means updating **three**
  places by hand: `backend/app/load_test.py`'s `ALLOWED_TARGETS`,
  `deploy/k8s/04-networkpolicy.yaml`'s egress rules, and
  `MONITOR_LOADTEST_ALLOWED_NAMESPACES` in the ConfigMap (informational —
  the first two are the actual enforcement).
- `deploy/k8s/08-deployment.yaml`'s `resources.limits` were bumped (cpu
  500m→1000m, memory 256Mi→512Mi) to give the load-test feature headroom for
  its brief, hard-capped bursts; `requests` are unchanged so idle/scheduled
  behavior is the same as before.
- Load generation happens in-process (the monitor pod itself, via
  `httpx.AsyncClient`) — **not** a spawned Job/Pod, which would need
  `create` on pods/jobs and break the "zero write verbs, ever" RBAC
  invariant this app has held since it was first deployed. See the module
  docstring in `backend/app/load_test.py` for the full reasoning.

**Auth credential model changed in v0.1.2:** Basic Auth is no longer read
from the K8s Secret on every request — it's stored in SQLite (PBKDF2-HMAC
hash + salt), seeded from the Secret's env vars only once, the first time
the pod ever starts against an empty database. Change it from the
dashboard's Settings page (requires the current password); the Secret is
now only a bootstrap/recovery mechanism, not the live value. This was a
deliberate choice to avoid giving the app any `secrets` RBAC (see
`backend/app/auth.py` docstring for the full reasoning).

**If locked out** (forgot the changed password, no Secret access needed):

```bash
kubectl -n k8s-cluster-monitor exec deploy/k8s-cluster-monitor -- \
  python3 -c "import sqlite3; sqlite3.connect('/data/monitor.db').execute('DELETE FROM auth_credential').close()"
kubectl -n k8s-cluster-monitor rollout restart deployment/k8s-cluster-monitor
```

This re-seeds from whatever the `k8s-cluster-monitor-auth` Secret currently
holds. To change what it re-seeds to:

```bash
kubectl create secret generic k8s-cluster-monitor-auth -n k8s-cluster-monitor \
  --from-literal=BASIC_AUTH_USERNAME=admin \
  --from-literal=BASIC_AUTH_PASSWORD="$(openssl rand -base64 24)" \
  --dry-run=client -o yaml | kubectl apply -f -
```

(this alone has no effect on a running deployment until the `auth_credential`
row is cleared as above — the database is authoritative once seeded).

## What this is

An in-cluster, read-only Kubernetes cluster tester with a web dashboard.
Runs comprehensive checks (health/network/security/storage & backup/observability)
on a schedule or on-demand, stores history in SQLite, and shows results as
tables and graphs. Chaos/resilience testing is intentionally **not**
automated here — see `backend/app/checks/base.py` and the "proposed chaos
tests" tab for why, and use the `k8s-resilience-test` Claude Code skill
instead for that, with human approval on every destructive step.

## RBAC rationale (read this before applying)

- **Fully read-only**: `get/list/watch` only, zero `create/update/patch/delete`,
  no `pods/exec`, no `pods/portforward`. See `02-clusterrole.yaml`.
- **No `secrets` access anywhere**, cluster-wide or namespaced. Kubernetes RBAC
  cannot restrict access to metadata only — any read verb on `secrets` ships
  full `data` over the wire. A long-running server process holding that
  permission is a much bigger blast radius than a human occasionally running
  `kubectl get secrets -o custom-columns=...`. The dashboard's security tab
  says so explicitly rather than silently omitting the check.
- **One narrow namespaced exception**: `pods/log` in the `velero` namespace
  only, for the backup-log-error scan (`03b`/`03c`). Reading logs is also an
  information-exposure risk (apps can log secrets/PII to stdout by
  accident), so it's scoped to exactly the one namespace/check that needs it.
- The ClusterRole also grants read on `clusterroles`/`clusterrolebindings`/
  `roles`/`rolebindings` cluster-wide — meaningful reconnaissance value even
  though it's read-only. Accepted trade-off, noted here for the next reviewer.
- The ServiceAccount's own `automountServiceAccountToken` is left at the
  default (true) since this pod's entire purpose is calling the K8s API. The
  security check will (correctly) flag this SA in its own scan output — that's
  transparency working as intended.

## Build

From the `k8s-cluster-monitor/` directory (build context must include both
`backend/` and `frontend/`):

```bash
docker build -f backend/Dockerfile -t registry.local.cloud:5000/k8s-cluster-monitor:vX.Y.Z .
```

Verified locally: builds cleanly, runs as non-root (uid 10001), and starts
correctly under `--read-only --tmpfs /tmp`. Current running version:
`v0.1.2` (see "Current status" above for what each version shipped).

## GATE 1 — push to the registry (pause and confirm before doing this)

```bash
docker push registry.local.cloud:5000/k8s-cluster-monitor:vX.Y.Z
```

Check first whether the registry needs auth/an imagePullSecret — inspect an
existing app's Deployment for a precedent:

```bash
kubectl get deploy -n demo-app -o jsonpath='{.items[0].spec.template.spec.imagePullSecrets}'
```

## GATE 2 — apply to the cluster (pause and confirm before doing this)

Apply in dependency order (do **not** blanket-apply the whole directory
blindly the first time — read what each step does):

```bash
kubectl apply -f 00-namespace.yaml
kubectl apply -f 01-serviceaccount.yaml
kubectl apply -f 02-clusterrole.yaml
kubectl apply -f 03-clusterrolebinding.yaml
kubectl apply -f 03b-role-velero-logs.yaml
kubectl apply -f 03c-rolebinding-velero-logs.yaml

# Create the REAL Basic Auth secret imperatively — never from a committed file:
kubectl create secret generic k8s-cluster-monitor-auth \
  -n k8s-cluster-monitor \
  --from-literal=BASIC_AUTH_USERNAME=<choose-a-username> \
  --from-literal=BASIC_AUTH_PASSWORD="$(openssl rand -base64 24)"
# Save the generated password now — kubectl will not show it again.

kubectl apply -f 05-configmap.yaml
kubectl apply -f 07-pvc.yaml
kubectl apply -f 08-deployment.yaml
kubectl apply -f 09-service.yaml
```

`04-networkpolicy.yaml` is applied **last, separately, and optionally** — see
the warning comment inside that file. Verify the pod is Ready and the
dashboard works *before* adding it, and be ready to roll back immediately if
anything breaks:

```bash
kubectl delete ciliumnetworkpolicy k8s-cluster-monitor-egress -n k8s-cluster-monitor
```

**This bit us during the actual rollout (2026-07-19):** a first attempt used
a plain `networking.k8s.io/v1` `NetworkPolicy` with `ipBlock` rules for the
node-local DNS address and the kube-apiserver ClusterIP/node IPs. Applied
live, it broke ALL egress from the pod (DNS + API access), twice, even after
adding what looked like the right node IPs — rolled back immediately both
times per the procedure above, with zero downtime beyond the few seconds it
took to `delete`. Root cause, found by applying the same policy to a
disposable debug pod (`kubectl run debug-egress-test ... --labels=debug=egress-test`)
and inspecting `hubble observe --from-ip <pod-ip>`: Cilium classifies the
node-local DNS address and the apiserver as special internal identities
(`host` / `kube-apiserver`), which a vanilla `NetworkPolicy` `ipBlock` rule
cannot match at all — the traffic was silently DROPPED regardless of how
correct the IP looked. The fix was rewriting it as a `CiliumNetworkPolicy`
using `toEntities: [host, kube-apiserver]`, verified the same safe way
(disposable pod first) before ever touching the real deployment. See the
comment block in `04-networkpolicy.yaml` for the full detail — if this
cluster ever migrates off Cilium, that file needs to go back to being a
plain `NetworkPolicy`, re-tested the same careful way.

`10-httproute.yaml` **is applied** — the dashboard is reachable at
`https://test2.studiobasa.com/k8stest/` (path-prefix on the cluster's
shared Gateway, same pattern as headlamp-route/monitoring-route/etc). This
exposes a read-only, Basic-Auth-protected tool over HTTPS — a different risk
profile than the Headlamp finding (cluster-admin + public exposure) this
tool's own security check flags elsewhere. Since it's reachable beyond
`kubectl port-forward` now, consider changing the Basic Auth password from
the dashboard's Settings tab (see "Auth credential model changed in v0.1.2"
above) if it hasn't been changed from its initial value yet.

## GATE 3 — enable scheduling (light confirmation)

Scheduling is disabled by default (`MONITOR_SCHEDULE_CRON: ""`). Enable it
from the dashboard's Settings tab once you've confirmed on-demand runs work,
picking a cadence that's reasonable given the added load on the API server,
Prometheus, and Loki. **Currently set to `0 */6 * * *`** (every 6 hours, all
5 domains) via the Settings API — persisted in SQLite, so it survives pod
restarts without needing the ConfigMap's default touched.

## Verify after deploying

```bash
kubectl -n k8s-cluster-monitor get pods -w
kubectl -n k8s-cluster-monitor port-forward svc/k8s-cluster-monitor 8080:80
# open http://localhost:8080 — browser will prompt for the Basic Auth credentials
```

Trigger one "run all" from the Overview tab and confirm findings populate.
Dogfood restart-survival: `kubectl -n k8s-cluster-monitor delete pod -l app.kubernetes.io/name=k8s-cluster-monitor`
and confirm run history is still there after the new pod comes up (the PVC
should have survived the restart — if it didn't, that's the same failure
mode this tool exists to catch in *other* apps).

## Backing up this tool's own data

The PVC uses the cluster's default StorageClass (`reclaimPolicy: Delete`).
Make sure the `k8s-cluster-monitor` namespace is included in the cluster's
Velero backup schedule/selector — otherwise this tool's own finding history
becomes the one PVC that isn't covered by the very backup posture it checks
for other apps.
