# Step 5: gateway-docs

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-007·008·009·018), `/docs/ARCHITECTURE.md` ("게이트웨이와 Guardrail 비대칭 구조", "상태 관리"의 비용 원장), `/docs/THREAT_MODEL.md` (F6)
- `/docs/PRD.md`, `/docs/EVAL.md`, `/AGENTS.md`(기술 스택 `LLM` 줄과 명령어 블록), `/.env.example`, `/tests/test_docs_consistency.py`, `/tests/test_makefile.py`
- 이전 step 산출물: `/src/govops/env.py`, `/src/govops/costs.py`, `/src/govops/gateway/client.py`, `/src/govops/gateway/faults.py`, `/src/govops/gateway/smoke.py`, `/infra/litellm/config.yaml`, `/infra/litellm/config.obs.yaml`

## 작업

이 phase에서 확정된 결정을 문서에 반영한다. 코드는 수정하지 않는다. 문서에는 **결정 내용만** 쓴다(누가 정했는지·변경 경위는 쓰지 않는다).

### ADR-019 추가 (`docs/ADR.md` 끝, 기존 ADR 서식, 상태 "유효", 날짜 2026-10-05)

제목: "M3 결정 — fallback 모델·원장 정산·모델 호출 타임아웃·모델 장애 주입". 결정 항목:
1. fallback 모델은 Claude Sonnet 5.5(`claude-sonnet-5-5`, LiteLLM `anthropic/claude-sonnet-5-5`)이고 호출 단위(LiteLLM Router)로 전환한다. 단가는 입력 $2/MTok·출력 $10/MTok(출처 `https://platform.claude.com/docs/en/about-claude/pricing`, 확인일 2026-10-05)이고 캐시 입력도 일반 입력 단가로 센다.
2. **비용 메모**: 4.7 이후 Anthropic 모델은 새 토크나이저를 쓰므로 같은 텍스트에 토큰이 약 30% 더 나온다. 호출 전 추정은 입력 상한을 크게 잡으므로 보정 계수를 두지 않고, 실측 비용은 원장으로 확인한다.
3. **모델 호출 타임아웃**(도구 호출의 Soft 3초/Hard 10초와 별개): 주 모델 15초, `num_retries` 0(같은 모델 재시도 없이 바로 fallback), fallback 모델 30초, 클라이언트 60초. 두 LiteLLM 설정 파일(`config.yaml`·`config.obs.yaml`)의 값은 같고 테스트로 고정한다.
4. **원장**: fallback이 가능한 호출은 공급사별 예약 2건(OpenAI·Anthropic, 각 공급사 단가)을 잡고 응답 모델 기준으로 정산한다(응답한 쪽에 실제 비용, 다른 쪽은 0). OpenAI 상한 소진은 예산 중단이고, Anthropic 상한만 소진되면 fallback 없는 별칭(`agent-primary-only`)으로 호출한다.
5. **응답 모델 ID 매핑**은 정확 일치 + 등록된 접두어(날짜 접미사 `-YYYYMMDD`·`-YYYY-MM-DD` 허용)이다. 매핑되지 않은 모델은 등록 모델 중 최대 단가로 두 예약 모두 정산하고 경보한다. **미등록 응답 모델은 가장 비싼 단가로 양쪽 공급사에 모두 정산하므로 비용 보고가 과대 계상될 수 있다.** dev manifest에 `unmapped_models`를 기록하고 하나라도 있으면 결과 문서에 표시한다. 호출마다 제공자 모델 ID를 `ChatResult.model`·LLM 기록 `response_model`·`models_seen`에 남긴다.
6. **모델 장애 주입(F6)**: 호출 번호 기준 스크립트(첫 호출만·둘째 호출만·전부 실패)를 두 백엔드로 구현한다 — `mock_testing_fallbacks` 요청 파라미터, 닫힌 포트를 가리키는 배포 `agent-primary-down` + fallbacks. 하네스에서만 쓰고 `GOVOPS_MODEL_FAULT_MODE`가 설정된 상태로는 일반 서비스가 시작되지 않는다. 사용할 백엔드는 `smoke-fallback` 결과로 사전 등록 문서에서 하나로 확정한다. chaos proxy는 쓰지 않는다.
7. **환경 변수**: 빈 문자열·공백은 미설정으로 보고 기본값을 쓴다(`env_or`). `src` 전체에서 비어 있지 않은 기본값을 `environ.get`/`getenv`에 넘기는 패턴은 스캔 테스트가 막는다.
8. **감사 식별자**: 감사 로그의 `task_id`(`^[0-9a-f]{32}$`)와 `actor_sub`(등록 사용자 라벨 형식)는 형식으로 검증하며 개인정보 정규식은 식별자·해시 필드에 적용하지 않는다(무작위 hex가 전화번호 규칙에 걸려 정상 기록이 거부되는 것을 막는다). 형식 밖 값은 거부한다.
9. **mock 변경 후 절차**: mock 코드가 바뀐 PR을 머지한 뒤에는 `docker compose -f infra/docker-compose.yml up -d --build mock-jira mock-slack`을 실행한다.

### 기존 문서의 "확인 필요" 갱신

- `docs/ADR.md` ADR-007의 fallback "확인 필요: 모델 ID·가격" 문구와 `docs/ARCHITECTURE.md`·`docs/EVAL.md`·`docs/PRD.md`·`AGENTS.md` 기술 스택 `LLM` 줄의 fallback 항목을 확정 값(`claude-sonnet-5-5`, ADR-019 참조)으로 바꾼다. **Guardrail v2 분류기(Haiku 계열)의 "확인 필요"는 그대로 둔다.**
- `docs/ARCHITECTURE.md` 게이트웨이 절: fallback 한 줄을 ADR-019 참조로 바꾸고 `infra/litellm/config.yaml` 설명에 `agent-fallback`·`agent-primary-only`·`agent-primary-down`을 한 줄 추가한다. 디렉토리 구조에 `env.py`, `gateway/faults.py`·`smoke.py`를 한 줄씩 추가한다.
- `docs/THREAT_MODEL.md` F6 행의 "모델 장애를 LiteLLM 앞에서 주입하는 방법은 미정" 문구를 ADR-019 6항 참조로 바꾼다. F6 행 판정에 "주 모델 장애 중 Guardrail 통과 확인은 M4(Guardrail 구현 후)"를 명시한다. 장애 주입 **표본 수**의 "미정(M3에서 결정)"은 그대로 둔다(다음 phase).
- `docs/EVAL.md` 안정성 절: fallback 보고 항목에 "`unmapped_models`가 하나라도 있으면 결과 문서에 표시한다"를 한 줄 추가한다.
- `AGENTS.md`: 위 `LLM` 줄 외에는 **명령어 블록만** 수정한다. `make smoke-fallback`을 추가한다.
- `.env.example`: `GOVOPS_MODEL_FAULT_MODE`와 `ANTHROPIC_API_KEY` 설명이 현재 구현과 맞는지 확인한다.

`tests/test_docs_consistency.py`·`tests/test_makefile.py`가 통과해야 한다(테스트를 약화하지 말고 문서·Makefile을 맞춘다).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조와 AGENTS.md 계층 경계를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 결정 주체나 변경 경위를 문서에 쓰지 마라. 이유: 문서에는 결정 내용만 쓴다.
- 장애 주입 표본 수의 "미정"과 F6 백엔드 최종 선택을 채우지 마라. 이유: 다음 phase의 사전 등록 문서에서 정한다.
- Guardrail v2 분류기(Haiku)의 "확인 필요"를 지우지 마라.
- `docs/results/hypotheses.md`를 수정하지 마라. 이유: 사전 등록 파일이다.
- `AGENTS.md`는 `LLM` 줄과 명령어 블록 외를 수정하지 마라.
- 코드·테스트를 수정하지 마라(문서 일관성 테스트 갱신이 필요하면 이유를 summary에 적는다).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
