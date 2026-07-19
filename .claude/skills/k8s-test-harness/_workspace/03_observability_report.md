# 관측성 검증 보고서

- 점검 일시: 2026-07-19 (KST 기준 오후, UTC 06:2x)
- 대상: monitoring 네임스페이스 kube-prometheus-stack, logging 네임스페이스 loki-stack
- 방식: 전부 읽기 전용 조회 (kubectl, port-forward 후 Prometheus/Loki HTTP API). 설정 변경 없음. port-forward 프로세스는 점검 종료 후 모두 종료 완료.

---

## 종합 판정

| 영역 | 상태 |
|------|------|
| Prometheus 스크레이프 | 대체로 정상 (48 up / 8 down = kube-proxy) |
| Alertmanager | **심각 (CRITICAL) — 미기동, 알림 미전달** |
| 알림 규칙 | 정상 (245개 규칙, 151개 alerting) |
| Grafana | 정상 |
| Loki 인제스천 | 정상 (지연 약 1초) |
| 스크레이프 커버리지 | 인프라 O / 애플리케이션 네임스페이스 대부분 X |

가장 중요한 결론: 알림 규칙과 스크레이프는 살아있으나 **Alertmanager가 83일째(정확히는 2026-07-18 reconcile 실패 이후) 기동되지 못해, 발화된 알림이 어디로도 전달되지 않는 "침묵 실패" 상태**입니다.

---

## Prometheus 타겟 헬스 (Down 목록 + 원인 추정)

- 전체 activeTargets: 56개 → **up 48 / down 8**
- Down 8개 전부 `job=kube-proxy` (kube-system), 오류: `dial tcp <node>:10249: connect: connection refused`

| job | 대상 | 오류 | 원인 추정 |
|-----|------|------|-----------|
| kube-proxy | 8개 노드 전부 (192.168.78.101~105, 192.168.77.101~103) | 10249 connection refused | kube-proxy 메트릭 바인드 주소가 127.0.0.1(기본값)로 설정되어 있어 노드 IP:10249에서 수집 불가. 이 클러스터는 Cilium CNI(cilium-secrets/cilium-test 네임스페이스 존재)를 사용하므로 kube-proxy가 부분 모드/미사용일 가능성도 있음. **`TargetDown`(warning) 알림이 이로 인해 발화 중.** |

정상 수집 중인 주요 job: apiserver(3), coredns(2), kube-controller-manager(3), kube-scheduler(3), kubelet(24, cAdvisor 포함), node-exporter(8), kube-state-metrics(1), prometheus/grafana/operator(자체).

> 참고(health 팀 대체 지표): node-exporter(8/8 up)와 kube-state-metrics(up)가 정상 수집 중이므로, `kubectl top` 실패 시 `node_cpu_seconds_total`, `node_memory_*`, `container_cpu_usage_seconds_total`(cAdvisor via kubelet), `kube_pod_container_resource_*` 등으로 노드/파드 리소스 사용량을 대체 산출 가능합니다.

---

## Alertmanager 상태 (Firing 목록 + 침묵 실패 점검 결과)

### 침묵 실패 점검 결과 — 심각 (최우선 조치 대상)

- Alertmanager CRD(`prometheus-stack-kube-prom-alertmanager`): **READY 0 / AVAILABLE False / RECONCILED False**, 관련 StatefulSet 자체가 생성되지 않음(NotFound).
- 근본 원인(CRD status.conditions):
  `failed to create statefulset: StatefulSet.apps "alertmanager-prometheus-stack-kube-prom-alertmanager" is invalid: spec.volumeClaimTemplates[0].spec.resources[storage]: Required value`
  → Alertmanager storage volumeClaimTemplate에 storageClassName(`nfs-nas-sc-main`)은 지정되어 있으나 **스토리지 용량(resources.requests.storage)이 비어 있어** StatefulSet 생성이 거부됨. 마지막 reconcile 실패 시각 2026-07-18T08:33:33Z.
- Prometheus가 인지한 Alertmanager: **active 0 / dropped 21** → 발화 알림을 보낼 대상이 하나도 없음.
- 이를 시스템 스스로도 감지 중: `PrometheusNotConnectedToAlertmanagers`(warning), `PrometheusOperatorSyncFailed`(warning, monitoring/kube-prometheus-stack) 발화 중.
- `Watchdog`(항상 발화하는 dead-man's switch) 알림이 Prometheus 안에서는 firing이지만, Alertmanager가 없어 외부 수신자에게 도달하지 못하므로 **워치독의 목적(파이프라인 생존 확인)이 무력화된 상태**.

결론: "Firing 알림이 없다"가 아니라 "**Firing 알림은 13건 있으나 전달 경로가 끊긴**" 전형적 침묵 실패입니다. 알림 규칙(prometheusrules 36그룹/245규칙)은 정상 존재하므로 규칙 공백 문제는 아님.

### 현재 Firing 알림 (13건, state=firing / pending 1건)

| alertname | severity | 대상 | 비고 |
|-----------|----------|------|------|
| PrometheusNotConnectedToAlertmanagers | warning | monitoring | 위 침묵 실패의 직접 신호 |
| PrometheusOperatorSyncFailed | warning | monitoring / kube-prometheus-stack | Alertmanager reconcile 실패에 기인 |
| TargetDown | warning | kube-proxy | 위 타겟 8종 다운 |
| KubeDaemonSetRolloutStuck | warning | logging/loki-stack-promtail, velero/node-agent | 롤아웃 정체 (교차 확인 필요) |
| KubeDaemonSetMisScheduled | warning | logging/loki-stack-promtail, velero/node-agent | 파드 오배치 |
| KubeJobFailed | warning | os-monitor/os-monitor-predict-29740470, os-monitor/os-monitor-report-daily-29740380 | 배치 Job 실패 |
| CPUThrottlingHigh | info | gitea/postgresql, gitea/gitea-valkey-cluster | CPU 스로틀링 |
| Watchdog | none | - | 상시 발화(정상). 단 전달 경로 없음 |
| InfoInhibitor | none | - | 정보성 inhibitor |

> storage-backup 팀 교차검증 참고: velero `node-agent` DaemonSet이 RolloutStuck/MisScheduled로 발화 중 → Velero 백업 파이프라인 이상 가능성. storage-backup 팀과 교차 확인 권장.

---

## Grafana 헬스

- prometheus-stack-grafana 파드: `3/3 Running`(82일), 서비스 `prometheus-stack-grafana`(ClusterIP) 정상.
- `GET /api/health` → `{"database":"ok","version":"12.4.3"}` 정상.
- loki-stack-grafana 파드: `2/2 Running` 정상.
- 데이터소스(Prometheus/Loki) 연결 상세 및 대시보드 내용 검증은 **인증 필요로 미수행**(파드/서비스/헬스 레벨까지만 확인). 단, 아래 Loki 인제스천이 정상이고 Prometheus API 조회가 정상이므로 백엔드 데이터소스 자체는 가용.

---

## Loki 인제스천 상태

- loki-stack-0(StatefulSet) `1/1 Running`, promtail DaemonSet 8개 파드 전부 `Running`.
- `GET /ready` → `200 ready`.
- 최근 1시간 `query_range {namespace=~".+"}` 조회: 최신 로그 타임스탬프와 현재 시각 차이 **약 1초** → 인제스천 정상, 지연/중단 없음.
- 사용 가능 레이블: app, component, container, filename, instance, job, namespace, node_name, pod, stream. 여러 네임스페이스(gpu-operator 등) 로그가 실시간 수집됨을 확인.

> 주의: Loki instant query(`/query`)는 시간 범위 없이는 빈 결과를 반환하므로, 인제스천 확인 시 `query_range`에 start/end를 지정해야 함(SKILL 절차의 instant query만으로는 오탐 가능).

---

## 스크레이프 커버리지 갭 (네임스페이스별)

- ServiceMonitor: monitoring 13개, gpu-operator 1개 / PodMonitor: **0개**.
- 실제 스크레이프되는 네임스페이스: default, kube-system, monitoring 뿐. 나머지는 애플리케이션 레벨 메트릭 미수집.

주의: kubelet/cAdvisor(24) + kube-state-metrics + node-exporter가 클러스터 전역 컨테이너/노드/오브젝트 메트릭을 수집하므로 **모든 네임스페이스의 리소스(CPU/메모리/파드상태) 메트릭은 커버**됨. 아래 "갭"은 **애플리케이션 자체 노출 메트릭(/metrics)이 수집되지 않는** 네임스페이스를 의미.

| 네임스페이스 | 워크로드 수 | SM/PM | 애플리케이션 메트릭 커버리지 |
|--------------|-------------|-------|------------------------------|
| gpu-operator | 10 | SM 존재 | **주의: SM은 있으나 active target 없음** — DCGM/exporter 미존재 또는 셀렉터 불일치 의심 |
| monitoring | 5 | O | 커버 |
| kube-system | 13 | (전역 SM) | 컨트롤플레인 커버(kube-proxy만 down) |
| cilium-test-1 | 6 | 없음 | 갭 |
| cert-manager | 3 | 없음 | 갭 (cert-manager는 메트릭 노출 지원, SM 추가 권장) |
| gitea | 3 | 없음 | 갭 |
| demo-app | 3 | 없음 | 갭 |
| logging | 3 | 없음 | 갭 (loki/promtail 메트릭) |
| velero | 3 | 없음 | 갭 (백업 성공/실패 메트릭 미수집 → 백업 알림 정확도 저하) |
| os-monitor | 4 | 없음 | 갭 (자체 PrometheusRule `os-monitor-alerts`는 존재) |
| model-factory | 2 | 없음 | 갭 |
| metallb-system | 2 | 없음 | 갭 |
| arc-systems | 1 | 없음 | 갭 |
| headlamp | 1 | 없음 | 갭 |
| nginx-gateway | 1 | 없음 | 갭 |

> security 팀 공유 대상(모니터링 사각지대): nginx-gateway(인그레스 경로), cert-manager(인증서 만료 메트릭 미수집), metallb-system(LB) 등 보안·가용성상 중요한 컴포넌트의 애플리케이션 메트릭이 수집되지 않음.

---

## 권장 조치 (우선순위)

1. **[CRITICAL] Alertmanager 복구** — Alertmanager CR의 `spec.storage.volumeClaimTemplate.spec.resources.requests.storage`에 용량 값 지정(예: 스토리지클래스 정책에 맞는 Gi). 값 누락으로 StatefulSet이 생성되지 못하고 있어, 현재 모든 알림이 외부로 전달되지 않음. (본 점검은 읽기 전용이므로 수정 미실행 — 조치 필요.)
2. **[HIGH] KubeJobFailed / velero node-agent 이상** — os-monitor 배치 Job 2건 실패, velero node-agent DaemonSet 정체를 storage-backup 팀과 교차 확인.
3. **[MEDIUM] kube-proxy 타겟 다운** — metricsBindAddress 확인(0.0.0.0:10249) 또는 Cilium 사용 시 kube-proxy 스크레이프 잡 비활성화로 오탐 제거.
4. **[LOW/MEDIUM] 커버리지 갭** — cert-manager/velero/nginx-gateway 등 메트릭 노출 컴포넌트에 ServiceMonitor 추가, gpu-operator SM이 타겟을 만들지 못하는 원인 점검.
