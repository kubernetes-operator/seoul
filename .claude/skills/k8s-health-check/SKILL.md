---
name: k8s-health-check
description: "Kubernetes 노드/컨트롤플레인/워크로드 헬스 진단 절차. 노드 컨디션, 리소스 압박, 파드 재시작, HPA/PDB, 이벤트 로그를 점검할 때 사용. '클러스터 상태 확인', '노드 점검', '파드 왜 자꾸 재시작되는지' 같은 요청에도 사용할 것."
---

# K8s 헬스체크 절차

클러스터 인프라와 워크로드 헬스를 체계적으로 점검하는 절차. 모든 명령은 읽기 전용이다.

## 1. 노드 레벨

```
kubectl get nodes -o wide
kubectl describe nodes | grep -A5 "Conditions:"
kubectl get nodes -o json | jq '.items[] | {name:.metadata.name, taints:.spec.taints}'
```

Ready 외 조건(MemoryPressure/DiskPressure/PIDPressure/NetworkUnavailable)이 True인 노드가 있으면 critical로 분류한다. 노드 역할(control-plane/infra/worker)별로 그룹화해 표로 정리하면 대규모 클러스터에서 가독성이 높아진다.

## 2. 컨트롤플레인

관리형 컨트롤플레인(EKS/GKE 등)이면 `kube-system`에 API서버/etcd 파드가 없을 수 있다 — 이 경우 클라우드 콘솔/매니지드 서비스 상태로 대체 확인이 필요함을 보고서에 명시한다. 자체 구축(kubeadm 등)이면:

```
kubectl get pods -n kube-system -l component=kube-apiserver
kubectl get pods -n kube-system -l component=etcd
kubectl get pods -n kube-system -l component=kube-scheduler
kubectl get pods -n kube-system -l component=kube-controller-manager
kubectl get componentstatuses 2>/dev/null  # deprecated, 실패해도 무시
```

## 3. 워크로드 헬스

```
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded
kubectl get pods -A -o json | jq '.items[] | select(.status.containerStatuses[]?.restartCount > 3) | {ns:.metadata.namespace, name:.metadata.name, restarts:.status.containerStatuses[].restartCount}'
kubectl get deploy,sts,ds -A -o json | jq '.items[] | select(.status.replicas != .status.readyReplicas) | {ns:.metadata.namespace, name:.metadata.name}'
```

재시작 카운트가 높은 파드는 `kubectl describe pod`와 이전 컨테이너 로그(`kubectl logs --previous`)로 원인을 짚는다. CrashLoopBackOff/OOMKilled/Evicted를 구분해 기록한다.

## 4. HPA / PDB / ResourceQuota

```
kubectl get hpa -A
kubectl get pdb -A
kubectl get resourcequota -A
```

HPA가 목표 대비 항상 최대치에 걸려있으면 용량 부족 신호로 별도 표시한다. PDB의 `minAvailable`/`maxUnavailable`이 실제 레플리카 수와 모순되면(예: 레플리카 1개에 minAvailable 1) 드레인 시 영구 차단 가능성으로 기록 — resilience 테스트 계획에 영향을 준다.

## 5. 이벤트 스캔

```
kubectl get events -A --sort-by=.lastTimestamp | grep -i warning
```

동일한 (namespace, reason, object) 조합이 반복되면 "반복 패턴"으로 승격한다. 1~2회성 이벤트는 참고 목록에만 남긴다.

## 6. 리소스 사용량 (선택)

```
kubectl top nodes
kubectl top pods -A --sort-by=memory
```

metrics-server 부재로 실패하면(`error: Metrics API not available`) 실패 사실만 기록하고, 관측성 에이전트에게 Prometheus 기반 대체 지표를 요청한다.

## 보고서 구조

```
# 헬스체크 보고서
## 노드 요약 (역할별 표, 이상 노드만 상세)
## 컨트롤플레인 상태
## 워크로드 이상 목록 (심각도순)
## 반복 이벤트 Top N
## 리소스 사용량 (또는 수집 실패 사유)
```
