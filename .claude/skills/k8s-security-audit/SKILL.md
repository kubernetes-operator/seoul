---
name: k8s-security-audit
description: "Kubernetes RBAC, Pod Security, 시크릿 위생, 인증서/이미지 보안 감사 절차. 과도한 권한, 특권 컨테이너, 정책 미비를 점검할 때 사용. '보안 점검', 'RBAC 감사', '누가 cluster-admin 권한 있는지' 요청에도 사용할 것. 시크릿 값 자체는 절대 조회하지 않는다."
---

# K8s 보안 감사 절차

RBAC, Pod Security, 시크릿 위생을 감사하는 절차. **시크릿 데이터 값 조회는 이 스킬의 어떤 단계에서도 수행하지 않는다.**

## 1. RBAC 감사

```
kubectl get clusterrolebindings -o json | jq '.items[] | select(.roleRef.name=="cluster-admin") | {name:.metadata.name, subjects:.subjects}'
kubectl get clusterroles -o json | jq '.items[] | select(.rules[]?.resources[]? == "*" and .rules[]?.verbs[]? == "*") | .metadata.name'
kubectl get rolebindings -A -o json | jq '.items[] | select(.roleRef.name=="cluster-admin")'
```

`system:` 접두 기본 제공 바인딩은 시스템 컴포넌트로 분류해 요약만 남긴다. 사용자/서비스어카운트가 직접 cluster-admin에 바인딩된 사례를 우선순위로 보고한다.

## 2. Pod Security

```
kubectl get pods -A -o json | jq '.items[] | select(.spec.hostNetwork==true or .spec.hostPID==true or .spec.hostIPC==true) | {ns:.metadata.namespace, name:.metadata.name}'
kubectl get pods -A -o json | jq '.items[] | select(.spec.containers[]?.securityContext?.privileged==true) | {ns:.metadata.namespace, name:.metadata.name}'
kubectl get namespaces -o json | jq '.items[] | {name:.metadata.name, pss:.metadata.labels}'  # pod-security.kubernetes.io 라벨 확인
```

시스템 네임스페이스(kube-system, cilium-secrets, gpu-operator 등)는 정당한 특권 사용이 흔하므로 낮은 가중치로, 사용자 네임스페이스(demo-app, model-factory 등)의 특권 파드는 높은 가중치로 분류한다.

## 3. ServiceAccount / 토큰 자동 마운트

```
kubectl get sa -A -o json | jq '.items[] | select(.automountServiceAccountToken != false) | {ns:.metadata.namespace, name:.metadata.name}'
```

파드 스펙 레벨에서 재정의됐을 수 있으므로, SA 레벨 결과는 "잠재적 위험"으로만 표시하고 실제 파드의 마운트 여부는 필요 시 개별 확인한다.

## 4. 시크릿 위생 (메타데이터만)

```
kubectl get secrets -A -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,TYPE:.type,AGE:.metadata.creationTimestamp
```

**금지:** `-o yaml`, `-o json`(전체), `-o jsonpath='{.data}'` 등 `data`/`stringData` 필드를 노출하는 모든 조회. 타입(`kubernetes.io/dockerconfigjson`, `Opaque` 등)과 생성일만으로 위생을 평가한다 — 예: 1년 이상 로테이션되지 않은 `Opaque` 시크릿은 "로테이션 검토 대상"으로 표시.

## 5. 인증서 만료 (cert-manager)

```
kubectl get certificate -A -o json | jq '.items[] | {ns:.metadata.namespace, name:.metadata.name, notAfter:.status.notAfter, ready:(.status.conditions[]? | select(.type=="Ready") | .status)}'
```

`notAfter`까지 30일 이내는 긴급, 90일 이내는 경고로 구간화한다.

## 6. 이미지 위생

```
kubectl get pods -A -o json | jq '.items[].spec.containers[].image' | sort -u | grep -E ':latest$|^[^:@]+$'
```

태그 미지정 또는 `:latest` 사용 이미지를 나열한다. 다이제스트(`@sha256:`) 고정 여부도 함께 확인한다.

## 보고서 구조

```
# 보안 감사 보고서
## RBAC 위험 (cluster-admin 바인딩, 와일드카드 권한)
## Pod Security 위반 (네임스페이스별, 시스템/사용자 구분)
## ServiceAccount 토큰 자동마운트 현황
## 시크릿 메타데이터 요약 (값 미포함)
## 인증서 만료 현황 (긴급/경고/정상)
## 이미지 위생 (latest 태그, 다이제스트 미고정)
```
