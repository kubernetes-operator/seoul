---
name: k8s-storage-backup-check
description: "Kubernetes 스토리지(PV/PVC/StorageClass)와 Velero 백업/복구 체계 검증 절차. 바인딩 실패, 백업 성공/실패 이력, MinIO 백엔드 헬스를 점검할 때 사용. 'PVC가 Pending', '백업 잘 되고 있는지', 'Velero 상태 확인' 요청에도 사용할 것. 실제 복구 실행은 승인 없이 하지 않는다."
---

# K8s 스토리지/백업 검증 절차

PV/PVC 및 Velero+MinIO 백업 체계를 점검하는 절차. **복구(restore) 실행, 백업 생성, PV/PVC 삭제 등 상태 변경 명령은 사용자 승인 전까지 실행하지 않는다.**

## 1. PV / PVC / StorageClass

```
kubectl get pv -o wide
kubectl get pvc -A -o wide
kubectl get storageclass
kubectl get pvc -A --field-selector=status.phase!=Bound
```

Pending 상태 PVC는 `kubectl describe pvc <name> -n <ns>`의 Events로 원인(프로비저너 오류, StorageClass 불일치, 용량 부족)을 짚는다. reclaimPolicy가 `Delete`인 StorageClass를 사용하는 PV 중 중요 데이터로 보이는 것(DB, 백업 관련 네임스페이스)은 별도 표시한다.

## 2. Velero 백업 이력

```
kubectl get backups.velero.io -n velero -o wide
kubectl get schedules.velero.io -n velero
kubectl get backups.velero.io -n velero -o json | jq '.items[] | select(.status.phase != "Completed") | {name:.metadata.name, phase:.status.phase}'
```

Velero CLI가 있으면 `velero backup get`, `velero backup describe <name>`으로 더 상세한 실패 원인을 확인한다. 없으면 CRD 조회로 대체한다.

최근 성공 백업 시점과 현재 시각의 격차를 계산해 RPO(복구목표시점) 관점에서 평가한다 — 스케줄된 주기보다 훨씬 오래 성공 백업이 없으면 긴급으로 분류한다.

## 3. MinIO 백엔드 헬스

```
kubectl get pods,svc -n velero -l app=minio 2>/dev/null
kubectl get pods -n minio 2>/dev/null
kubectl logs -n velero deploy/velero --tail=100 | grep -i error
```

Velero 파드 로그에서 백엔드(MinIO) 연결 오류(`connection refused`, `NoSuchBucket` 등)를 스캔한다.

## 4. 복구 드릴 제안 (실행 금지 — 제안만)

실제 복구 가능 여부를 검증하려면 다음 절차를 **제안서**로만 작성하고 실행하지 않는다:

```
# 제안 절차 예시 (실행하지 말 것)
velero restore create --from-backup <backup-name> --namespace-mappings <원본ns>:<임시테스트ns>
```

제안서에는 대상 백업, 예상 소요 시간, 복구 대상을 원본이 아닌 임시 네임스페이스로 격리하는 방법, 실패 시 정리 절차를 포함한다. 사용자/오케스트레이터의 명시적 승인이 있을 때만 이 절차를 실행하며, 실행 시에도 원본 네임스페이스가 아닌 임시 네임스페이스로 제한한다.

## 보고서 구조

```
# 스토리지/백업 검증 보고서
## PV/PVC 상태 (Pending/이상만 상세)
## StorageClass 현황
## Velero 백업 이력 및 RPO 평가
## MinIO 백엔드 헬스
## 복구 드릴 제안 (승인 대기 / 승인됨-실행결과 / 해당없음)
```
