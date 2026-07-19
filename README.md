# seoul

Infra/tooling workspace for the Seoul Kubernetes cluster.

- `apps/k8s-cluster-monitor/` — in-cluster K8s test dashboard (health/network/security/storage-backup/observability). Deployed and running at https://test2.studiobasa.com/k8stest/ (Basic Auth, changeable from its Settings tab). See its `README.md` for access instructions and `deploy/README.md` for deployment details.
- `.claude/` — Claude Code harness (agents/skills) for cluster auditing, including the manual resilience/chaos-test skill this monitor deliberately doesn't automate.
