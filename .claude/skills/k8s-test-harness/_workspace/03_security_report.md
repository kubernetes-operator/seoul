# 보안 감사 보고서

- 감사일: 2026-07-19
- 클러스터: v1.34.3 (control-plane 3, infra 2, worker 3 — 총 8 노드)
- 네임스페이스: 21개 (시스템/애드온 + 사용자: demo-app, model-factory, gitea, minio, os-monitor 등)
- 범위: RBAC, Pod Security, ServiceAccount 토큰 자동마운트, 시크릿 위생(메타데이터만), 인증서 만료, 이미지 위생
- 원칙: 감사(read-only)만 수행. **시크릿 데이터 값은 일절 조회하지 않음** — 타입/네임스페이스/개수 등 메타데이터만 확인. 교정 조치는 수행하지 않음.

---

## RBAC 위험 (cluster-admin 바인딩, 와일드카드 권한)

### cluster-admin ClusterRoleBinding 대상
| 바인딩 | 대상 | 분류 | 심각도 |
|---|---|---|---|
| `cluster-admin` | Group `system:masters` | 기본 제공(부트스트랩) | 정보 |
| `kubeadm:cluster-admins` | Group `kubeadm:cluster-admins` | kubeadm 기본 제공 | 정보 |
| `globaladmin-user-9mtlz` | **User `user-9mtlz`** | 개별 사용자 직접 바인딩 | **높음** |
| `headlamp-admin` | ServiceAccount `headlamp/headlamp` | 애드온(대시보드) | 중간 |
| `velero-server` | ServiceAccount `velero/velero-server` | 백업 컴포넌트 | 중간 |

- **높음 — `user-9mtlz`가 cluster-admin에 직접 바인딩**: 개별 User에게 클러스터 전체 관리 권한이 직접 부여됨. Rancher 계열로 추정되나(아래 `cattle-globalrole-admin`도 동일 사용자), 그룹/RBAC 계층이 아닌 개인 계정 직결 바인딩은 계정 탈취 시 전면 장악으로 이어짐. 소유자 확인 및 그룹 기반 권한으로 전환 검토 권장.
- **중간 — headlamp 대시보드 SA가 cluster-admin**: 웹 대시보드(Headlamp)의 SA가 cluster-admin 전권 보유. 대시보드 접근 경로(인증/노출)가 곧 클러스터 전권 경로가 됨. Headlamp 노출 범위 및 인증 방식 점검 필요(network 팀원 교차 검토 대상).
- **중간 — velero-server SA가 cluster-admin**: 백업 도구 특성상 광범위 read 권한은 정당하나 cluster-admin(쓰기 포함 전권)은 과도할 수 있음. 최소권한 ClusterRole로 축소 검토 권장.

### 와일드카드(`*` resources + `*` verbs) ClusterRole (system: 접두 제외)
Rancher/Cluster-API(CAPI) 스택이 대량의 전권 ClusterRole을 도입함. 대부분 오퍼레이터 SA에 바인딩되어 정당하나 공격 표면 확대 요인:

| ClusterRole | 바인딩 대상 | 비고 |
|---|---|---|
| `cattle-globalrole-admin` | User `user-9mtlz` | 위 cluster-admin 사용자와 동일 — 전권 중복 부여 |
| `cluster-owner` | User `u-b4qkhsnliz` | Rancher 개별 사용자에 전권 |
| `capi-aggregated-manager-role` | SA `cattle-capi-system/capi-manager` | CAPI 오퍼레이터(정당) |
| `rancher-turtles-aggregated-manager-role` | SA `cattle-turtles-system/rancher-turtles-manager` | Rancher Turtles 오퍼레이터(정당) |
| `provisioning-rke-cattle-io` | (바인딩 없음) | 미사용 전권 롤 — 정리 검토 |
| `rancher-turtles-operator-admin` | (바인딩 없음) | 미사용 전권 롤 — 정리 검토 |

- system: 접두 와일드카드 ClusterRole은 쿠버네티스 기본 제공으로 요약만 남김.

**재현 명령(read-only):**
```
kubectl get clusterrolebindings -o json | jq '.items[] | select(.roleRef.name=="cluster-admin") | {name:.metadata.name, subjects:.subjects}'
kubectl get clusterroles -o json | jq -r '.items[] | select(.rules[]? | (.resources[]? == "*") and (.verbs[]? == "*")) | .metadata.name' | grep -vE '^system:'
```

---

## Pod Security 위반 (네임스페이스별, 시스템/사용자 구분)

### Pod Security Standards(PSS) 적용 현황 — 전반적 미비
- **20개 네임스페이스 중 19개가 `enforce=none`, `warn=none`** (라벨 미설정). PSS 강제/경고가 사실상 비활성.
- `metallb-system`만 `enforce=privileged`(=사실상 무제한 허용) 설정 — 강제는 있으나 가장 느슨한 등급.
- 사용자 네임스페이스(demo-app, model-factory, gitea, minio 등)에 `baseline`/`restricted` PSS 미적용 → **중간~높음**. 최소 `warn=baseline`부터 단계 도입 권장.

### 특권/호스트 네임스페이스 파드
| 네임스페이스 | 항목 | 분류 | 심각도 |
|---|---|---|---|
| kube-system | privileged 25, hostNetwork/PID/IPC 57 (cilium, kube-proxy, csi-nfs 등) | 시스템 — 정당 | 정보 |
| gpu-operator | privileged 10 (nvidia-*, gpu-feature-discovery) | 시스템 애드온 — 정당 | 정보 |
| monitoring | node-exporter 8개 hostNetwork+hostPID=true | 모니터링 DaemonSet — 통상 정당 | 낮음 |
| metallb-system | 2 hostNetwork (speaker) | LB 컴포넌트 — 정당 | 낮음 |
| **cilium-test-1** | perf-client/server 3개 hostNetwork=true | 사용자 테스트 워크로드 | **중간** |
| **default** | `node-debugger-infra01-*` 3개 hostNetwork+hostPID+hostIPC=true | 노드 디버그 파드 | **중간** |

- **default/node-debugger-* (3개)**: 세 가지 호스트 네임스페이스 전체(hostNetwork+hostPID+hostIPC)를 공유하는 노드 디버깅 파드가 default에 상주. `kubectl debug node/...` 잔여물일 가능성 높음 — 임시 파드는 사용 후 삭제 권장. 상주 시 노드 전체 접근 경로가 됨.
- **cilium-test-1/perf-* (3개)**: 성능 테스트용 hostNetwork 파드. 테스트 네임스페이스(12일 경과)로 보이며 테스트 종료 후 정리 권장.

### runAsNonRoot 미설정 (root 실행 허용) — 사용자 네임스페이스
파드/컨테이너 어느 레벨에서도 `runAsNonRoot: true`가 없는 파드 수:

| 네임스페이스 | 미설정 파드 수 | 심각도 |
|---|---|---|
| logging | 9 | 중간 |
| default | 5 | 중간 |
| demo-app | 4 | 중간 |
| model-factory | 3 | 중간 |
| gitea | 1 | 낮음 |
| minio / headlamp / nginx-gateway | 0 | 양호 |

- 다수 사용자 워크로드가 root 실행을 명시적으로 차단하지 않음. 컨테이너가 uid 0으로 뜰 수 있어 컨테이너 탈출 시 영향 확대. `securityContext.runAsNonRoot: true` + non-root uid 지정 권장.

**재현 명령(read-only):**
```
kubectl get pods -A -o json | jq -r '.items[] | select(.spec.hostNetwork==true or .spec.hostPID==true or .spec.hostIPC==true) | .metadata.namespace' | sort | uniq -c
kubectl get pods -A -o json | jq -r '.items[] | select(.spec.containers[]?.securityContext?.privileged==true) | .metadata.namespace' | sort | uniq -c
kubectl get ns -o json | jq -r '.items[] | "\(.metadata.name): \(.metadata.labels["pod-security.kubernetes.io/enforce"] // "none")"'
```

---

## ServiceAccount 토큰 자동마운트 현황

대부분의 사용자 네임스페이스 SA가 `automountServiceAccountToken: false`를 명시하지 않음 → 토큰이 파드에 자동 마운트되어 탈취 시 API 접근에 재사용될 수 있음(잠재적 위험).

| 네임스페이스 | automount 미차단 SA / 전체 SA |
|---|---|
| monitoring | 7/8 |
| logging | 4/4 |
| headlamp | 2/2 |
| model-factory | 2/2 |
| nginx-gateway | 2/2 |
| demo-app | 1/1 |
| minio | 1/1 |
| default | 1/1 |
| gitea | 1/3 |

- 참고: 파드 스펙에서 재정의됐을 수 있으므로 위 수치는 **SA 레벨 "잠재적 위험"** 지표임. 토큰이 실제로 불필요한 워크로드는 파드 스펙에 `automountServiceAccountToken: false` 설정 권장.
- default SA가 RoleBinding/ClusterRoleBinding의 subject로 직접 권한을 받은 사례는 **발견되지 않음**(양호).

**재현 명령(read-only):**
```
kubectl get sa -n <ns> -o json | jq '[.items[] | select(.automountServiceAccountToken != false)] | length'
```

---

## 시크릿 메타데이터 요약 (값 미포함)

> 조직 정책에 따라 시크릿의 `data`/`stringData` 값은 조회/출력하지 않음. 아래는 네임스페이스·타입·개수 등 메타데이터만.

주요 타입 분포(네임스페이스별 개수):
- `Opaque`: monitoring 11, cert-manager 6, velero 4, gitea 4, logging 3, os-monitor 2, metallb-system 2, default 2, model-factory 1, demo-app 1, kube-system 1, headlamp 1 등
- `kubernetes.io/tls`: kube-system 3, default 1
- `helm.sh/release.v1`(Helm 릴리스 상태): velero 2, monitoring 2, logging/kube-system/headlamp/gpu-operator/gitea/arc-systems 각 1

평가:
- 클러스터 생성 후 약 95일 경과로, **1년 이상 로테이션되지 않은 Opaque 시크릿은 없음**(생성일 기준). 현 시점 로테이션 긴급 대상 없음.
- 다만 애플리케이션 자격증명으로 추정되는 `Opaque` 시크릿이 사용자 네임스페이스(gitea 4, model-factory 1, demo-app 1 등)에 존재 → 정기 로테이션 정책 수립 권장.
- `helm.sh/release.v1`은 Helm 배포 메타데이터로 통상 위험 대상 아님.

**재현 명령(메타데이터만, read-only):**
```
kubectl get secrets -A -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,TYPE:.type,CREATED:.metadata.creationTimestamp
```
(주의: `-o yaml`/`-o json`(전체)/`-o jsonpath='{.data}'` 등 data 필드 노출 명령은 사용 금지)

---

## 인증서 만료 현황 (긴급/경고/정상)

cert-manager `Certificate` 리소스는 1건 확인:

| 네임스페이스/이름 | notAfter | Ready | 만료까지 | 구간 |
|---|---|---|---|---|
| default/letsencrypt-issuer-public-cert | 2026-09-23T07:22:58Z | True | **66일** | **경고(90일 이내)** |

- 긴급(30일 이내): 없음
- 경고(90일 이내): 1건 — `default/letsencrypt-issuer-public-cert` (66일 남음). Let's Encrypt 자동 갱신이 정상 동작하는지 확인 권장(cert-manager 갱신 임계값 통상 만료 30일 전).
- 참고: cert-manager `Certificate` CRD로 관리되지 않는 TLS 시크릿(`kubernetes.io/tls`, kube-system 3 / default 1)은 별도 존재하나, 이들은 만료 정보를 시크릿 data 조회 없이 확인 불가하므로 본 감사에서는 만료 판정을 생략함(정책상 data 미조회).

**재현 명령(read-only):**
```
kubectl get certificate -A -o json | jq -r '.items[] | "\(.metadata.namespace)/\(.metadata.name) notAfter=\(.status.notAfter) ready=(\(.status.conditions[]? | select(.type=="Ready") | .status))"'
```

---

## 이미지 위생 (latest 태그, 다이제스트 미고정)

- 전체 distinct 이미지: **69개**
- 다이제스트(`@sha256:`) 고정: **7개** (약 10%) — 고정률 낮음
- `:latest` 또는 태그 미지정: **8개**

`:latest`/무태그 이미지 (사용자 네임스페이스 강조):

| 네임스페이스 | 이미지 | 심각도 |
|---|---|---|
| demo-app | registry.local.cloud:5000/demo-app-api:**latest** | **중간** |
| model-factory | registry.local.cloud:5000/model-factory/model-portal:**latest** | **중간** |
| os-monitor | registry.local.cloud:5000/os-monitor/api:**latest** | **중간** |
| os-monitor | registry.local.cloud:5000/os-monitor/collector:**latest** | **중간** |
| os-monitor | registry.local.cloud:5000/os-monitor/dashboard:**latest** | **중간** |
| default | ollama/ollama:**latest** | 낮음 |
| default | busybox:**latest** / busybox(무태그) | 낮음 |

- 사설 레지스트리(`registry.local.cloud:5000`)의 사내 앱들이 `:latest`로 배포됨 → 재배포/스케줄링 시 예측 불가한 이미지 변동, 롤백 곤란, 공급망 무결성 저하. **버전 태그 + 다이제스트 고정** 권장.
- 대부분의 이미지가 다이제스트 미고정(69개 중 62개). 프로덕션 워크로드부터 `@sha256:` 고정 도입 권장.

**재현 명령(read-only):**
```
kubectl get pods -A -o json | jq -r '.items[] | (.spec.containers[]?, .spec.initContainers[]?).image' | sort -u | grep -E ':latest$|^[^:@]+$'
```

---

## 종합 우선순위 (요약)

| 순위 | 이슈 | 심각도 | 조치 방향(감사 제언, 본 에이전트는 미적용) |
|---|---|---|---|
| 1 | User `user-9mtlz` 개인 계정에 cluster-admin + cattle-globalrole-admin 직접 바인딩 | 높음 | 소유자 확인, 그룹 기반/최소권한 전환 |
| 2 | 사용자 네임스페이스 PSS 미적용(19/20 enforce=none) | 중간~높음 | warn=baseline부터 단계 도입 |
| 3 | headlamp 대시보드 SA가 cluster-admin | 중간 | 노출/인증 점검(network 팀 교차), 권한 축소 |
| 4 | default/node-debugger 등 호스트 전권 디버그 파드 상주 | 중간 | 사용 후 삭제 |
| 5 | 사내 앱 `:latest` 태그, 다이제스트 미고정 다수 | 중간 | 버전+다이제스트 고정 |
| 6 | SA 토큰 자동마운트 광범위 미차단 | 중간 | 불필요 워크로드 automount=false |
| 7 | letsencrypt 인증서 66일 후 만료(경고) | 경고 | 자동 갱신 동작 확인 |
| 8 | 사용자 워크로드 runAsNonRoot 미설정 다수 | 중간 | securityContext 강화 |

## 팀 교차 제보 제안
- network 팀원: `headlamp` 대시보드 및 사용자 네임스페이스 서비스 노출 범위 재평가 요청(cluster-admin SA 결합 시 위험 증폭).
- resilience 팀원: `default/node-debugger`, `cilium-test-1/perf-*` 등 호스트 네임스페이스 파드는 장애 주입 대상에서 제외/정리 후 진행 권장.
- health 팀원: 권한 부족으로 조회 실패한 리소스 없음(현 계정으로 전체 조회 성공).

---
*본 보고서는 read-only 감사 결과이며 교정 조치는 수행하지 않았습니다. 시크릿 데이터 값은 어떤 항목에도 포함되지 않았습니다.*
