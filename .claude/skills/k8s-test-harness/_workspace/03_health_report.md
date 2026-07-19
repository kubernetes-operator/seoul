# 헬스체크 보고서

- 점검 일시: 2026-07-19
- 점검자: k8s-health 에이전트 (읽기 전용 kubectl: get/describe/top/logs)
- 클러스터 구성: 8노드 (control-plane 3 / infra 2 / worker 3)
- 종합 판정: **정상 가동 중** — 노드/컨트롤플레인/워크로드 모두 Ready. 다만 **스토리지 관련 반복 이슈 1건(warning)** 과 **클러스터 전역 DNS 설정 경고(warning)** 확인.

---

## 노드 요약 (역할별)

모든 노드 `Ready`, 모든 압박 조건(Memory/Disk/PID/NetworkUnavailable) `False`. 이상 노드 없음.

| 역할 | 노드 | STATUS | 커널 | 런타임 | 테인트 | 비고 |
|------|------|--------|------|--------|--------|------|
| control-plane | playcekubectr01~03 | Ready | 6.8.0-106 | containerd 2.2.1 | control-plane:NoSchedule | 정상 |
| infra | infra01 | Ready | 6.8.0-107 | containerd 2.2.1 | 없음 | 정상 |
| infra | infra02 | Ready | 6.8.0-106 | containerd 2.2.1 | 없음 | 정상 |
| worker | playcekubewrk01 | Ready | 6.8.0-106 | containerd 2.2.1 | 없음 | 정상 |
| worker | playcekubewrk02 | Ready | **6.17.0-22** | containerd 2.2.1 | nvidia.com/gpu=rtx4060:NoSchedule | GPU 노드 |
| worker | playcekubewrk03 | Ready | **6.17.0-23** | containerd 2.2.1 | nvidia.com/gpu=rtx2070:NoSchedule | GPU 노드(65d, 신규) |

- **[info] 커널 버전 스큐**: 대부분 6.8.0 계열이나 GPU 워커 2대(wrk02/03)만 6.17.0 계열. 기능 이상은 없으나 커널 정책 일관성 관점에서 참고.
- 쿠버네티스 버전은 전 노드 `v1.34.3`로 일치.

---

## 컨트롤플레인 상태

| 컴포넌트 | 상태 | 비고 |
|----------|------|------|
| kube-apiserver | 3/3 Running (재시작 0) | 정상 |
| kube-scheduler | 3/3 Running | 재시작 3~5회(마지막 67~79일 전, 현재 안정) |
| kube-controller-manager | 3/3 Running | 재시작 4~5회(일부 3d17h 전) |
| etcd | 정적 파드 미노출 | 아래 참고 |
| componentstatuses | scheduler/controller-manager/etcd-0 모두 `Healthy` | (deprecated API지만 확인용) |

- **[info] etcd 파드 미노출**: `kube-system`에 etcd 정적 파드가 없음(apiserver/scheduler/controller-manager는 정적 파드로 존재). 외부 etcd 또는 systemd 관리 etcd 구성으로 추정. `componentstatuses`에서 `etcd-0 Healthy`로 도달성은 확인됨. 실제 etcd 멤버 헬스는 호스트/etcdctl 레벨 확인 필요(읽기 전용 kubectl 범위 밖).
- **[info] 컨트롤플레인 재시작 3d17h 전 상관관계**: controller-manager(ctr01/03), snapshot-controller, csi-nfs-controller 등의 마지막 재시작이 공통적으로 약 3d17h 전. 특정 시점 이벤트(노드 재부팅/네트워크 순단 등) 정황이나 현재는 모두 안정 Running.

---

## 워크로드 이상 목록 (심각도순)

현재 `Running/Succeeded` 아닌 파드 **0건**, 활성 CrashLoopBackOff/OOMKilled/Evicted **0건**. Deployment/StatefulSet/DaemonSet 준비 상태 전부 정상(desired=ready).

| 심각도 | 대상 | 내용 | 조치 방향 |
|--------|------|------|-----------|
| **warning** | PV `pvc-28386df5...b02d` (monitoring/data-timescaledb-0, 50Gi, nfs-nas-sc-main) | `Released` 상태로 방치 + `VolumeFailedDelete` **355회 반복**. NFS 마운트 실패(`mount failed: exit status 32`)로 프로비저너가 정리 불가. 원 워크로드(timescaledb 파드/PVC)는 이미 삭제됨 → 고아 PV가 스토리지 누수 + 이벤트 누적 유발. Reclaim=Delete. | **storage-backup 팀 인계**: NFS 서버 export/네트워크 확인, 정리 불가 시 PV 수동 처리 검토 |
| info | kube-system `csi-nfs-controller` | csi-provisioner 13회 / csi-snapshotter 11회 재시작(마지막 3d17h 전). 현재 4/4 Running. 위 NFS 이슈와 연관 가능. | 위 NFS 이슈 해소 후 재관찰 |
| info | nginx-gateway `nginx-gateway-78bb8f9c6c-tcmx6` | 누적 9회 재시작(lastState=Error), 현재 Running. | 활성 이슈 아님, 참고 |
| info | gpu-operator / kube-system operator 파드들 | NFD/gpu-operator/cilium-operator/snapshot-controller 등 누적 재시작 5~11회, 모두 현재 Running·95일 가동 중 누적치. | 활성 이슈 아님 |

> 참고: 초기 조회 시 DaemonSet 14종이 "not-ready"로 보였으나, 이는 판정에 `readyReplicas`(DaemonSet엔 없는 필드)를 쓴 오탐이며 `numberReady==desiredNumberScheduled`로 재확인 결과 전부 정상.

---

## 반복 이벤트 Top N

이벤트 보존 윈도우 내 Warning 집계.

| 순위 | reason | 횟수 | 대상 | 판정 |
|------|--------|------|------|------|
| 1 | `VolumeFailedDelete` | **355회** (단일 PV) | monitoring/data-timescaledb-0 PV | **반복 패턴(warning)** — 위 워크로드 이상 참조 |
| 2 | `DNSConfigForming` | 8개 파드(다수 네임스페이스) | speaker, kube-proxy, cilium, cilium-envoy, nodelocaldns, node-exporter, cilium-test perf-* 등 | **반복 패턴(warning)** — 아래 참조 |

- **[warning] DNSConfigForming 전역 발생**: "Nameserver limits were exceeded, some nameservers have been omitted" — 적용 라인 `169.254.25.10 168.126.63.1 168.126.63.1`. **업스트림 `168.126.63.1`이 중복** 기재되어 nameserver 3개 제한에 걸려 일부가 누락됨. 네임스페이스/노드 무관하게 광범위 발생 → 클러스터 DNS(resolv.conf/nodelocaldns 업스트림) 설정 정리 필요. 현재 DNS 해석 자체 실패 징후는 없어 심각도 warning(설정 위생).
- 그 외 1~2회성 노이즈 이벤트 없음(윈도우 내 Warning은 위 2종이 전부).

---

## 리소스 사용량

`kubectl top` 정상 동작(metrics-server 가용). 전 노드 여유 충분, 리소스 압박 없음.

| 노드 | CPU | MEM |
|------|-----|-----|
| infra01 | 8% | 7% |
| infra02 | 2% | 30% |
| playcekubectr01 | 2% | 28% |
| playcekubectr02 | 5% | 39% |
| playcekubectr03 | 4% | 32% |
| playcekubewrk01 | 3% | 35% |
| playcekubewrk02 | 0% | 12% |
| playcekubewrk03 | 0% | 10% |

- HPA: 없음(`No resources found`) — 오토스케일링 미구성. 용량 부족 신호 없음.
- PDB: gitea-postgresql / gitea-valkey-cluster 2건(maxUnavailable=1, ALLOWED DISRUPTIONS=1) — 드레인 영구 차단 위험 없음.
- ResourceQuota: 없음.

---

## 협업 인계 사항

- **storage-backup 팀**: `VolumeFailedDelete` 355회 / 고아 PV(monitoring/data-timescaledb-0, nfs-nas-sc-main) 및 csi-nfs-controller NFS 마운트 실패(exit status 32) — PV/PVC·NFS export 상태 점검 요청.
- **observability/network 팀**: 전역 `DNSConfigForming`(업스트림 168.126.63.1 중복, nameserver 3개 초과) — nodelocaldns/resolv.conf 업스트림 구성 정리 검토.
- **resilience 팀**: 현재 활성 헬스 이상 워크로드 없음. 단, csi-nfs-controller 및 nginx-gateway는 재시작 이력 보유 노드로 장애 주입 시 주의.
- **권한(RBAC) 이슈 없음** — 모든 리소스 조회 정상(403 없음).
