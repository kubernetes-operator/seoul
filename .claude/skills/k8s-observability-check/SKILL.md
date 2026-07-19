---
name: k8s-observability-check
description: "Kubernetes 관측성 스택(Prometheus, Alertmanager, Grafana, Loki) 검증 절차. 스크레이프 타겟 헬스, 알림 발화, 로그 수집 정상 동작을 점검할 때 사용. '모니터링 잘 되고 있는지', 'Prometheus 타겟 다운', '알림 왜 안 오는지' 요청에도 사용할 것."
---

# K8s 관측성 스택 검증 절차

kube-prometheus-stack과 loki-stack의 헬스를 점검하는 절차. 모두 읽기 전용 조회다.

## 1. Prometheus 타겟 헬스

```
kubectl -n monitoring port-forward svc/prometheus-stack-kube-prom-prometheus 9090:9090 &
curl -s localhost:9090/api/v1/targets | jq '.data.activeTargets[] | select(.health!="up") | {job:.labels.job, instance:.labels.instance, error:.lastError}'
```

port-forward는 점검 종료 후 반드시 `kill %1` 등으로 종료한다. Down 상태 타겟은 ServiceMonitor 라벨 셀렉터 불일치, 네임스페이스 네트워크 정책 차단, 대상 파드 다운 중 원인을 좁혀 기록한다.

## 2. Alertmanager 상태

```
curl -s localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.state=="firing") | {alertname:.labels.alertname, severity:.labels.severity}'
kubectl -n monitoring get alertmanager -o yaml
```

Firing 알림이 전혀 없다고 "정상"으로 단정하지 말 것 — 알림 규칙 자체가 비어있거나(`kubectl get prometheusrules -A`) Alertmanager 라우팅이 끊긴 침묵 실패 가능성을 함께 확인한다.

## 3. Grafana 헬스

```
kubectl -n monitoring get pods -l app.kubernetes.io/name=grafana
kubectl -n monitoring exec deploy/prometheus-stack-grafana -- curl -s localhost:3000/api/health
```

인증이 필요해 API 응답 검증이 막히면 파드/서비스 레벨 헬스까지만 확인하고 "대시보드 내용 검증은 접근 제약으로 미수행"으로 명시한다.

## 4. Loki 인제스천 헬스

```
kubectl -n logging get pods -l app=loki
kubectl -n logging port-forward svc/loki 3100:3100 &
curl -s -G localhost:3100/loki/api/v1/query --data-urlencode 'query={namespace=~".+"}' --data-urlencode 'limit=1' | jq '.data.result[0].values[0][0]'
```

반환된 최신 타임스탬프가 현재 시각과 크게 벌어져 있으면(수 분 이상) 인제스천 지연/중단으로 기록한다.

## 5. 스크레이프 커버리지 갭

```
kubectl get servicemonitor,podmonitor -A
kubectl get namespaces -o name | sed 's|namespace/||'  # 전체 네임스페이스와 위 결과 비교
```

ServiceMonitor/PodMonitor가 전혀 없는 사용자 네임스페이스를 커버리지 갭으로 표시한다.

## 보고서 구조

```
# 관측성 검증 보고서
## Prometheus 타겟 헬스 (Down 목록 + 원인 추정)
## Alertmanager 상태 (Firing 목록 + 침묵 실패 점검 결과)
## Grafana 헬스
## Loki 인제스천 상태
## 스크레이프 커버리지 갭 (네임스페이스별)
```
