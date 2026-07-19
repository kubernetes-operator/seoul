# Deploying k8s-cluster-monitor

## Current status: deployed

All three gates below are done. Namespace `k8s-cluster-monitor` is live,
image `registry.local.cloud:5000/k8s-cluster-monitor:v0.1.0`, schedule
`0 */6 * * *` (every 6 hours, all 5 domains), `CiliumNetworkPolicy` applied.
The steps below are kept as the reference procedure for redeploying,
recovering, or standing this up again elsewhere — not a to-do list.

**Known follow-up:** the Basic Auth password was intentionally set to a weak
value at the operator's explicit request during initial setup. Rotate it
when convenient:

```bash
kubectl create secret generic k8s-cluster-monitor-auth -n k8s-cluster-monitor \
  --from-literal=BASIC_AUTH_USERNAME=admin \
  --from-literal=BASIC_AUTH_PASSWORD="$(openssl rand -base64 24)" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n k8s-cluster-monitor rollout restart deployment/k8s-cluster-monitor
```

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
docker build -f backend/Dockerfile -t <registry>/k8s-cluster-monitor:v0.1.0 .
```

Verified locally this session: builds cleanly, runs as non-root (uid 10001),
and starts correctly under `--read-only --tmpfs /tmp`.

## GATE 1 — push to the registry (pause and confirm before doing this)

```bash
docker push registry.local.cloud:5000/k8s-cluster-monitor:v0.1.0
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

`10-httproute.yaml.disabled` is **not** applied by default — see the warning
comment inside it about combining a cluster-posture dashboard with public
exposure (this cluster's own audit flagged exactly that combination
elsewhere as a HIGH risk).

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
