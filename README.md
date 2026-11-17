# seoul

Infra/tooling workspace for the Seoul Kubernetes cluster.

- `apps/k8s-cluster-monitor/` — in-cluster K8s test dashboard (health/network/security/storage-backup/observability/performance), branded **k8s-cluster-tester** in the UI. Deployed and running at https://test2.studiobasa.com/k8stest/ (form login; default `admin` / `password` is public by design — **replace it on first login** from its Settings tab). Deployment is **GitOps via ArgoCD** — a push to `main` is the deploy (images are still built/pushed manually). See its `README.md` for access instructions and `deploy/README.md` ("ArgoCD / GitOps") for deployment details.
- `.claude/` — Claude Code harness (agents/skills) for cluster auditing, including the manual resilience/chaos-test skill this monitor deliberately doesn't automate.
