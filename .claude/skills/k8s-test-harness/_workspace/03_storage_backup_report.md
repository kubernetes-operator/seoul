# 스토리지/백업 검증 보고서

- 점검 일시: 2026-07-19
- 점검자: k8s-storage-backup 에이전트 (조회 전용 — 상태 변경 명령 미실행)
- 클러스터: kubernetes:6443 (8노드, v1.34.3 / 3 control-plane, 2 infra, 3 worker — 전부 Ready)
- 백엔드: Velero + MinIO (velero 네임스페이스), 스토리지 프로비저너 nfs.csi.k8s.io (NFS 192.168.200.200)

---

## 위험도 요약 (심각 순)

| 심각도 | 항목 | 요지 |
|--------|------|------|
| 🔴 P0 (심각) | Velero 백업 전건 실패 | 최근 27일 내 daily/weekly/monthly 백업 15건 **전부 `FailedValidation`** — 유효 복구 지점 0개, RPO 사실상 무한 |
| 🟠 P2 (경고) | Released PV 삭제 무한 실패 | `monitoring/data-timescaledb-0` PV가 Released 상태로 NFS mount 실패하며 21시간째 삭제 재시도(x355) |
| 🟡 P3 (주의) | `minio` 네임스페이스 유령 PVC | 워크로드(파드/STS) 0개인데 500Gi PVC 7개(≈3.5TB) Bound 유지 — 방치 스토리지 의심 |

---

## PV/PVC 상태 (Pending/이상만 상세)

- PV 총 33개: **Bound 32 / Released 1**
- PVC 총 32개: **전부 Bound** (Pending/미바인딩 PVC 없음)
- 참고: 이 클러스터는 `status.phase` field-selector를 지원하지 않아 `-o json`+jq로 대체 조회함.

### 이상 항목 — Released PV (삭제 실패)
- PV: `pvc-28386df5-42f6-48c0-8e6f-4d672989b02d` (50Gi, RWO, SC=nfs-nas-sc-main, reclaimPolicy=**Delete**)
- 원래 클레임: `monitoring/data-timescaledb-0` (해당 PVC는 이미 삭제됨 → PV가 Released 로 잔류)
- 이벤트: `VolumeFailedDelete` — `2m38s(x355 over 21h)`
  - 메시지: `failed to mount nfs server ... mount.nfs: mount system call failed (exit status 32)`
- 해석: TimescaleDB가 `monitoring` → `os-monitor` 네임스페이스로 이전된 것으로 보임(현재 `os-monitor/data-timescaledb-0` PVC가 42h째 Bound). 구 PV의 자동 삭제(reclaim=Delete)가 NFS 마운트 실패로 계속 실패 중.
- 영향: 데이터 유실 위험은 낮으나(구 데이터), NFS 서버측 마운트/공유 접근 문제 신호일 수 있음. **health 팀원에게 NFS 서버(192.168.200.200) 공유 `/c/kubernetes/storageclass` 마운트 가용성 교차 확인 요청 권고.**

### 중요 데이터 PV (reclaimPolicy=Delete 주의 대상)
모든 StorageClass가 reclaimPolicy=**Delete**이므로 아래 데이터성 PV는 PVC 실수 삭제 시 데이터가 함께 삭제됨 — 백업 의존도 높음(그러나 아래 백업이 전부 실패 중이라 안전망 부재):
- DB류: `demo-app/postgres-pvc`, `model-factory/postgresql-pvc`(20Gi), `gitea/data-gitea-postgresql-*`, `gitea/data-gitea-postgresql-ha-postgresql-*`
- 로그/모니터링: `logging/storage-loki-stack-0`, `monitoring/prometheus-...-db-*`, `os-monitor/data-timescaledb-0`
- 오브젝트 스토리지 데이터: `minio/*`, `velero/export-*`(MinIO 백엔드 자체)

## StorageClass 현황

| 이름 | 프로비저너 | reclaimPolicy | 바인딩모드 | 확장허용 | 기본 |
|------|-----------|---------------|-----------|---------|------|
| nfs-nas-sc | nfs.csi.k8s.io | Delete | Immediate | true | - |
| nfs-nas-sc-main | nfs.csi.k8s.io | Delete | Immediate | true | ✅ default |

- 사용 중인 PV는 전부 `nfs-nas-sc-main`. `nfs-nas-sc`는 사용 PV 없음.
- 주의: 두 SC 모두 reclaimPolicy=Delete → 운영 데이터 보호를 위해 DB 등 중요 볼륨은 Retain 정책 SC 사용을 검토 권고.

## Velero 백업 이력 및 RPO 평가

### 🔴 결정적 문제: 백업 전건 FailedValidation
스케줄 3종 모두 Enabled 이고 최근까지 백업 리소스는 생성되나, **생성된 백업 15건 전부 `FailedValidation` 으로 실제 데이터 백업이 한 건도 수행되지 않음** (`Completed` 0건, errors/warnings 0 — 검증 단계에서 즉시 탈락).

- daily(`velero-daily-app-backup`, `0 18 * * *`): 2026-07-12~07-18 7건 전부 FailedValidation
- weekly(`velero-weekly-backup`, `1 19 * * 0`): 06-21~07-12 4건 전부 FailedValidation
- monthly(`velero-monthly-full-backup`, `3 19 * * 0`): 06-21~07-12 4건 전부 FailedValidation

### 근본 원인 (검증 에러 메시지 원문)
```
provider aws has more than one possible volume snapshot location,
and none were specified explicitly or as a default
```
- VolumeSnapshotLocation(VSL)이 `default`, `volume-backup` **2개** 존재하고 둘 다 provider=`aws`.
- 어느 것도 기본(default)으로 지정되지 않았고, 3개 스케줄 template 모두 `volumeSnapshotLocations` 미지정(null).
- 백업 spec은 `snapshotVolumes: true` 이므로 Velero가 VSL을 하나로 특정하지 못해 **백업 실행 전 검증에서 실패**.
- 참고: daily 백업은 `defaultVolumesToFsBackup: true`(node-agent 파일시스템 백업)라 VSL 없이도 볼륨 데이터 백업이 가능하나, `snapshotVolumes:true` + 다중 VSL 조합 탓에 검증 자체를 통과하지 못함.

### 백엔드는 정상 (문제는 순수 설정 오류)
- BackupStorageLocation 4종 전부 `Available`, 최근 재검증 성공(수 초 전): `daily-backup`, `weekly-backup`, `monthly-backup`, `default`(기본, bucket=osc-backup), 전부 s3Url=`http://minio:9000`.
- 즉 MinIO 연결·버킷은 문제 없음 → **VSL 설정만 바로잡으면 백업 정상화 가능.**

### RPO 평가 — 🔴 긴급
- 유효(Completed) 복구 지점: **0개**. 보존 창(최소 27일) 내 성공 백업 전무.
- 기대 RPO: daily 24h / weekly·monthly 주기. **실제 RPO: 무한(복구 불가)**.
- 결론: 현재 어떤 네임스페이스도 Velero로 복구 불가. resilience 팀의 파괴적 테스트 전 안전망이 존재하지 않음 → **파괴적 테스트 착수 금지 권고**.

### 권고 조치 (상태 변경이므로 미실행 — 승인 필요)
아래 중 택1 (Velero 서버/스케줄 수정 = 상태 변경, 승인 전 미실행):
1. Velero 서버에 기본 VSL 지정: `--default-volume-snapshot-locations aws:default`
2. 또는 각 Schedule의 `spec.template.volumeSnapshotLocations`에 `["default"]` 명시
3. (VSL이 실제로 불필요하고 fs 백업만 사용한다면) `snapshotVolumes:false` + `defaultVolumesToFsBackup:true`로 통일

## MinIO 백엔드 헬스

- Velero 백엔드 MinIO(velero 네임스페이스): `minio-0`, `minio-1` 모두 `Running`(70d, 재시작 0). svc `minio:9000`(ClusterIP), `minio-console`(NodePort 32039) 정상.
- Velero 컨트롤러 `velero-7c5cf65c87-rt24h` Running(67d), node-agent 5개 전부 Running.
- Velero 파드 로그: connection refused / NoSuchBucket 등 백엔드 오류 **없음**(에러 라인은 정보성 1건뿐).
- 결론: **백엔드 헬스 양호.** 백업 실패는 백엔드가 아니라 VSL 설정 오류가 원인.

### 별도 `minio` 네임스페이스 (백엔드와 무관 — 방치 의심)
- PVC 7개 Bound(500Gi×7 ≈ 3.5TB): `export-minio-0~2`, `export-0-minio-0/1`, `export-1-minio-0/1`.
- 그러나 해당 네임스페이스에 **파드/서비스/StatefulSet/Deployment 0개** (`kubectl get all -n minio` → No resources found).
- 해석: 애플리케이션용 별도 MinIO가 제거되었으나 PVC(≈3.5TB)만 잔류. reclaimPolicy=Delete라 PVC 삭제 시 데이터 소멸 주의. 의도된 보존인지 방치인지 확인 필요.

## 복구 드릴 제안 (승인 대기 — 단, 현재 실행 불가/차단)

**상태: 실행 차단(BLOCKED).** 복구 드릴의 전제인 `Completed` 백업이 0개이므로 지금은 드릴 자체가 불가능합니다. 아래 순서로 진행 권고:

1. (선행) 위 VSL 설정 오류 수정 → 스케줄이 1회 이상 `Completed` 백업 생성 확인.
2. (그 후) 아래 드릴 제안서로 복구 검증. **모두 승인 전 미실행.**

### 드릴 제안서 (성공 백업 확보 후 적용)
- 대상 백업: 수정 후 최초로 `Completed` 된 daily 백업 1건.
- 검증 방식: 원본이 아닌 **임시 네임스페이스로 격리 복구** (예: `default` → `restore-drill`).
```
# 실행 금지 — 승인 후, 성공 백업 확보 후에만
velero restore create drill-<date> \
  --from-backup <completed-backup-name> \
  --namespace-mappings default:restore-drill
```
- 예상 소요: 대상 데이터량에 따라 수 분~수십 분(daily 대상 ns = monitoring/logging/default).
- 격리: `--namespace-mappings`로 임시 ns에만 복원, 운영 워크로드 무영향.
- 실패/정리: 검증 후 `kubectl delete ns restore-drill`로 임시 ns 일괄 삭제(정리도 승인 후 수행).
- 롤백: 임시 ns만 사용하므로 원본 롤백 불필요.

---

## 팀 통신 (권고)
- → resilience 팀원: **유효 백업 0건 — 파괴적 테스트 전 안전망 없음, 착수 보류 요청.**
- → health 팀원: NFS 서버(192.168.200.200, 공유 `/c/kubernetes/storageclass`) 마운트 가용성 및 Released PV 삭제 실패(exit 32) 원인 교차 확인 요청.
- → observability 팀원: Velero 백업 전건 실패가 Alertmanager 알림으로 잡히는지(백업 실패 알림 룰 존재 여부) 교차 확인 요청 — 27일간 무알림이었다면 알림 룰 누락 의심.

## 실행한 명령 (전부 조회 전용)
kubectl get nodes/pv/pvc/storageclass, kubectl describe pv(Released), kubectl get backups.velero.io/schedules.velero.io/backupstoragelocations/volumesnapshotlocations, kubectl get pods,svc/all -n {velero,minio}, kubectl logs deploy/velero(조회). **백업 생성·복구·삭제 등 상태 변경 명령 미실행.**
