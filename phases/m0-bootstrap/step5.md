# Step 5: compose-obs

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (기술 스택의 관측 항목, "고정 제약")
- `/docs/ARCHITECTURE.md` ("배치도"와 그 아래 설명: 핵심 스택은 Langfuse 없이 뜨고, Langfuse는 profile `obs`로만 뜬다)
- `/docs/ADR.md` (ADR-009: Langfuse v3 셀프호스팅 구성과 Postgres 공유)
- step 3·4 산출물: `/infra/docker-compose.yml`, `/infra/postgres/init/01-create-databases.sh`, `/tests/test_compose.py`, `/.env.example`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

Langfuse v3 셀프호스팅을 compose **profile `obs`** 로만 추가한다. 핵심 스택(postgres·litellm·mock-jira·mock-slack)은 Langfuse 없이 떠야 한다. compose는 실제로 기동하지 않는다. TDD로 테스트를 먼저 쓴다.

### 만들 것

1. **`infra/docker-compose.yml`에 서비스 추가** (모두 `profiles: ["obs"]`): `langfuse-web`, `langfuse-worker`, `clickhouse`, `redis`, `minio`. Langfuse v3 공식 셀프호스팅 compose 구성을 네가 아는 범위에서 따르되, 구성의 세부(환경 변수 이름, 필요한 버킷 설정 등)는 **확인 필요(구현 시 공식 문서로 확인)** 로 주석에 남긴다.
   - Langfuse의 Postgres는 기존 `postgres` 서비스의 `langfuse` DB를 쓴다(ADR-009, 인스턴스 하나). DB URL은 새 변수를 만들지 말고 compose 안에서 기존 `${POSTGRES_USER}`·`${POSTGRES_PASSWORD}`로 조합한다.
   - 포트는 모두 `127.0.0.1`에만 publish한다(web UI 하나만 publish하고 clickhouse·redis·minio는 publish하지 않아도 된다).
   - 이미지는 태그를 고정하고, 각 `image:` 줄 위에 `확인 필요(첫 기동 전 사용자가 공식 릴리스 노트로 확인)` 주석을 단다.
   - 모든 비밀은 `${VAR}` 참조로만 쓴다.
2. **`.env.example`에 변수 이름 추가**(값 없이 `NAME=`, 용도 주석 포함): Langfuse `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY`, ClickHouse 사용자·비밀번호, Redis 인증, MinIO 루트 사용자·비밀번호 등 compose에 실제로 쓴 `${VAR}` 전부. 기존 줄은 그대로 둔다.

### 테스트 (`tests/test_compose.py` 확장)

- `langfuse-web`, `langfuse-worker`, `clickhouse`, `redis`, `minio`가 모두 `profiles`에 `obs`를 가진다.
- 핵심 4개 서비스(`postgres`, `litellm`, `mock-jira`, `mock-slack`)는 `profiles`가 없고, 어떤 핵심 서비스도 obs 서비스에 `depends_on`하지 않는다.
- 기존 공통 검사(포트 127.0.0.1, 비밀 리터럴 금지, 이미지 태그·`확인 필요` 주석, 모든 `${VAR}` ⊆ `.env.example`, 관리 포트 9000 비공개)가 새 서비스에도 통과한다.
- Langfuse 서비스가 `postgres`를 가리키고, 별도 Postgres 서비스를 추가하지 않았다(서비스 목록에 postgres 계열이 하나뿐).

핵심 규칙:
- 핵심 스택이 obs 없이 떠야 한다. 핵심 서비스에 `profiles`를 붙이거나 obs 서비스에 의존시키지 마라.
- 새 환경 변수는 반드시 `.env.example`에 이름으로 추가한다(step 3의 검사가 강제한다).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest(tests, scripts). docker는 실행하지 않는다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - Langfuse 구성이 profile `obs`에만 속하고 핵심 스택은 영향이 없는가? (ADR-009)
   - Postgres 인스턴스가 하나이고 DB만 나뉘는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `docker`, `docker compose`를 실행하지 마라. 이유: 실제 기동·pull·`docker stats` 실측은 사용자가 한다.
- 비밀 값을 compose에 리터럴로 쓰지 마라. 이유: 값은 사용자 `.env`에만 둔다.
- Langfuse 자체 Postgres 서비스를 추가하지 마라. 이유: ADR-009가 인스턴스 하나에 DB 분리를 정했다.
- Langfuse의 환경 변수 이름이나 이미지 태그 존재를 확정된 사실처럼 단정하지 마라. 이유: 공식 문서 확인이 남은 항목이다.
- LiteLLM의 Langfuse 연동 설정(callback)을 추가하지 마라. 이유: trace 연동은 M2 범위다.
- `.env`와 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- 설치·네트워크 명령을 실행하지 마라.
- `scripts/`, `phases/`, `web/`, `src/govops/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
