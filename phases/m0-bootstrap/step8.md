# Step 8: docs-sync

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/PRD.md`, `/docs/ARCHITECTURE.md`, `/docs/ADR.md` (기존 ADR의 항목 형식 "상태 / 결정 / 이유 / 트레이드오프 / 변경 기록"을 그대로 따른다)
- `/README.md`
- step 0~7 산출물: `/pyproject.toml`, `/Makefile`, `/infra/docker-compose.yml`, `/infra/litellm/config.yaml`, `/infra/postgres/init/`, `/infra/mocks/Dockerfile`, `/src/govops/mocks/` (service.py, admin.py, `__main__.py`), `/web/package.json`, `/web/.nvmrc`, `/tests/`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

M0에서 확정된 내용을 문서에 반영한다. 코드는 수정하지 않는다(문서 전용 step). "확인 필요"·"미정" 표기는 **실제로 이 phase에서 결정된 것만** 지우고, 나머지는 그대로 둔다.

1. **`docs/ADR.md`에 ADR-016 추가** (ADR-015 다음, 기존 형식 그대로). 제목 예: "M0 골격 결정 — web 호스트 실행, mock 관리 접근 경로, checkpointer DB 보류". 담을 내용:
   - **web은 호스트에서 `pnpm dev`로 실행하고 compose에 넣지 않는다.** 이유: 배치도가 BFF를 호스트에 두고, 에이전트 서비스도 호스트에서 `uv run`으로 돈다(ADR-004).
   - **mock 관리 포트 분리**: REST 포트(컨테이너 8000)만 `127.0.0.1`에 publish하고, 관리 포트(9000)는 publish하지 않는다. 호스트에서의 접근 경로는 컨테이너 안 CLI `docker compose exec <mock> python -m govops.mocks.admin <명령>`이다. 평가 하네스의 상태 조회와 M3 장애 주입이 이 경로를 쓴다. M0은 CLI 골격(`health`)까지만 만들었고 상태 조회·초기화·장애 주입 명령은 M1·M3에서 추가한다.
   - **LangGraph checkpointer DB는 M0에서 만들지 않는다.** Postgres init은 `litellm`·`langfuse` DB만 만든다. checkpointer DB 위치는 M1에서 결정한다.
   - **패키지 이름 `govops` 확정**, Python 3.12, uv 기반, 계층 패키지 11개 + `costs.py`.
   - **`.env.example` 원칙**: 변수 이름만 두고 값은 `.env`에만 둔다. 비용 상한은 환경 변수가 아니라 코드 설정(ADR-007 값)이며 `costs.py`에서 M1에 구현한다.
   - **`make verify` 구성**: ruff check → ruff format --check → pytest → web typecheck → eslint → vitest. API 키·네트워크 없이 통과한다.
   - 변경 기록에 `2026-10-03: M0에서 확정` 한 줄.
2. **`docs/ARCHITECTURE.md`**:
   - "패키지 이름 `govops`는 미정(M0에서 확정)이다." → 확정으로 고친다.
   - "web을 호스트에서 `pnpm`으로 띄우는지 compose에 넣는지는 미정(M0에서 결정)이다." → 호스트 실행으로 확정(ADR-016 참조).
   - mock 관리 엔드포인트 문단의 "경로는 미정(M3에서 결정)" → 접근 경로는 컨테이너 안 CLI로 확정(ADR-016), 장애 주입 명령 내용은 M3로 남긴다.
   - 디렉토리 구조의 `infra/`에 실제 구성(`postgres/init`, `litellm/config.yaml`, `mocks/Dockerfile`, `docker-compose.yml`)을 반영한다.
3. **`AGENTS.md`**: "패키지 이름 `src/govops/`는 M0에서 확정한다" → 확정으로 고친다. 기술 스택의 uv·Python 버전 문구("버전은 M0에서 고정")를 실제 값(Python 3.12, `pyproject.toml`/`.python-version`)으로, web 검사 문구("M0 phase에서 추가한다")를 추가 완료로 고친다. "명령어" 절의 `make verify` 설명을 현재 구성(위 6단계)으로 바꾼다. **CRITICAL 규칙은 한 줄도 바꾸지 마라.**
4. **`docs/PRD.md`**: 마일스톤 표 아래 "`make verify`는 현재 하네스 자체 테스트(pytest)만 돈다…" 문단을 현재 상태(M0에서 ruff·pytest·tsc·eslint·vitest 추가 완료, Playwright e2e는 M5 이후)로 고친다. 표의 M0 행은 그대로 둔다.
5. **`README.md`**: 현재 내용 아래에 짧은 "개발 환경" 절을 추가한다(`uv run`, `make verify`, web은 `cd web && pnpm dev`, compose는 `docker compose -f infra/docker-compose.yml up`·`--profile obs`). 사용자가 직접 할 일(키 발급과 `.env` 입력, compose 첫 기동과 `docker stats` 실측, 이미지 태그의 공식 릴리스 노트 확인)을 목록으로 적는다.

## Acceptance Criteria

```bash
make verify   # 문서 변경이므로 전체 검사가 그대로 통과해야 한다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 문서 일관성을 확인한다:
   - `grep -n "미정(M0" docs/*.md AGENTS.md`에 M0에서 결정된 항목이 남아 있지 않은가?
   - ADR-016이 실제 산출물(경로·명령 이름)과 일치하는가?
   - AGENTS.md CRITICAL 규칙이 변경되지 않았는가? (`git diff -- AGENTS.md`로 CRITICAL 줄 변경 없음 확인)
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 코드·테스트·compose·Makefile을 수정하지 마라. 이유: 문서 전용 step이다. 문서와 코드가 어긋나면 문서를 코드에 맞춘다.
- AGENTS.md의 CRITICAL 규칙과 계층 경계 표를 바꾸지 마라. 이유: 규칙 원본이며 테스트가 이를 따른다.
- M0에서 결정되지 않은 "미정"·"확인 필요" 표기(이미지 태그, 모델 ID·가격, checkpointer DB 위치, Langfuse annotation queue 등)를 지우지 마라. 이유: 실제로 결정·확인되지 않았다.
- 압박 검증(Q1~Q23)·설계 문서의 다른 결정 내용을 재작성하지 마라. 이유: 이번 변경과 무관하다.
- `.env`와 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- 설치·네트워크 명령을 실행하지 마라.
- `scripts/`, `phases/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
