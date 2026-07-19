---
name: k8s-network-check
description: "Kubernetes 네트워킹(Cilium CNI, NetworkPolicy, Service/Ingress/Gateway, MetalLB, DNS) 검증 절차. 연결성 문제, 정책 커버리지, 외부 노출 경로를 점검할 때 사용. 'Cilium 상태 확인', '이 서비스 왜 접속이 안되는지', 'NetworkPolicy 점검' 요청에도 사용할 것."
---

# K8s 네트워크 검증 절차

Cilium CNI, MetalLB, Gateway/Ingress 환경의 네트워킹을 점검하는 절차.

## 1. CNI (Cilium) 상태

```
kubectl -n kube-system get pods -l k8s-app=cilium
kubectl -n kube-system exec ds/cilium -- cilium status --brief
kubectl -n kube-system exec ds/cilium -- cilium status | grep -A3 "Cluster health"
```

Cilium 에이전트가 일부 노드에서만 NotReady면 해당 노드 전체의 네트워킹이 불안정할 수 있다 — health 에이전트에게 해당 노드 상태를 교차 확인 요청한다.

## 2. NetworkPolicy 커버리지

```
kubectl get networkpolicy -A
kubectl get ciliumnetworkpolicy -A 2>/dev/null
```

정책이 하나도 없는 네임스페이스 목록을 만들고, `kube-system` 등 시스템 네임스페이스는 별도 표시한다. **정책 부재를 즉시 취약점으로 단정하지 말 것** — "기본 all-allow, 의도 여부 확인 필요"로 중립 기술한다.

## 3. Service / LoadBalancer (MetalLB)

```
kubectl get svc -A -o wide | grep LoadBalancer
kubectl get ipaddresspool -n metallb-system
kubectl get l2advertisement -n metallb-system
```

LoadBalancer 타입 Service의 `EXTERNAL-IP`가 `<pending>`이면 IPAddressPool 고갈이나 L2Advertisement 미매칭을 의심한다. 풀의 할당 가능 IP 수와 현재 사용 중인 LB Service 수를 비교한다.

## 4. Ingress / Gateway API

```
kubectl get ingress -A
kubectl get gateway,httproute -A 2>/dev/null
kubectl get certificate -A  # cert-manager
```

Gateway/HTTPRoute의 백엔드 Service가 실제로 존재하고 엔드포인트가 있는지(`kubectl get endpoints <svc>`) 확인한다. cert-manager Certificate 리소스의 `READY` 상태가 False면 발급 실패 원인(`kubectl describe certificate`)을 짚는다.

## 5. DNS / 연결성 테스트 (임시 파드, 비파괴적)

```
kubectl run netcheck --rm -it --restart=Never --image=nicolaka/netshoot -- \
  sh -c "nslookup kubernetes.default; nslookup <타겟서비스>.<네임스페이스>.svc.cluster.local"
```

`--rm`으로 자동 정리되도록 하되, 세션이 비정상 종료되면 `kubectl get pod netcheck`로 잔존 여부를 반드시 확인하고 수동 삭제한다. PodSecurity 정책으로 임의 이미지 실행이 막히면(에러 메시지에 admission 관련 문구) 해당 네임스페이스는 "제약됨"으로 기록하고 `default` 등 완화된 네임스페이스에서 재시도한다.

## 6. Hubble (있는 경우)

```
kubectl -n kube-system get pods -l k8s-app=hubble-relay
cilium hubble observe --namespace <ns> --verdict DENIED -n 50
```

DENIED 플로우가 있으면 어떤 정책에 의해 차단되었는지와 의도된 차단인지를 함께 기록한다.

## 보고서 구조

```
# 네트워크 검증 보고서
## Cilium/CNI 상태
## NetworkPolicy 커버리지 (네임스페이스별 표)
## LoadBalancer/MetalLB 할당 현황
## Ingress/Gateway 라우팅 및 인증서 상태
## DNS/연결성 테스트 결과 (생성/삭제한 테스트 리소스 명시)
```
