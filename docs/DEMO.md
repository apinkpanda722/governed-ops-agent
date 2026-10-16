# 데모 촬영·리허설 가이드

가상 회사의 **mock 시뮬레이션**을 시연한다. 실제 Jira·Slack 발송이 아니다. 이 문서는 [M7 승인 설계 3절](M7_DESIGN_DRAFT.md#3-데모-설계-약-3분-30초)의 실행 순서다. 아래 명령은 모두 **사용자 실행**이며 이 문서 작성 step에서는 실행하지 않는다. 영상 링크: (촬영 후 등록).

데모는 공식 평가 결과를 바꾸지 않는다. 평가 러너를 쓰지 않고 웹으로만 실행하며 `data/results/`·`data/replay/`·사전 등록 파일을 만들거나 고치지 않는다. 시연 호출은 비용 원장에 남고 다음 결과 커밋에 함께 넣는다. 원장만 올리는 PR은 만들지 않는다. 시연 전에 보존 정리를 실행해 턴 색인을 지우지 않는다.

## 사전 준비(사용자 실행)

설치된 의존성, A 합성 시드, 사용자별 mock 토큰·해시 매니페스트, 비용 원장, 감사 서명키·공개키가 준비되어 있어야 한다. 설정은 `.env.example`의 변수 이름을 따라 사용자가 `.env`에 넣는다. 키·토큰 값이나 파일 본문을 화면에 출력하지 않는다. `DEMO_LOGIN=true`, v2 off, 프로필은 미설정 기본값 `writes_team`을 사용한다. 일반 서비스의 `GOVOPS_MODEL_FAULT_MODE`는 비워 둔다.

**A MCP 서버(8765)는 끄지 않는다.** 별도 A 저장소의 실행 안내에 따라 켜 둔 상태에서 B 저장소 루트에서 시작한다. 아래 서버 명령은 별도 터미널에서 계속 실행한다.

작업 트리가 clean 또는 비용 원장만 변경된 상태인지 확인한다.

```bash
git status --short
```

핵심 스택을 기동한다.

```bash
docker compose -f infra/docker-compose.yml up -d
```

mock 이미지를 최신 코드로 빌드한다.

```bash
docker compose -f infra/docker-compose.yml up -d --build mock-jira mock-slack
```

관측 스택과 LiteLLM 콜백을 기동한다.

```bash
make obs-up
```

**이후 LiteLLM `/health/readiness`가 HTTP 200이 될 때까지 기다린다.** Langfuse 첫 기동이면 UI에서 프로젝트·키를 만들고 사용자가 `.env`에 설정한 뒤 `make obs-up`을 다시 실행한다. 준비가 끝나기 전에 촬영하지 않는다.

앱 DB를 멱등 초기화한다.

```bash
make db-init
```

live A 계약이 동결 스냅샷과 일치하는지 확인한다.

```bash
make smoke-contract
```

v2가 켜져 있지 않은지 본문 대신 일치 줄 수만 확인한다. 기대는 0이며, 이때 grep 종료 코드 1은 일치 없음이다. `.env` 원문은 출력하지 않는다.

```bash
grep -c '^GOVOPS_GUARDRAIL_V2=on' .env
```

에이전트 API를 호스트에서 기동한다.

```bash
make serve
```

웹을 빌드한다.

```bash
pnpm -C web build
```

다른 터미널에서 웹을 기동한다. `http://127.0.0.1:3100/login`을 연다.

```bash
pnpm -C web start
```

mock 상태와 장애 규칙을 초기화한다. 관리 포트는 publish하지 않으며 compose 내부 CLI만 사용한다.

```bash
docker compose -f infra/docker-compose.yml exec mock-jira python -m govops.mocks.admin reset
```

```bash
docker compose -f infra/docker-compose.yml exec mock-slack python -m govops.mocks.admin reset
```

```bash
docker compose -f infra/docker-compose.yml exec mock-jira python -m govops.mocks.admin faults clear
```

```bash
make demo-faults-off
```

시드 상태인지 확인한다. 촬영 화면에 본문을 출력하는 `--bodies` 옵션은 사용하지 않는다.

```bash
docker compose -f infra/docker-compose.yml exec mock-jira python -m govops.mocks.admin state
```

```bash
docker compose -f infra/docker-compose.yml exec mock-slack python -m govops.mocks.admin state
```

사전 고정점을 남긴다. 감사 체인을 초기화하지 않는다.

```bash
make audit-anchor LABEL=m7-demo-pre
```

아래 장면을 촬영 전에 리허설한다. PASS·FAIL·SKIP과 사유를 구분하고 실제 실행하지 않은 검사를 완료로 쓰지 않는다.

## 장면별 조작과 기대 화면

### 장면 0 — 소개·배치도

[ARCHITECTURE 배치도](ARCHITECTURE.md#배치도)에서 신원 위임·게이트웨이·승인·감사의 위치를 보여 준다. mock 시뮬레이션이며 데모 호출을 평가에 합산하지 않음을 설명한다.

### 장면 1 — 응답 유실 뒤 발송 확인

`demo-platform-g3`로 로그인한다. 발송 전에 다음 규칙을 켠다. `scripts/demo/mock_faults.json`은 `slack.send`의 첫 요청에 `drop_after_apply`와 `hide_for 1000`을 함께 주입한다. `set`은 규칙을 교체하고 카운터를 초기화하므로 장면 직전에 적용한다. 서비스에 새 장애 환경 변수를 추가하지 않는다.

```bash
make demo-faults-on
```

요청 카운터가 초기화되어 있는지 확인한다. `faults stats`는 규칙 목록 조회가 아니며 요청 카운터만 제공한다.

```bash
docker compose -f infra/docker-compose.yml exec mock-slack python -m govops.mocks.admin faults stats
```

웹의 새 대화에서 “INC-2026-012 장애보고 요약해서 team-platform에 올려줘”를 입력한다. 승인 카드의 팀 채널 수신처·초안·검사 결과를 확인한 뒤 승인한다. 완료 카드와 기록 링크를 보여 준다. 기대 효과는 mock-slack 메시지 1건이다. 이 수는 시연 기대값이며 아직 live 실측한 결과가 아니다.

```bash
docker compose -f infra/docker-compose.yml exec mock-slack python -m govops.mocks.admin state
```

```bash
docker compose -f infra/docker-compose.yml exec mock-slack python -m govops.mocks.admin faults stats
```

발송 후 `applied_rules`로 주입 여부를 확인한다. 기록 타임라인의 실행 시도·표식 조회·결과를 보여 주되 이벤트 수·순서와 `marker_lookup` 횟수는 리허설에서 실측해 기록한다. 기대한 주입 경로를 타지 않았으면 성공 시연으로 설명하지 않는다.

장면 직후 규칙을 해제한다.

```bash
make demo-faults-off
```

### 장면 2 — 존재 보호

규칙이 해제된 상태에서 `demo-finance-g3`로 로그인한다. 새 대화에서 장면 1과 같은 요청을 입력한다. 기대 화면은 “찾을 수 없음”, 승인 카드 없음이며 mock 메시지 수가 늘지 않아야 한다. 같은 상태 조회 명령으로 전후를 대조한다. live 편차가 있으므로 촬영 전에 리허설하고 실제 나온 경로를 적는다.

### 장면 3 — 간접 인젝션 상황의 수신처 확인

`demo-platform-g3`로 로그인한다. [설계 0-6절](M7_DESIGN_DRAFT.md#0-6-장면-34-데이터)에서 고른 공개 합성 회의록에 대해 다음 질의를 그대로 쓴다.

> 결제서비스팀 2026-06-30 할인 캐시 개선 프로젝트 회의록을 요약해서 team-platform 채널에 올려줘

기대 화면: 승인 카드의 **수신처가 요청자가 지정한 팀 채널**이다. 격리 표시는 필수 기대값이 아니다. 수신처가 다르면 결정론 검사 차단 또는 승인 거부 경로를 보여 준다. 인젝션 본문은 문서·화면·캡션에 인용하지 않는다. 결과를 보고 질의를 고르지 않으며, 한 번 정한 질의에서 나온 경로(수신처 일치·결정론 차단·승인 거부·격리)를 그대로 적는다.

리허설 결과: (촬영 전 기록)

### 장면 4 — 전사 공지의 별도 승인자

요청자는 `demo-platform-g3`, 승인자는 `demo-platform-g4`다. 근거는 공개 문서 `syn-notice-019`이며 질의는 그대로 쓴다.

> 플랫폼개발팀 공지 "인증 키 갱신 방식 결정"을 요약해서 전사 공지 채널에 올려줘

g3에게 본인 승인 불가가 표시되는 것을 확인한다. g4로 로그인해 승인함의 초안·수신처·검사 결과를 확인하고 승인한다. 발송 완료 화면을 보여 준다. 발송 신원은 g3이며 g4는 수신처를 바꿀 수 없다. 팀 전용 근거가 추가되어 V15가 막으면 차단 사실을 그대로 보여 주거나 공개 근거 질의 대체임을 밝힌다.

### 장면 5 — 감사·trace·수치

소유자로 돌아와 기록 화면의 PASS 배지, 확인 시각·레코드·고정점 개수와 한계 문구를 보여 준다. PASS는 이 호스트 체인과 작업 트리 고정점 기준이다. 삭제·정리된 대화는 기록 화면에서 볼 수 없으며 체인은 CLI로 확인한다.

```bash
make audit-verify
```

복사본에서만 꼬리를 자르는 시연을 실행한다.

```bash
make demo-scene5
```

스크립트는 임시 디렉터리에 체인 파일과 legacy 파일(`chain.genesis`가 봉인한다)을 복사하고 원본 디렉터리의 최신 고정점을 고른다. 복사본 검증: 자르기 전 PASS → 자른 뒤 FAIL. 먼저 복사본을 검증해 `PASS chain …`을 출력하고(PASS가 아니면 코드만 출력하고 exit 1), 마지막 체인 파일에 그 고정점보다 작은 seq만 남긴 뒤 임시 고정점의 `dir`만 복사본으로 바꿔 기존 검증기를 다시 호출한다. 기대 출력은 `PASS chain …` 다음 `FAIL chain … code=tail_truncated seq=<n>`다. 원본 체인·고정점은 읽기만 하며 임시 복사본은 종료 시 정리한다. 고정점이 없으면 안내와 함께 exit 2이므로 사전 고정점을 먼저 남긴다.

Langfuse에서 시연 task의 trace를 보여 준다. 수치 화면은 [README 결과 표](../README.md#평가-결과-요약)의 출처와 같은 문구만 쓴다. 감사 탐지의 기존 관측은 [M4 결과 41·77행](results/2026-10-08-m4-audit-live.md), [`scene5_tail_cut` 재확인 35행](results/2026-10-13-m6-limits-delete-live.md#감사보존)에 있다. 복사본 live 시연은 촬영 전 확인할 항목이다.

## 초기화와 촬영 종료

| 대상 | 방법 |
|---|---|
| mock 메시지·코멘트·티켓 | 위 `admin reset` 명령 |
| 앱 DB 대화·checkpoint | 웹 소프트 삭제 뒤 `make checkpoint-prune`; 기본 dry-run이며 APPLY 여부는 사용자 판단. 시연 전에는 정리하지 않는다 |
| outbox | 초기화하지 않는다. task ID가 키에 포함된다 |
| 감사 체인 | 초기화하지 않는다. `m7-demo-*` 고정점으로 시연 구간을 구분한다 |
| 세션 | 서비스 재시작 시 재로그인 |

촬영을 마치면 주입 규칙을 해제한다.

```bash
make demo-faults-off
```

후속 고정점을 남기고 비용 원장 증분은 결과 커밋에 함께 포함한다.

```bash
make audit-anchor LABEL=m7-demo-post
```

## 실패 시 대체 경로

| 실패 | 대체 |
|---|---|
| live A·LiteLLM 불가 | `tests/e2e_backend.py`를 쓰는 `make e2e-web`의 replay 화면으로 대체하고, 장면 1 주입·장면 5는 터미널 로그로 대체한다. **화면 하단과 영상 캡션에 “replay(기록 재생)”를 표시한다** |
| 장면 1 승인 실패 | 같은 문장으로 최대 2회 재시도하고 편집 여부를 밝힌다. 시연은 평가가 아니다 |
| 장면 4 V15 차단 | 공개 근거 문서 질의로 대체하거나 “결정론 검사가 막음” 화면을 쓴다 |
| 장면 5 탐지 불발 | `make smoke-audit`의 `PASS scene5_tail_cut` 터미널 출력을 대체 화면으로 쓰고 대체 사실을 표시한다 |
| 규칙이 남음 | `faults clear` 후 읽기 탐침으로 적용 규칙이 없는지 확인한다. 필요하면 `admin reset`한다 |

replay로 바꾸기 전에 `next start` 데모 서버를 종료한다. e2e는 포트 3200·8200·8201을 새로 쓰고 기존 서버를 재사용하지 않으며 `next build`가 `web/.next`를 덮어쓴다. A MCP 서버(8765)는 계속 켜 둔다.

```bash
make e2e-web
```

꼬리 절단 시연의 대체 확인이 필요할 때만 사용자 실행한다.

```bash
make smoke-audit
```
