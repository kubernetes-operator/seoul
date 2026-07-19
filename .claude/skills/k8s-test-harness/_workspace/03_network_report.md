# 네트워크 검증 보고서

- 점검 일시: 2026-07-19 (UTC 기준 06:2x)
- 점검자: k8s-network 에이전트 (관찰 + 임시 테스트만 수행, 기존 워크로드/정책 무변경)
- 클러스터: k8s v1.34.3 / 8노드 (control-plane 3, infra 2, worker 3) — 전 노드 Ready
- CNI: Cilium 1.18.6 · LB: MetalLB(L2) · GW: nginx-gateway(Gateway API) · TLS: cert-manager

---

## Cilium/CNI 상태

| 항목 | 상태 | 비고 |
|---|---|---|
| cilium 에이전트 파드 | 8/8 Running (1/1) | cilium-tht49 재시작 1회(88d 전, 현재 안정) |
| cilium-operator | 2/2 Running | 62grq 재시작 8회(최근 3d18h 전), pldph 6회 — 현재 정상 |
| `cilium status --brief` | OK | |
| Cluster health | **8/8 reachable** | Probe interval ~2m50s |
| Controller Status | 175/175 healthy | |
| Modules Health | OK(160), Degraded(0), Stopped(13) | Degraded 0으로 정상 (Stopped=미사용 모듈) |
| Routing | Tunnel [vxlan] / Host Legacy | |
| Masquerading | IPTables (IPv4 Enabled) | |
| IPAM | IPv4 33/254 (10.233.70.0/24) — 여유 충분 | |
| KubeProxyReplacement | **False** | kube-proxy 병행 사용 (eBPF 대체 미적용) — 의도 여부 확인 권장 |
| Encryption | Disabled | 노드간 트래픽 암호화 미적용 — 의도 여부 확인 권장 |
| Hubble | Ok (relay 1/1 Running), Metrics: Disabled | 플로우 관측 가능, 메트릭 export는 비활성 |

- 재현: `kubectl -n kube-system exec ds/cilium -- cilium status`, `kubectl -n kube-system get pods -l k8s-app=cilium -o wide`
- **DNS 인프라(부가):** nodelocaldns DaemonSet 8/8, CoreDNS 2/2, dns-autoscaler 1/1 — 모두 정상. Pod resolv.conf는 node-local 캐시(169.254.25.10)를 가리킴(Kubespray 표준).

---

## NetworkPolicy 커버리지 (네임스페이스별 표)

- NetworkPolicy: **2건(gitea 전용)**, CiliumNetworkPolicy: **0건**, CiliumClusterwideNetworkPolicy: **0건**
- 즉, gitea를 제외한 전 네임스페이스는 **기본 all-allow 상태**. 아래는 취약점 단정이 아니라 "정책 부재 = 기본 개방, 의도 여부 확인 필요" 중립 기술.

| 네임스페이스 | 정책 유무 | 구분 | 비고 |
|---|---|---|---|
| gitea | NetworkPolicy 2건 | 앱 | `gitea-postgresql`, `gitea-valkey-cluster` (백엔드 DB/캐시 보호) |
| kube-system | 없음 | 시스템 | 기본 all-allow |
| cert-manager, metallb-system, nginx-gateway | 없음 | 인프라 | 기본 all-allow |
| monitoring, logging, os-monitor | 없음 | 관측 | 기본 all-allow (Grafana 등 노출) |
| default, demo-app, headlamp, minio, velero | 없음 | 앱/운영 | 기본 all-allow |
| model-factory, gpu-operator, arc-systems | 없음 | ML/CI | 기본 all-allow |
| cilium-test-1, cilium-secrets, kube-public, kube-node-lease | 없음 | 시스템/테스트 | 기본 all-allow |

- 심각도: **정보/중립** — 클러스터 전반 east-west 트래픽이 정책으로 분리되어 있지 않음. security 팀원과 교차 검토 대상(특히 minio/velero/model-factory 등 데이터 보유 네임스페이스).
- 재현: `kubectl get networkpolicy,ciliumnetworkpolicy -A`

---

## LoadBalancer/MetalLB 할당 현황

| Service | Type | External-IP | Ports | 상태 |
|---|---|---|---|---|
| nginx-gateway/nginx-gateway | LoadBalancer | 192.168.77.200 | 80/443 | 정상 할당, `<pending>` 없음 |

- IPAddressPool `first-pool`: `192.168.77.200-192.168.77.210` (11 IP, autoAssign=true) — 사용 1 / 가용 11, **여유 충분**, 고갈 위험 없음.
- L2Advertisement `first-pool-advertisement`: first-pool 광고, 인터페이스/노드셀렉터 제약 없음.
- speaker DaemonSet: **2/2** (Desired 2) — nodeSelector `node-role.kubernetes.io/infra=` 로 infra01/infra02에만 배치. **의도된 설계**(8노드 중 2개만 실행)로, 결함 아님. controller 1/1 정상.
- 심각도: **정상**. (참고: L2 speaker가 infra 2노드에 한정 → 두 노드 동시 장애 시 LB VIP 광고 중단 가능. resilience 팀원 장애주입 시 고려 요망.)
- 재현: `kubectl get svc -A | grep LoadBalancer`, `kubectl get ipaddresspool,l2advertisement -n metallb-system`

---

## Ingress/Gateway 라우팅 및 인증서 상태

- 레거시 Ingress 리소스: **없음** (전량 Gateway API 사용)
- GatewayClass `nginx` (controller `gateway.nginx.org/nginx-gateway-controller`): Accepted=True
- Gateway `default/gateway`: Address **192.168.77.200**, Accepted=True, Programmed=True
  - listener `http` (80, HTTP): attachedRoutes=8
  - listener `https` (443, HTTPS, cert=`letsencrypt-issuer-public-tls`): attachedRoutes=8

| HTTPRoute (ns/name) | Hostname | Backend:Port | Accepted | ResolvedRefs | 엔드포인트 |
|---|---|---|---|---|---|
| default/gemma4-route | test1.studiobasa.com | open-webui:80 | True | True | 있음(1) |
| demo-app/demo-app-route | test2.studiobasa.com | web:80 | True | True | 있음(1) |
| gitea/gitea-route | test2.studiobasa.com | gitea-http:3000 | True | True | 있음(1) |
| headlamp/headlamp-route | test2.studiobasa.com | headlamp:80 | True | True | 있음(1) |
| kube-system/cilium-route | test2.studiobasa.com | hubble-ui:80 | True | True | 있음(1) |
| logging/logging-route | test2.studiobasa.com | loki-stack-grafana:80 | True | True | 있음(1) |
| monitoring/monitoring-route | test2.studiobasa.com | prometheus-stack-grafana:80 | True | True | 있음(1) |
| os-monitor/os-monitor-route | test2.studiobasa.com | os-monitor-dashboard:80 | True | True | 있음(2) |

- 7개 라우트가 `test2.studiobasa.com` 을 공유 → path 기반 라우팅. 전 라우트 Accepted/ResolvedRefs=True이며 모든 백엔드 Service에 정상 엔드포인트 존재(빈 엔드포인트 없음).
- cert-manager:
  - ClusterIssuer `letsencrypt-clusterissuer-public`: Ready=True
  - Certificate `default/letsencrypt-issuer-public-cert`: **Ready=True**, dnsNames=[studiobasa.com, test1.studiobasa.com, test2.studiobasa.com], notAfter **2026-09-23**, 자동갱신 예정 2026-08-24 → 사용 호스트 전부 커버, 만료 여유 충분.
  - TLS secret `letsencrypt-issuer-public-tls` 존재(kubernetes.io/tls).
- 심각도: **정상**. 발견된 라우팅/인증서 결함 없음.
- 재현: `kubectl get gateway,httproute -A`, `kubectl get certificate,clusterissuer -A`

---

## DNS/연결성 테스트 결과 (생성/삭제한 테스트 리소스 명시)

- **생성 리소스:** `default` 네임스페이스에 임시 파드 `netcheck` (image: nicolaka/netshoot, `--rm --restart=Never`)
- **삭제 확인:** 세션 종료 시 자동 삭제됨 — 로그상 "pod netcheck deleted from default namespace", 이후 `kubectl get pod netcheck -n default` → **NotFound**(잔존 없음). 수동 정리 불필요.

| 테스트 | 결과 |
|---|---|
| resolv.conf | search `*.svc.cluster.local`, nameserver 169.254.25.10 (node-local cache) |
| `nslookup kubernetes.default` | 성공 → 10.233.0.1 |
| `nslookup gitea-http.gitea.svc` | 성공 → 10.233.66.220 (엔드포인트와 일치) |
| 외부 DNS `studiobasa.com` | 성공 (non-authoritative) |
| curl Gateway (Host: test2.studiobasa.com) | **HTTP 200** (nginx-gateway svc 경유 라우팅 정상) |
| curl CoreDNS metrics :9153 | HTTP 200 |

- 참고: nslookup 출력의 `Got recursion not available` 경고는 node-local DNS 캐시의 정상 응답 특성으로, 해석 자체는 모두 성공(무해).
- Hubble DROPPED 플로우(최근 50): IPv6 RouterSolicitation / Unsupported L2·L3 protocol 만 관측 → **정책 기반 거부(policy-denied) 없음**. NetworkPolicy 부재 상태와 일관됨.
- 재현: `kubectl run netcheck --rm -i --restart=Never --image=nicolaka/netshoot -n default --command -- sh -c "..."`, `kubectl -n kube-system exec ds/cilium -- hubble observe --verdict DROPPED --last 50`

---

## 종합 및 후속 제안

- **정상:** Cilium 8/8 · LB IP 할당 · Gateway/HTTPRoute 8건 전부 Accepted · 백엔드 엔드포인트 존재 · 인증서 Ready · DNS/외부 접속/게이트웨이 200.
- **확인 필요(중립):**
  1. NetworkPolicy가 gitea 외 전무 → 클러스터 east-west 기본 개방. 의도 여부를 security 팀원과 교차 검토(minio/velero/model-factory 우선).
  2. Cilium `KubeProxyReplacement=False`, `Encryption=Disabled` — 설계 의도 확인 권장.
  3. MetalLB speaker가 infra 2노드 한정 → 동시 장애 시 VIP 광고 중단 가능(resilience 팀원 공유).
- 이번 점검 중 기존 워크로드/정책 변경 없음. 임시 리소스(netcheck) 잔존 없음.
