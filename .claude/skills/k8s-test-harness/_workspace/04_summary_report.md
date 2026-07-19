# K8s 클러스터 종합 테스트 — 요약 보고서

- 점검일: 2026-07-19
- 범위: 헬스 / 네트워크 / 보안 / 스토리지-백업 / 관측성 (5개 영역, 사용자 요청으로 복원력/카오스 영역 제외)
- 클러스터: v1.34.3, 8노드(control-plane 3, infra 2, worker 3), Cilium + MetalLB + nginx-gateway(Gateway API) + cert-manager + Velero/MinIO + kube-prometheus-stack + loki-stack

## 클러스터 개요
노드/컨트롤플레인/워크로드 자체는 안정적으로 가동 중(모든 노드 Ready, 활성 CrashLoop/OOM 0건, 리소스 여유 충분)이다. 다만 **"보이지 않는 곳"의 결함 3건이 서로 다른 영역에서 교차 확인되며 우선순위가 가장 높다.**

## 교차 검증된 핵심 이슈 (Critical)

### 1. Alertmanager 완전 미기동 — 모든 알림이 무전달 중 (observability, CRITICAL)
Alertmanager StatefulSet이 `volumeClaimTemplate.spec.resources.requests.storage` 값 누락으로 2026-07-18부터 생성 자체가 실패했다. Prometheus는 발화 중인 알림 13건을 갖고 있지만 연결된 Alertmanager가 0개라 어디로도 전달되지 않는다. 심지어 파이프라인 생존 확인용 `Watchdog` 알림조차 무력화된 상태다.
**의미:** 이 보고서에 실린 다른 이슈들(백업 전건 실패, 배치 Job 실패 등)도 실제 운영 중에는 아무도 알림받지 못하고 있었을 가능성이 높다. **가장 먼저 조치할 항목.**

### 2. Velero 백업 전건 실패 — 유효 복구 지점 0개 (storage-backup, CRITICAL)
최근 27일간 생성된 백업 15건이 전부 `FailedValidation`. 원인은 VolumeSnapshotLocation이 2개(`default`, `volume-backup`) 존재하는데 어느 것도 기본으로 지정되지 않은 순수 설정 오류다. MinIO 백엔드 자체는 정상. observability 팀이 동시에 발견한 `velero/node-agent` DaemonSet RolloutStuck/MisScheduled 알림과도 연결되는 정황이다.
**의미:** 현재 클러스터는 사실상 재해복구 안전망이 없다. **복원력/카오스 테스트는 이 문제 해결 전까지 보류를 권고한다** (스토리지 팀 자체 제언이기도 함).

### 3. NFS 기반 고아 PV 삭제 무한 실패 (health + storage-backup 교차 확인)
`monitoring/data-timescaledb-0` PV가 이미 삭제된 PVC의 잔재로 21시간 넘게 `VolumeFailedDelete`를 355회 반복 중(NFS mount exit status 32). TimescaleDB는 `os-monitor` 네임스페이스로 이전된 것으로 보이며 데이터 유실 위험은 낮으나, NFS 서버(192.168.200.200) 측 마운트 가용성 자체를 점검할 신호로 두 팀이 독립적으로 지적했다.

## 높은 우선순위 (High)

### 4. 개인 계정에 클러스터 전권 직결 (security)
User `user-9mtlz`가 `cluster-admin`과 `cattle-globalrole-admin`(Rancher) 양쪽에 개별 직접 바인딩되어 있다. 그룹/RBAC 계층을 거치지 않은 개인 계정 직결은 계정 탈취 시 전면 장악으로 이어진다.

### 5. Headlamp 대시보드 = cluster-admin + 공개 라우트 노출 (security + network 교차 확인)
security가 발견한 "Headlamp SA에 cluster-admin 부여"는 network가 확인한 "headlamp가 Gateway `test2.studiobasa.com`으로 공개 노출"과 결합되면 위험도가 증폭된다. 대시보드 인증 방식(SSO/로컬 계정 등)을 반드시 확인해야 한다.

### 6. 사용자 네임스페이스 Pod Security Standards 사실상 전무 (security)
20개 네임스페이스 중 19개가 `enforce=none`. demo-app/model-factory/gitea 등 실제 애플리케이션 네임스페이스도 특권 통제가 걸려있지 않다.

## 중간 우선순위 (Medium)

| 이슈 | 발견 팀 | 비고 |
|---|---|---|
| `default/node-debugger-*` 등 호스트 전권(hostNetwork+PID+IPC) 디버그 파드 상주 | security | 임시 파드 정리 필요, resilience 테스트 시 제외 대상으로도 지적됨 |
| `minio` 네임스페이스 유령 PVC 7개(≈3.5TB), 워크로드 0개 | storage-backup | 방치 여부 확인 필요 (reclaimPolicy=Delete) |
| 사내 앱(`demo-app`, `model-factory`, `os-monitor`) `:latest` 태그 다수, 다이제스트 고정률 10% | security | 롤백/공급망 무결성 저하 |
| NetworkPolicy가 gitea 2건 제외 전무 — 클러스터 east-west 기본 all-allow | network + security 교차 | 특히 minio/velero/model-factory 우선 검토 권고 |
| 스크레이프 커버리지 갭 — cert-manager/velero/nginx-gateway/metallb 등 애플리케이션 메트릭 미수집 | observability + security 교차 | 인증서 만료·백업·LB 등 가용성 핵심 컴포넌트가 모니터링 사각지대 |
| kube-proxy 타겟 8개 전부 Down (connection refused) | observability | Cilium 환경에서 kube-proxy 스크레이프 잡 설정 재검토 |
| KubeJobFailed(os-monitor 배치 2건) | observability | 원인 조사 필요 |

## 낮은 우선순위 / 참고 (Low/Info)
- DNS 업스트림 `168.126.63.1` 중복 기재로 nameserver 3개 제한 초과 경고(전역 8개 파드) — health
- `letsencrypt-issuer-public-cert` 66일 후 만료(자동갱신 정상 동작 여부 확인 권고) — security
- Cilium `KubeProxyReplacement=False`, `Encryption=Disabled` — 의도된 설계인지 확인 권고 — network
- MetalLB speaker가 infra 2노드에만 배치(의도된 설계) — 두 노드 동시 장애 시 LB VIP 광고 중단 가능 — network
- SA 토큰 자동마운트 다수 미차단, runAsNonRoot 미설정 다수 — security
- 헬스/네트워크/성능 자체는 전반적으로 양호 (Cilium 8/8, Gateway 8개 라우트 전부 정상, GPU 워커 커널 스큐는 기능 이상 없음)

## 복원력(카오스) 테스트 관련 권고
이번 실행에서는 사용자 요청으로 제외했으나, storage-backup 팀의 독립적 결론과 일치하여 **다음 실행 시에도 위 Critical 2건(Alertmanager 복구, Velero VSL 설정)이 해결되기 전까지는 카오스 테스트 착수를 보류할 것을 권고**한다. 안전망(백업) 없이 장애를 주입하는 것은 위험도가 비대칭적으로 크다.

## 다음 단계 제안
1. Alertmanager storage 용량 설정 수정 (가장 시급, 영향 범위 큼에도 수정 자체는 간단)
2. Velero 스케줄에 VolumeSnapshotLocation 명시 또는 서버 기본값 지정
3. `user-9mtlz` cluster-admin 바인딩 소유자 확인 및 최소권한 전환
4. Headlamp 인증 방식 점검
5. 위 조치 완료 후 재점검 + 복원력 테스트 실행 여부 재논의

---
*본 보고서는 5개 서브 에이전트(헬스/네트워크/보안/스토리지-백업/관측성)의 병렬 읽기 전용 점검 결과를 통합한 것이며, 시크릿 데이터 값은 어떤 항목에도 포함되지 않았고 클러스터 상태를 변경하는 명령은 실행되지 않았다.*
