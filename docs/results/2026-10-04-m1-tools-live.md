# M1 도구 계층 live 실측 (m1-tools)

사용자가 `m1-tools` 머지 후 live 환경에서 실행한 스모크와 기록의 결과다. 토큰·Authorization 값은 이 문서에 없다.

## 실행 조건

| 항목 | 값 |
|---|---|
| 날짜 | 2026-10-04 |
| 코드 커밋 | main `88a4087`(PR #10 머지) |
| 모드 | live (실제 A MCP, 호스트 기동) |
| A git HEAD | `d2eec9c08ffbd61cf8ab5d84d4dd817452d044cc` (기준 `d2eec9c`와 일치) |
| 계약 스냅샷 해시 | `0e3a05e4439eaee5ed963aeeda3f47ac3b703fc578de6fb1593654ac21d8f9c5` (`snapshot_sha256`) |
| A 인덱스 이름 | 이 실행에서는 기록하지 않았다(live 실행 기록은 M2 범위) |
| 시드 | 티켓 500건, 사용자 38명 (`data/seed/manifest.json`) |
| 모델 호출·비용 | 없음(LLM 미사용). 비용 $0 |
| compose | 핵심 스택(postgres, litellm, mock-jira, mock-slack) |

## 스모크 결과 (기대값은 실행 전에 코드에 고정)

| 스모크 | 기대값 | 결과 |
|---|---|---|
| `smoke-contract` | live `tools/list`가 `data/contract` 스냅샷 4개와 일치 | 일치 |
| `smoke-host-header` | 사전 가설: 위조 `Host` 헤더를 단 **인증된** 요청을 A가 거절(421 또는 403) | **421** (가설 일치) |
| 컨테이너 도달성 | `mock-jira` 컨테이너에서 `host.docker.internal:8765/mcp`로 POST 시 401이면 네트워크 도달 가능(인증 미들웨어가 먼저 응답) | **401** (도달 가능) |
| `smoke-identity` | `syn-incident-022`가 `demo-platform-g3`의 `lookup_id(incident, INC-2026-012)` 결과에 있고 `demo-finance-g3` 결과에는 없음. 시드의 권한 필드로 계산한 team 티켓 가시성이 platform 보임·finance 안 보임 | 일치 |
| `smoke-visibility` | 38명 × 표본 40건에서 독립 oracle·mock-jira·A(`run_sql`) 가시성 불일치 0건 | 비교 1520건, 불일치 **0** |
| `smoke-mock` | 인증(401), jira 조회·검색, slack 채널 9개, 자기 팀 채널 이력 성공, 타 팀 채널 이력 403 | 일치 |

## 리소스 (`docker stats`, 핵심 스택)

| 컨테이너 | 메모리 |
|---|---|
| litellm | 563.8 MiB |
| postgres | 46.0 MiB |
| mock-slack | 46.5 MiB |
| mock-jira | 39.4 MiB |
| Docker VM 총량 | 15.66 GiB |

Langfuse(profile `obs`)는 M2에서 기동한 뒤 다시 실측한다.

## A 응답 기록 (`make record-a`)

- 요청 6건 중 4건 저장(`demo-platform-g3`·`demo-finance-g3`의 `lookup_id`와 `get_document`), 2건 건너뜀.
- 건너뜀: `search_docs` 2건(platform·finance). 결과에 `syn-*`가 아닌 문서가 포함되어 기록 전체를 건너뛰었다(응답 편집 없음).
- 저장 파일은 `data/replay/a/recordings.jsonl`. 검증 규칙(`syn-*`만, 토큰·Authorization 없음)을 통과한다.

## 미확인

| 항목 | 상태 |
|---|---|
| litellm 이미지 cosign 서명 검증(`v1.103.3`) | 사용자 확인 대기 |
| minio digest 일치 확인 | 사용자 확인 대기 |
