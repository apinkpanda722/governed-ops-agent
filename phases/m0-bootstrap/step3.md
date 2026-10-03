# Step 3: compose-core

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (기술 스택, "고정 제약", mock 관리 엔드포인트 규칙)
- `/docs/ARCHITECTURE.md` ("배치도"와 그 아래 설명, "디렉토리 구조"의 `infra/`)
- `/docs/ADR.md` (ADR-004·ADR-007·ADR-008·ADR-009)
- `/.env.example` (변수 이름만 있는 파일. 이 step에서 이름을 **덧붙인다**)
- step 0~2 산출물: `/pyproject.toml`, `/Makefile`, `/tests/test_repo_rules.py`, `/tests/test_architecture.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

compose 핵심 스택 중 **postgres와 litellm**을 만든다(mock 2개는 step 4, Langfuse는 step 5). compose는 실제로 기동하지 않는다. 검증은 YAML을 파싱하는 테스트로만 한다. TDD로 테스트를 먼저 쓴다.

### 만들 파일

1. **`infra/docker-compose.yml`** (`name: govops`)
   - `postgres`: 이미지 태그 고정(`latest` 금지). 환경 변수는 `${POSTGRES_USER}`, `${POSTGRES_PASSWORD}` 참조. 포트는 `127.0.0.1:5432:5432`. 이름 있는 볼륨, healthcheck(`pg_isready`). `infra/postgres/init/`을 init 디렉토리로 마운트한다.
   - `litellm`: 이미지 태그 고정. `infra/litellm/config.yaml`을 마운트하고 `--config`로 읽는다. 포트 `127.0.0.1:4000:4000`. 환경 변수는 `${LITELLM_MASTER_KEY}`, `${LITELLM_DATABASE_URL}`, `${OPENAI_API_KEY}`, `${ANTHROPIC_API_KEY}`를 `${}` 참조로만 전달한다. `postgres`의 healthy 상태에 의존한다.
   - 각 `image:` 줄 바로 위에 주석으로 `확인 필요(첫 기동 전 사용자가 공식 릴리스 노트로 확인)`를 적는다. 태그는 네가 아는 실존 태그 중 하나를 고르되 존재 여부를 단정하지 않는다.
2. **`infra/postgres/init/01-create-databases.sh`**: 인스턴스 하나에 DB `litellm`, `langfuse` 두 개를 만든다(ADR-009). LangGraph checkpointer DB는 만들지 않는다(M1에서 결정, ARCHITECTURE.md "미정").
3. **`infra/litellm/config.yaml`**: `model_list`에 주 모델 별칭 1개(OpenAI `gpt-6-luna`, `api_key: os.environ/OPENAI_API_KEY`)와 `general_settings`(master key와 DB URL은 `os.environ/...` 참조). fallback(Anthropic Sonnet 계열)과 Guardrail 훅은 **주석으로 자리만** 남기고 "확인 필요(구현 시 공식 문서로 확인): 모델 ID·가격" / "Guardrail은 M4"라고 적는다. 모델 ID를 추측해서 채우지 마라.
4. **`.env.example`에 변수 이름 추가**(값 없이 `NAME=`, 용도 주석 포함): `POSTGRES_USER`, `POSTGRES_PASSWORD`, `LITELLM_MASTER_KEY`, `LITELLM_DATABASE_URL`. 기존 줄과 주석은 그대로 두고, 파일 끝의 "LiteLLM·Postgres·Langfuse 변수는 M0 compose step에서 이름이 추가된다" 주석을 실제 추가한 것에 맞게 갱신한다.

### 테스트 (`tests/test_compose.py`, 이후 step 4·5가 이어서 확장한다)

`pyyaml`(설치됨)으로 `infra/docker-compose.yml`을 읽어 검사한다:
- `postgres`, `litellm` 서비스가 있다.
- 모든 `ports` 항목이 `127.0.0.1:`로 시작한다(전체 인터페이스 바인드 금지).
- 이름에 `PASSWORD`/`KEY`/`SECRET`/`SALT`/`TOKEN`이 들어간 환경 변수의 값은 리터럴이 아니라 `${...}` 참조다.
- 모든 `image:`가 `latest`가 아닌 태그를 가지고, 바로 위 줄이 `확인 필요`가 들어간 주석이다(원문 텍스트로 검사).
- **compose 안의 모든 `${VAR}`(`${VAR:-기본값}`, `${VAR:?메시지}` 형태 포함)가 `.env.example`에 이름으로 존재한다.** 이 검사는 이후 step에서 서비스가 늘어도 그대로 적용된다.
- init 스크립트가 `litellm`, `langfuse` DB를 만든다(스크립트 텍스트 검사).

핵심 규칙:
- 비밀 값(비밀번호·키)을 compose·config·스크립트에 리터럴로 쓰지 마라. 값은 사용자 `.env`에만 있다.
- 비용 상한 값은 설정 파일이나 환경 변수에 넣지 않는다(ADR-007, 코드 설정은 M1).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest(tests, scripts). docker는 실행하지 않는다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - Postgres 인스턴스 하나에 `litellm`·`langfuse` DB만 있고 checkpointer DB는 만들지 않았는가?
   - 모든 포트가 `127.0.0.1`에 묶였는가? 비밀이 리터럴로 들어가지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `docker`, `docker compose`를 실행하지 마라(`config` 포함). 이유: 실제 기동·pull은 사용자가 한다. 검증은 YAML 파싱 테스트로 한다.
- `.env`와 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- `.env.example`에 값을 넣지 마라. 이유: 이름만 둔다. step 2의 검사가 이를 강제한다.
- Anthropic 모델 ID·가격, 이미지 태그의 존재 여부를 확정된 사실처럼 쓰지 마라. 이유: "확인 필요" 항목이다.
- mock 서비스·Langfuse 서비스를 이 step에서 추가하지 마라. 이유: step 4·5 소관이다.
- `scripts/`, `phases/`, `web/`, `src/govops/` 계층 패키지를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
