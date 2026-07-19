---
name: k8s-resilience-test
description: "Kubernetes 장애 주입/카오스/복원력 테스트 절차 — 파드 삭제, 노드 코든/드레인, PDB 위반, HPA 부하 테스트. '카오스 테스트', '장애 시뮬레이션', '자가치유 확인', '노드 죽으면 어떻게 되는지 테스트' 요청에 사용. 모든 파괴적 단계는 실행 직전 반드시 사용자 승인을 받는다 — 예외 없음."
---

# K8s 복원력/카오스 테스트 절차

**이 스킬의 모든 절차는 클러스터 상태를 실제로 바꾸는 파괴적 작업이다.** 각 섹션은 "제안 → 승인 확인 → 실행 → 원상복구 확인" 4단계를 반드시 거친다. 승인 없이 2단계를 건너뛰지 않는다.

## 0. 사전 준비 (모든 테스트 공통)

1. 대상 워크로드/노드가 health 에이전트의 "이상 있는 대상 제외" 목록에 있는지 확인
2. 대상이 운영 워크로드인지 테스트/데모 워크로드인지 확인하고, 운영으로 보이면 반드시 사용자에게 재확인
3. storage-backup 에이전트에게 대상과 관련된 최신 백업 존재 여부 확인
4. observability 에이전트에게 장애 주입 시점을 공유해 알림/지표 반응을 함께 관찰하도록 요청

## 1. 파드 삭제 → 자가치유 확인

**제안 예시:** "`demo-app` 네임스페이스의 파드 1개를 삭제하여 Deployment가 자동 재생성하는지 확인하겠습니다. 예상 다운타임: 서비스 레플리카가 2개 이상이면 무중단."

승인 후:
```
kubectl delete pod <pod-name> -n <ns>
kubectl get pods -n <ns> -w  # 재생성 확인, 일정 시간 후 Ctrl-C
```
재생성 시간과 최종 상태(Running/CrashLoopBackOff 등)를 기록한다.

## 2. 노드 코든/드레인 → 재스케줄링 확인

**제안 예시:** "워커 노드 `playcekubewrk0X`를 cordon+drain하여 워크로드가 다른 노드로 재스케줄링되는지 확인하겠습니다. 해당 노드의 파드들은 일시적으로 재시작됩니다."

승인 후:
```
kubectl cordon <node>
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=120s
# 재스케줄링 확인
kubectl get pods -A -o wide | grep <node 대신 이동한 노드들>
# 원상복구
kubectl uncordon <node>
```
드레인이 PDB에 의해 차단되면(타임아웃/에러) 이를 "PDB가 정상적으로 보호 중"으로 긍정적으로 기록하고, 무리하게 강제하지 않는다. 컨트롤플레인 노드(`playcekubectr01~03`)는 드레인 대상에서 제외한다 — 쿼럼 손실 위험.

## 3. PodDisruptionBudget 위반 테스트

health 에이전트가 파악한 PDB 설정을 참고해, `minAvailable`을 위반하는 수의 파드를 동시에 드레인 시도한다. 목적은 PDB가 실제로 드레인을 차단하는지 확인하는 것 — 차단되면 성공적인 테스트 결과다.

## 4. HPA 부하 테스트

**제안 예시:** "`<서비스>`에 합성 부하를 발생시켜 HPA가 스케일 아웃하는지 확인하겠습니다."

승인 후 임시 부하 생성 파드를 사용:
```
kubectl run load-gen --rm -it --restart=Never --image=busybox -- \
  /bin/sh -c "while true; do wget -q -O- http://<svc>.<ns>.svc.cluster.local; done"
```
HPA 리소스(`kubectl get hpa -w`)로 스케일 아웃 반응 시간을 기록하고, 테스트 종료 시 부하 생성 파드를 확실히 종료(`--rm` 또는 수동 delete)한다.

## 5. LoadBalancer/Gateway Failover 관찰

네트워크 에이전트가 파악한 서비스의 백엔드 파드 중 일부를 의도적으로 재시작(delete)하여 MetalLB/Gateway가 나머지 정상 파드로 트래픽을 유지하는지 관찰한다. 완전한 서비스 중단(모든 백엔드 동시 삭제)은 절대 제안하지 않는다.

## 보고서 구조

```
# 복원력 테스트 보고서
## 테스트 항목별 [제안 → 승인여부 → 실행결과 → 원상복구 확인]
## 승인되지 않아 미실행된 항목 (사유 명시)
## 발견된 취약점 (자가치유 실패, PDB 미설정, HPA 반응 지연 등)
```
