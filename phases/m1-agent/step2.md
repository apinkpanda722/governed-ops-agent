# Step 2: agent-key

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (고정 제약의 비밀 값 항목: `.env`는 사람·에이전트가 열거나 출력하지 않는다, 토큰·키를 화면·로그·문서에 남기지 않는다)
- `/docs/ARCHITECTURE.md` ("상태 관리"의 비밀 값 항목), `/docs/ADR.md` (ADR-007, ADR-008의 팀별 가상 키)
- step 1 산출물: `/src/govops/gateway/client.py`, `/tests/test_gateway_client.py`
- `/infra/litellm/config.yaml`(`master_key`, 모델 별칭 `agent-primary`), `/.env.example`(`LITELLM_AGENT_KEY`·`LITELLM_BASE_URL`·`LITELLM_MASTER_KEY` 이름), `/Makefile`, `/tests/test_makefile.py`

## 작업

에이전트 전용 LiteLLM 가상 키 1개를 발급해 `.env`에 기록하는 **패키지 CLI**를 만든다. 사용자가 compose 기동 후 `make agent-key`로 실행한다. 이 CLI는 **마스터 키를 쓰는 유일한 코드**다. 발급된 키와 마스터 키는 화면·로그·예외 어디에도 나오지 않는다. TDD로 테스트를 먼저 쓴다.

### 만들 파일: `src/govops/gateway/agent_key.py` (`python -m govops.gateway.agent_key`)

```python
def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int
# 옵션: --env-file (기본 .env), --base-url (기본: 환경 변수 LITELLM_BASE_URL, 없으면 http://127.0.0.1:4000)
```
동작:
1. 마스터 키는 프로세스 환경 변수 `LITELLM_MASTER_KEY`에서만 읽는다(`make`가 `uv run --env-file .env`로 전달한다. 이 CLI가 `.env` 파일에서 마스터 키를 읽어 오지는 않는다). 없으면 키 값 없이 안내 메시지만 내고 종료 코드 1.
2. **이미 값이 있으면 거부**한다: 환경 변수 `LITELLM_AGENT_KEY`가 비어 있지 않거나 `--env-file`의 `LITELLM_AGENT_KEY=` 줄에 값이 있으면 아무 요청도 보내지 않고 종료 코드 1. 파일이 없어도 종료 코드 1(파일을 새로 만들지 않는다).
3. `POST {base_url}/key/generate`, 헤더 `Authorization: Bearer <마스터 키>`, 본문 `{"models": ["agent-primary"], "key_alias": "govops-agent"}`. 응답이 200이고 JSON의 `key`가 비어 있지 않은 문자열일 때만 성공으로 본다.
4. 발급된 키를 `--env-file`의 `LITELLM_AGENT_KEY=` 줄(값이 빈 줄)에 **제자리 기록**한다. 줄이 없으면 파일 끝에 줄을 추가한다. 다른 줄은 **바이트 단위로 그대로** 보존하고, 파일 권한은 유지한다. 임시 파일에 쓴 뒤 `os.replace`로 교체(원자적)하고 임시 파일은 같은 디렉토리에 0600으로 만든다.
5. 출력: 성공 시 "LITELLM_AGENT_KEY를 .env에 기록했습니다" 한 줄뿐이다. **발급된 키 값, 마스터 키 값, 요청·응답 본문, 헤더는 stdout·stderr·로그·예외 메시지 어디에도 나오지 않는다.** 실패 시에는 사유 종류(마스터 키 없음, 이미 설정됨, HTTP 상태 코드, 연결 실패, 응답 형식 오류)만 출력하고 종료 코드 1이며, 이때 파일은 바이트 단위로 변하지 않는다.
- 네트워크 오류 예외의 문자열화에 요청 헤더가 들어가는 라이브러리 동작이 있어도 출력되지 않게 예외를 잡아 사유 종류만 쓴다.

### Makefile

`agent-key` 타깃 추가(위에 한 줄 주석: "compose 기동 후 실행. 키는 출력되지 않고 .env에 기록된다"): `uv run --env-file .env python -m govops.gateway.agent_key`. `verify` 타깃은 호출하지 않는다. `tests/test_makefile.py`를 확장해 타깃 존재와 `--env-file .env` 사용, `verify`가 이 타깃을 호출하지 않음을 검사한다.

### 테스트 (`tests/test_agent_key.py`)

`.env` 대신 `tmp_path`의 임시 env 파일과 `httpx.MockTransport`를 쓴다. 실제 `.env`를 열거나 만들지 마라.
- 성공: 빈 `LITELLM_AGENT_KEY=` 줄이 채워지고, 다른 줄(주석·빈 줄·다른 변수)이 바이트 단위로 같으며 파일 권한이 유지된다. 줄이 없는 파일에는 끝에 추가된다. 요청은 `/key/generate`, `Authorization` 헤더가 마스터 키 센티널이고 본문에 키·마스터 키가 없다.
- **비노출(센티널)**: 마스터 키 센티널(`MASTER-SENTINEL`)과 발급 키 센티널(`ISSUED-SENTINEL`)이 `capsys`의 stdout·stderr, `caplog`(DEBUG, `httpx`·`httpcore` 포함), 모든 성공·실패 경로의 반환 코드와 예외 문자열에 **나타나지 않는다**. 성공 후에도 발급 키는 env 파일에만 있다.
- **이미 값이 있으면 거부**: (a) 파일에 값이 있는 `LITELLM_AGENT_KEY=<값>` 줄 (b) 환경 변수 `LITELLM_AGENT_KEY` 설정 — 두 경우 모두 transport가 받은 요청 수 0, 파일 바이트 불변, 종료 코드 1. 값이 비어 있는 줄은 거부하지 않는다.
- 실패 경로(401·500·연결 오류·응답에 `key` 없음·`key`가 빈 문자열·JSON 아님): 종료 코드 1, 파일 바이트 불변, 출력에 센티널 없음.
- 마스터 키 환경 변수가 없으면 요청 없이 종료 코드 1. env 파일이 없으면 요청 없이 종료 코드 1이고 파일이 생기지 않는다.
- **저장소 규칙 테스트**: `src/govops` 아래에서 문자열 `LITELLM_MASTER_KEY`를 포함하는 파일은 `gateway/agent_key.py` 하나뿐이다(`gateway/client.py`·`api/`·`agent/` 등에 없음).
- 계층 가드 통과.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 발급 키·마스터 키가 출력·로그·예외 어디에도 없는가? 이미 값이 있으면 거부하는가?
   - 실패 시 env 파일이 바이트 단위로 보존되는가? 마스터 키 참조가 이 파일 하나뿐인가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 실제 `.env`를 열거나 읽거나 수정하지 마라. 이유: AGENTS.md CRITICAL. 테스트는 `tmp_path`의 임시 파일만 쓴다.
- 이 CLI를 실제 LiteLLM에 대해 실행하지 마라. 이유: 발급은 사용자가 compose 기동 후 `make agent-key`로 한다.
- 발급된 키·마스터 키·요청/응답 본문을 어떤 출력·로그·예외·테스트 실패 메시지에도 남기지 마라.
- `scripts/`가 아니라 패키지(`src/govops/gateway/`)에 두어라. 이유: `scripts/`는 하네스 전용이다.
- 마스터 키를 앱의 다른 모듈(`gateway/client.py`, `api/` 등)에서 참조하게 만들지 마라.
- docker, 설치·네트워크 명령을 실행하지 마라. A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
