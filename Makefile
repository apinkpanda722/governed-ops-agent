.PHONY: verify

# 하네스의 단일 검증 진입점. Stop hook, scripts/execute.py, step AC가 모두 이 타깃을 호출한다.
# Python lint·포맷·pytest 이후 web tsc·eslint·vitest·playwright 설정/스펙 로드(--list, 브라우저·서버 불필요)를 순서대로 실행한다.
# API 키·네트워크 없이 설치된 의존성만 사용하며, 실패한 단계에서 즉시 중단한다.
verify:
	@echo "==> ruff check"
	uv run ruff check .
	@echo "==> ruff format --check"
	uv run ruff format --check .
	@echo "==> pytest"
	uv run pytest -q
	@echo "==> web typecheck (tsc)"
	@command -v pnpm >/dev/null || { echo "pnpm이 필요합니다 (사용자가 설치)"; exit 1; }
	pnpm -C web typecheck
	@echo "==> web lint (eslint)"
	pnpm -C web lint
	@echo "==> web test (vitest)"
	pnpm -C web test
	@echo "==> playwright config/spec load (--list)"
	pnpm -C web exec playwright test --list

A_ROOT ?= $(HOME)/PycharmProjects/ko-docs-agentic-rag
SMOKE_USER ?= demo-platform-g3

.PHONY: mock-tokens seed-extract record-a smoke-contract smoke-identity smoke-visibility smoke-mock smoke-host-header

# mock 코드가 바뀐 PR을 머지한 뒤에는 `docker compose -f infra/docker-compose.yml up -d --build mock-jira mock-slack`으로 mock 이미지를 다시 빌드한다.
# users 시드 필요; mock 인증 토큰·해시 파일 생성.
mock-tokens:
	uv run python -m govops.mocks.tokens

# 읽기 전용 A 시드 필요; 신원·권한 검증 후 결정론 추출.
seed-extract:
	uv run python -m govops.mocks.seed_extract --a-root $(A_ROOT)

# live A·사용자 토큰 필요; 계약 일치와 합성 응답만 기록.
record-a:
	uv run --env-file .env python -m govops.tools.record_a --requests data/replay/requests.json --out data/replay/a

# live A·사용자 토큰 필요; 고정 계약 일치.
smoke-contract:
	uv run --env-file .env python -m govops.tools.smoke contract

# live A·mock-jira·시드·토큰 필요; platform 가시·finance 비가시.
smoke-identity:
	uv run --env-file .env python -m govops.tools.smoke identity

# live A·mock-jira·시드·토큰 필요; 38명 × 40건 불일치 0건.
smoke-visibility:
	uv run --env-file .env python -m govops.tools.smoke visibility

# compose mock·시드·토큰 필요; 채널 9개·타 팀 403·잘못된 토큰 401.
smoke-mock:
	uv run --env-file .env python -m govops.tools.smoke mock

# live A·사용자 토큰 필요; 인증된 위조 Host 요청 421 또는 403 거절.
smoke-host-header:
	uv run --env-file .env python -m govops.tools.smoke host-header --user $(SMOKE_USER)


.PHONY: cost-report
# 원장 읽기 전용, 네트워크 불필요
cost-report:
	uv run python -m govops.costs report

.PHONY: costs-init
costs-init:
	uv run python -m govops.costs init

.PHONY: agent-key
# compose 기동 후 실행. 키는 출력되지 않고 .env에 기록된다
agent-key:
	uv run --env-file .env python -m govops.gateway.agent_key

.PHONY: agent-key-update
# compose LiteLLM 기동·`.env` 키 필요, 사용자 실행, 기존 키의 허용 모델 갱신
agent-key-update:
	uv run --env-file .env python -m govops.gateway.agent_key --update

.PHONY: db-init
# compose 기동 후 실행, 이미 있으면 변경 없음
db-init:
	docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres sh -c 'printf "%s\n" "SELECT '\''CREATE DATABASE govops'\'' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '\''govops'\'');" "\gexec" | psql --username "$$POSTGRES_USER" --dbname postgres --set ON_ERROR_STOP=1'

.PHONY: checkpoint-prune
# 기본은 dry-run, 삭제는 APPLY=1. compose Postgres·make db-init 후 사용자 실행
checkpoint-prune:
	uv run --env-file .env python -m govops.api.retention $(if $(APPLY),--apply,)

.PHONY: serve ask
# compose·live A 기동 후, DEMO_LOGIN=true 필요
serve:
	uv run --env-file .env python -m govops.api

# 예시: make ask AS=demo-platform-g3 Q="티켓을 요약해 주세요"
ask:
	uv run --env-file .env python -m govops.api.cli ask --as $(AS) "$(Q)"

.PHONY: smoke-obs
# compose 핵심 스택 + make obs-up + live A + Langfuse 키 필요, 사용자 실행
smoke-obs:
	uv run --env-file .env python -m govops.api.smoke obs

.PHONY: smoke-checkpointer smoke-agent
# compose·make db-init 필요, 쓰기·읽기 왕복
smoke-checkpointer:
	uv run --env-file .env python -m govops.api.smoke checkpointer

# live A·LiteLLM·make agent-key 필요, 실제 호출 수 센트, 기대값: 두 사용자 completed·platform만 syn-incident-022 관찰·인용·원장 증가
smoke-agent:
	uv run --env-file .env python -m govops.api.smoke agent

.PHONY: smoke-semantic
# 실제 API 호출, GOVOPS_GUARDRAIL_V2=on·compose·.env 필요, 사용자 실행
smoke-semantic:
	uv run --env-file .env python -m govops.api.smoke_semantic

.PHONY: eval-meta-extract
# 읽기 전용 A 필요, 사용자 실행
eval-meta-extract:
	uv run python -m govops.eval.meta_extract --a-root $(A_ROOT)

.PHONY: eval-gen
# meta.json 필요, 사용자 실행
eval-gen:
	uv run python -m govops.eval.taskgen

.PHONY: obs-up obs-down
# 사용자 실행
obs-up:
	LITELLM_CONFIG_FILE=./litellm/config.obs.yaml docker compose --env-file .env -f infra/docker-compose.yml --profile obs up -d
	@# 바인드 마운트한 설정 파일의 내용만 바뀌면 compose가 재생성하지 않으므로 LiteLLM은 항상 재생성한다
	LITELLM_CONFIG_FILE=./litellm/config.obs.yaml docker compose --env-file .env -f infra/docker-compose.yml --profile obs up -d --force-recreate --no-deps litellm

# 사용자 실행
obs-down:
	docker compose --env-file .env -f infra/docker-compose.yml --profile obs stop langfuse-web langfuse-worker clickhouse redis minio
	LITELLM_CONFIG_FILE=./litellm/config.yaml docker compose --env-file .env -f infra/docker-compose.yml up -d litellm


.PHONY: eval-run-dev
# live A·LiteLLM·mock·Postgres·hypotheses-m3.md 커밋 필요, dev 20 WITH_WRITES, 사용자 실행
eval-run-dev:
	uv run --env-file .env python -m govops.evalrun.dev_m3

.PHONY: redteam-run
# live A·LiteLLM·mock(오버레이)·Postgres 필요, 사용자 실행
redteam-run:
	uv run --env-file .env python -m govops.evalrun.redteam_run --config $(CONFIG) --run $(RUN)

.PHONY: redteam-judge
redteam-judge:
	uv run python -m govops.eval.redteam_judge $(JUDGE_ARGS)

.PHONY: run-verdict
run-verdict:
	uv run python -m govops.eval.run_summary verdict $(VERDICT_ARGS)

.PHONY: test-outbox-pg
# compose Postgres 기동·make db-init 후 사용자 실행, 테스트 전용 DB URL 필요
test-outbox-pg:
	uv run --env-file .env python -c 'import os, subprocess, sys; sys.exit(subprocess.call(["pytest", "tests/test_outbox_pg.py", "-q"]) if os.environ.get("GOVOPS_TEST_DATABASE_URL") else (print("GOVOPS_TEST_DATABASE_URL: 테스트 전용 DB URL 필요") or 1))'



.PHONY: smoke-effects
# compose mock·토큰 필요, 사용자 실행
smoke-effects:
	uv run --env-file .env python -m govops.tools.smoke effects --user $(SMOKE_USER)

.PHONY: scope-index
# 오프라인·결정론 생성. A의 커밋된 manifest·접근 정책만 읽으며 키·네트워크 불필요.
scope-index:
	uv run python -m govops.eval.scope_index --a-root "$(A_ROOT)"


.PHONY: smoke-approval
# live A·LiteLLM·compose mock·Postgres·`.env` 필요, 실제 호출 수 센트, 사용자 실행
smoke-approval:
	uv run --env-file .env python -m govops.evalrun.dev_m3 --smoke


.PHONY: eval-faults
# live A·LiteLLM·mock·Postgres·hypotheses-m3-faults.md 커밋 필요, 154회 약 75~80분, 사용자 실행 `caffeinate -i make eval-faults`
eval-faults:
	uv run --env-file .env python -m govops.evalrun.faults


.PHONY: smoke-faults
# live LiteLLM·mock·docker compose 필요, 실제 호출 1회, 사용자 실행
smoke-faults:
	uv run --env-file .env python -m govops.evalrun.faults_smoke


.PHONY: team-keys team-keys-update
# 사용자 실행, 팀별 키는 env 파일에만 기록한다. TEAM_KEYS_ARGS=--rotate 지원
team-keys:
	uv run --env-file .env python -m govops.gateway.team_keys $(TEAM_KEYS_ARGS)

team-keys-update:
	uv run --env-file .env python -m govops.gateway.team_keys --update $(TEAM_KEYS_ARGS)


.PHONY: eval-cards eval-review-status eval-human-sheet eval-human-register eval-gemini-grade smoke-gemini smoke-fallback
# 읽기 전용 A 저장소(A_ROOT) 필요, 사용자 실행
eval-cards:
	uv run python -m govops.eval.review_cli cards --a-root $(A_ROOT)

# 사용자 실행
eval-review-status:
	uv run python -m govops.eval.review_cli status

OBS ?= data/eval/observations.jsonl
CSV ?= data/eval/human/scores.csv
# 사용자 실행
eval-human-sheet:
	uv run --env-file .env python -m govops.eval.grading_cli human-sheet --observations $(OBS)

# 사용자 실행
eval-human-register:
	uv run --env-file .env python -m govops.eval.grading_cli register-human --csv $(CSV)

# 사용자 실행
eval-gemini-grade:
	uv run --env-file .env python -m govops.eval.grading_cli grade --observations $(OBS) --out data/eval/grading

# 사용자 실행
smoke-gemini:
	uv run --env-file .env python -m govops.eval.grading_cli smoke

# compose LiteLLM 기동(설정 반영: `docker compose up -d litellm`)·`.env` 키 필요, 사용자 실행, 소액 호출
smoke-fallback:
	uv run --env-file .env python -m govops.gateway.smoke fallback

.PHONY: smoke-guardrail
# live LiteLLM·팀 키·재기동된 LiteLLM 필요, 실제 호출 수 센트, 사용자 실행
smoke-guardrail:
	uv run --env-file .env python -m govops.gateway.smoke_guardrail

.PHONY: redteam-gen
# 오프라인·결정론 생성, 사전 등록 커밋 필요, 키·네트워크 불필요
redteam-gen:
	uv run python -m govops.eval.redteam

.PHONY: redteam-cards
# 오프라인 카드 생성, 판정은 사람이 내려받은 파일만 인정, 사용자 실행
redteam-cards:
	uv run python -m govops.eval.redteam_cards

.PHONY: redteam-review-status
# 사람 확정본 읽기 전용, 사용자 실행
redteam-review-status:
	uv run python -m govops.eval.redteam_cards --status

.PHONY: audit-verify
# 읽기 전용·키 불필요, 저장소 `data/results/`의 커밋된 체인과 legacy도 함께 보고, `AUDIT_DIR`은 환경 변수로만 바꾼다
audit-verify:
	uv run python -m govops.audit.verify --scan data/results

.PHONY: audit-anchor
# 사용자 실행. 체인에 고정점을 덧붙이고 대기 파일에 같은 줄을 기록한다. 사용: make audit-anchor LABEL=m4-audit-first. 첫 쓰기는 체인 genesis를 만든다
audit-anchor:
	@if [ -z "$(LABEL)" ]; then echo "사용: make audit-anchor LABEL=m4-audit-first" >&2; exit 2; fi
	uv run python -m govops.audit.anchor --label "$(LABEL)"

.PHONY: smoke-audit
# 키·네트워크 불필요, 임시 체인에서 변조 5종·꼬리 잘라내기 재현, 실제 키 권한·짝·체인 확인, 사용자 실행
smoke-audit:
	uv run python -m govops.audit.smoke

.PHONY: smoke-conversation test-m5-pg
# make serve(DEMO_LOGIN=true)·live A·LiteLLM·compose mock 기동 후 사용자 실행, 실제 호출 수 센트
smoke-conversation:
	uv run --env-file .env python -m govops.api.smoke_conversation

# compose Postgres 기동·make db-init 후 사용자 실행, 테스트 전용 DB URL 필요(임시 DB를 만들고 지운다)
test-m5-pg:
	uv run --env-file .env python -c 'import os, subprocess, sys; sys.exit(subprocess.call(["pytest", "tests/test_m5_pg.py", "-q"]) if os.environ.get("GOVOPS_TEST_DATABASE_URL") else (print("GOVOPS_TEST_DATABASE_URL: 테스트 전용 DB URL 필요") or 1))'

.PHONY: e2e-web
# e2e는 추적 파일을 쓰지 않는다. 이미 변경된 파일의 내용 변경은 같은 status 줄이면 감지하지 못한다.
# 사용자 실행 포트 3200·8200·8201이 이미 쓰이고 있으면 Playwright가 시작을 거부한다(기존 서버 재사용 금지). e2e는 `next build`로 `web/.next`를 덮어쓰므로 `next start`로 띄운 데모 서버(3100)는 먼저 끈다(`next dev`는 `.next/dev`를 써서 무관). e2e가 새로 만든 변경만 실패.
e2e-web:
	@before_status=$$(mktemp); after_status=$$(mktemp); \
	git status --porcelain | sort > "$$before_status"; \
	pnpm -C web exec playwright test; e2e_status=$$?; \
	git status --porcelain | sort > "$$after_status"; clean_status=0; \
	changed_paths=$$(comm -13 "$$before_status" "$$after_status"); \
	if [ -n "$$changed_paths" ]; then printf '%s\n' "$$changed_paths" | cut -c4-; clean_status=1; fi; \
	rm -f "$$before_status" "$$after_status"; \
	if [ "$$e2e_status" -ne 0 ]; then exit "$$e2e_status"; fi; \
	exit "$$clean_status"
