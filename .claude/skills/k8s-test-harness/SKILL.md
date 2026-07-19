---
name: k8s-test-harness
description: "설치된 Kubernetes 클러스터를 다방면(헬스/네트워크/보안/스토리지-백업/관측성/복원력)으로 종합 테스트하는 에이전트 팀 오케스트레이터. '쿠버네티스 테스트해줘', 'k8s 클러스터 점검', '클러스터 상태 종합 점검', '카오스 테스트 돌려줘' 요청 시 사용. 후속 작업: 이전 결과 기반 재테스트, 특정 영역(네트워크/보안 등)만 다시 점검, 하네스 팀 구성 업데이트, 보고서 보완 요청 시에도 반드시 이 스킬을 사용."
---

# K8s Test Harness Orchestrator

Kubernetes 클러스터를 6개 전문 영역(헬스/네트워크/보안/스토리지-백업/관측성/복원력)에서 종합 검증하는 에이전트 팀을 조율한다.

## 실행 모드: 에이전트 팀 (기본)

팬아웃/팬인 패턴 — 5개 영역(헬스/네트워크/보안/스토리지-백업/관측성)은 상호 독립적으로 병렬 조사하며 실시간으로 교차 발견을 공유한다. 복원력(카오스) 영역은 파괴적 작업이므로 별도 승인 게이트를 거친다.

## 에이전트 구성

| 팀원 | 에이전트 타입 | 역할 | 스킬 | 출력 |
|------|-------------|------|------|------|
| health | k8s-health | 노드/컨트롤플레인/워크로드 헬스 | k8s-health-check | `_workspace/03_health_report.md` |
| network | k8s-network | Cilium/정책/Ingress/LB/DNS | k8s-network-check | `_workspace/03_network_report.md` |
| security | k8s-security | RBAC/Pod보안/시크릿위생/인증서 | k8s-security-audit | `_workspace/03_security_report.md` |
| storage-backup | k8s-storage-backup | PV/PVC/Velero 백업 | k8s-storage-backup-check | `_workspace/03_storage_backup_report.md` |
| observability | k8s-observability | Prometheus/Loki/Grafana | k8s-observability-check | `_workspace/03_observability_report.md` |
| resilience | k8s-resilience | 장애주입/카오스 (승인 게이트) | k8s-resilience-test | `_workspace/03_resilience_report.md` |

## 워크플로우

### Phase 0: 컨텍스트 확인 (후속 작업 지원)

1. `_workspace/` 디렉토리 존재 여부 확인
2. 실행 모드 결정:
   - **미존재** → 초기 실행. Phase 1로 진행
   - **존재 + 특정 영역만 재점검 요청** (예: "네트워크만 다시 봐줘") → 부분 재실행. 해당 영역 팀원만 단독으로 (팀 구성 없이 Agent 도구로) 재호출하고 해당 리포트만 덮어쓴다
   - **존재 + 전체 재실행 요청** → 기존 `_workspace/`를 `_workspace_{YYYYMMDD_HHMMSS}/`로 이동 후 Phase 1 진행
3. 부분 재실행 시 이전 리포트 경로를 프롬프트에 포함해 기존 발견을 참고하고 갱신하도록 지시

### Phase 1: 준비 및 범위 확인

1. `kubectl cluster-info`, `kubectl get nodes`로 클러스터 규모 파악, `_workspace/00_input/cluster_snapshot.md`에 기록
2. **복원력(카오스) 테스트 포함 여부를 반드시 사용자에게 먼저 확인한다** — 이 영역은 클러스터 상태를 실제로 변경한다. 사용자가 원치 않으면 5개 영역(헬스/네트워크/보안/스토리지-백업/관측성)만 팀에 포함한다.
3. 복원력 테스트를 포함하기로 했다면, 테스트 대상 범위(전체 클러스터 vs 특정 네임스페이스만)와 시점(지금 vs 예약)에 대해서도 함께 확인한다
4. `_workspace/` 생성 (초기 실행 시, 또는 새 실행에서 기존 디렉토리를 이동한 직후)

### Phase 2: 팀 구성

```
TeamCreate(
  team_name: "k8s-test-harness-team",
  members: [
    { name: "health", agent_type: "k8s-health", model: "opus", prompt: "k8s-health-check 스킬을 사용해 클러스터 헬스를 점검하고 _workspace/03_health_report.md에 저장하라." },
    { name: "network", agent_type: "k8s-network", model: "opus", prompt: "k8s-network-check 스킬을 사용해 네트워킹을 점검하고 _workspace/03_network_report.md에 저장하라." },
    { name: "security", agent_type: "k8s-security", model: "opus", prompt: "k8s-security-audit 스킬을 사용해 보안을 감사하고 _workspace/03_security_report.md에 저장하라. 시크릿 값은 절대 조회하지 말 것." },
    { name: "storage-backup", agent_type: "k8s-storage-backup", model: "opus", prompt: "k8s-storage-backup-check 스킬을 사용해 스토리지/백업을 검증하고 _workspace/03_storage_backup_report.md에 저장하라. 복구 실행은 승인 없이 하지 말 것." },
    { name: "observability", agent_type: "k8s-observability", model: "opus", prompt: "k8s-observability-check 스킬을 사용해 관측성 스택을 검증하고 _workspace/03_observability_report.md에 저장하라." }
    // 사용자가 승인한 경우에만 아래 추가:
    // { name: "resilience", agent_type: "k8s-resilience", model: "opus", prompt: "k8s-resilience-test 스킬을 사용하되, 모든 파괴적 작업은 실행 전 반드시 리더(SendMessage)를 통해 사용자 승인을 받아라. 승인 없이는 절대 실행하지 말 것." }
  ]
)
```

작업 등록:
```
TaskCreate(tasks: [
  { title: "헬스체크", assignee: "health" },
  { title: "네트워크 검증", assignee: "network" },
  { title: "보안 감사", assignee: "security" },
  { title: "스토리지/백업 검증", assignee: "storage-backup" },
  { title: "관측성 검증", assignee: "observability" },
  // 승인 시: { title: "복원력 테스트", assignee: "resilience", depends_on: ["헬스체크", "네트워크 검증", "스토리지/백업 검증"] }
])
```

### Phase 3: 팀 자체 조율 실행

**실행 방식:** 5개 영역(헬스/네트워크/보안/스토리지-백업/관측성)은 완전 병렬로 동시 진행하며 발견을 실시간 공유한다.

**팀원 간 통신 규칙 (에이전트 정의 파일에 상세 명시됨):**
- health ↔ storage-backup: 노드 디스크 이상 ↔ PVC 바인딩 실패 교차 확인
- health ↔ security: 권한 부족으로 조회 실패한 리소스 공유
- network ↔ security: 정책 미비/과도 개방 서비스 교차 보고
- 모든 팀원 → observability: 자신의 점검 실패를 대체 지표로 보완 요청

**resilience 팀원 승인 게이트 (포함된 경우):**
- resilience가 파괴적 작업을 제안하면 리더(오케스트레이터)에게 SendMessage로 승인 요청이 온다
- 리더는 이 요청을 사용자에게 그대로 전달하여 실제 승인을 받은 뒤에만 resilience에게 승인 회신을 보낸다 — **리더가 임의로 승인하지 않는다**
- 사용자 응답이 없거나 거부되면 resilience에게 미실행을 지시하고, 해당 테스트는 보고서에 "미실행"으로 기록되도록 한다

**리더 모니터링:**
- 팀원이 유휴 상태가 되면 자동 알림 수신, TaskGet으로 전체 진행률 확인
- 특정 팀원이 막히면 SendMessage로 상태 확인 후 재할당

### Phase 4: 통합 및 종합 보고서 작성

1. 모든 팀원의 작업 완료 대기 (TaskGet)
2. 각 `_workspace/03_*_report.md`를 Read로 수집
3. 영역 간 교차 언급된 이슈(예: 네트워크가 지적한 정책 미비 + 보안이 지적한 동일 네임스페이스)는 통합 보고서에서 하나의 항목으로 병합하고 심각도를 재평가
4. 최종 종합 보고서 생성: `_workspace/04_summary_report.md`
   - 클러스터 개요
   - 영역별 핵심 발견 (critical 우선)
   - 교차 검증된 이슈 (2개 이상 팀원이 동일 대상을 지적한 경우)
   - 복원력 테스트 결과 (실행/미실행 구분)
   - 권장 조치 우선순위

### Phase 5: 정리

1. 팀원들에게 종료 요청 (SendMessage)
2. `TeamDelete`로 팀 정리
3. `_workspace/` 보존 (모든 개별 리포트 + 종합 보고서, 감사 추적용)
4. 사용자에게 종합 보고서 요약 전달 + 개선 피드백 요청 (Phase 7 진화 트리거)

## 데이터 흐름

```
[리더] → TeamCreate(5~6명) → [health]↔[network]↔[security]↔[storage-backup]↔[observability]
                                              ↓ (승인 게이트 통과 시)
                                          [resilience] ←SendMessage(승인요청)→ [리더] ←확인→ 사용자
                                              │
                    각자 _workspace/03_*_report.md 저장
                                              ↓
                                        [리더: Read + 통합]
                                              ↓
                                _workspace/04_summary_report.md
```

## 에러 핸들링

| 상황 | 전략 |
|------|------|
| 팀원 1명 실패/중지 | 리더가 감지 → SendMessage로 상태 확인 → 재시작. 재실패 시 해당 영역 "미수집" 명시 후 나머지로 진행 |
| 팀원 과반 실패 | 사용자에게 알리고 계속 진행 여부 확인 |
| resilience 승인 대기 중 세션 흐름상 응답 불가 | 미실행으로 종료, 보고서에 "승인 대기 중 미실행" 명시 |
| RBAC 권한 부족으로 특정 팀원이 다수 항목 조회 실패 | 사용자 kubeconfig 권한 범위를 보고서 서두에 명시하고, 부족한 권한 목록을 함께 안내 |
| 팀원 간 교차 발견 충돌 (예: A는 정상, B는 이상 보고) | 삭제하지 않고 양쪽 출처를 병기, 통합 보고서에서 "재확인 필요"로 표시 |

## 테스트 시나리오

### 정상 흐름
1. 사용자가 "쿠버네티스 클러스터 종합 테스트해줘" 요청
2. Phase 1에서 클러스터 규모 확인 + 복원력 테스트 포함 여부 질의 → 사용자가 "카오스 테스트는 이번엔 빼줘" 응답
3. Phase 2에서 5명 팀 구성 (resilience 제외), 5개 작업 등록
4. Phase 3에서 5명이 병렬 점검하며 교차 발견 공유 (예: network가 발견한 정책 미비를 security가 재확인)
5. Phase 4에서 종합 보고서 생성, 교차 검증된 이슈 병합
6. Phase 5에서 팀 정리, `_workspace/04_summary_report.md` 사용자 보고

### 에러 흐름
1. Phase 3에서 security 팀원이 RBAC 권한 부족으로 다수 조회 실패
2. security가 리더에게 SendMessage로 권한 문제 보고
3. 리더가 사용자에게 "현재 kubeconfig 권한으로는 일부 클러스터 범위 RBAC 조회 불가" 안내, 계속 진행 여부 확인
4. 사용자가 계속 진행 승인 → security는 조회 가능한 범위만으로 감사 완료, 보고서에 "권한 범위 밖 항목" 명시
5. Phase 4에서 종합 보고서에 이 제약사항을 서두에 기재

### 복원력 테스트 승인 흐름 (포함된 경우)
1. resilience 팀원이 "demo-app 네임스페이스 파드 삭제 테스트"를 리더에게 승인 요청
2. 리더가 사용자에게 그대로 전달: 대상, 예상 영향, 복구 방법 명시
3. 사용자 승인 → 리더가 resilience에게 실행 지시 → resilience가 실행 후 원상복구 확인 → 결과를 보고서에 기록
4. 사용자 거부 또는 무응답 → resilience는 미실행, 보고서에 사유와 함께 기록
