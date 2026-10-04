# M1 에이전트·API 계층 live 실측 (m1-agent)

사용자가 `m1-agent` 머지 후 live 환경에서 실행한 확인과 스모크의 결과다. 키·토큰 값은 이 문서에 없다.

## 실행 조건

| 항목 | 값 |
|---|---|
| 날짜 | 2026-10-04 |
| 코드 커밋 | main `fcc0013`(PR #11 머지) |
| 모드 | live (실제 A MCP, compose 핵심 스택, LiteLLM 경유 OpenAI) |
| 모델 | LiteLLM 별칭 `agent-primary` → `gpt-6-luna` (fallback·Guardrail 없음) |
| A git HEAD | `d2eec9c08ffbd61cf8ab5d84d4dd817452d044cc` |
| 계약 스냅샷 해시 | `0e3a05e4439eaee5ed963aeeda3f47ac3b703fc578de6fb1593654ac21d8f9c5` |
| A 인덱스 이름 | 이 실행에서는 기록하지 않았다(live 실행 기록은 M2 범위) |

## 확인 결과 (기대값은 실행 전에 코드에 고정)

| 확인 | 기대값 | 결과 |
|---|---|---|
| `make db-init` | `govops` DB가 멱등 생성되어 존재 | `govops` DB 존재 |
| `make agent-key` | 에이전트 전용 가상 키가 출력 없이 `.env`의 `LITELLM_AGENT_KEY`에 기록됨. 이미 값이 있으면 거부 | 키 값은 출력되지 않았고 `.env`에 기록됨(이 문서에도 키 값 없음) |
| `make smoke-checkpointer` | `GOVOPS_DATABASE_URL`의 Postgres에 체크포인트를 쓰고 읽은 값이 쓴 값과 같음 | 왕복 일치 |
| `make smoke-agent` | 두 사용자 task가 모두 `completed`, `syn-incident-022`가 platform의 관찰·인용 문서 ID에만 있고 finance에는 없음, 원장에 `agent_primary` 행 증가, OpenAI 누적액이 상한(9.13) 미만 | 아래 표와 같이 모두 충족 |

### `smoke-agent` 상세

같은 요청 문장(`INC-2026-012 장애를 요약해 주세요. 근거로 쓴 문서 ID를 본문에 표기해 주세요.`)을 두 사용자가 보냈다. 판정은 답변 문구가 아니라 관찰·인용 문서 ID로 한다.

| 사용자 | 상태 | 단계 수 | `syn-incident-022` |
|---|---|---:|---|
| `demo-platform-g3` | completed | 3 | 조회(관찰)·인용 모두 있음 |
| `demo-finance-g3` | completed | 2 | 문서 0건(관찰·인용 모두 없음) |

### 비용

- 모델 호출 5회(platform 3, finance 2), 합계 약 **$0.000714**, 원장(`data/manifest/costs.jsonl`)에 `agent_primary` 행 5개.
- OpenAI 상한 $9.13 대비 잔여 **$9.129286**.
- 이 실행은 비용 예약·정산 도입 전 코드(`fcc0013`)로 했으므로 원장 행은 호출 뒤 정산 행 형식 한 가지뿐이다(예약 행 없음). 예약·정산 행이 섞인 원장도 기존 행을 그대로 읽는다.

## 한계

- 단일 요청 문장·두 사용자의 한 번씩의 실행이다. TCR·정확도·지연 목표와는 무관한 동작 확인이다(M2 이후 평가).
- 모델 호출은 호출 단위 fallback·Guardrail 없이 `gpt-6-luna` 한 모델이다(M3·M4 범위).
