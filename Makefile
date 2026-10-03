.PHONY: verify

# 하네스의 단일 검증 진입점. Stop hook, scripts/execute.py, step AC가 모두 이 타깃을 호출한다.
# 프로젝트 셋업(/harness 0단계)에서 스택에 맞는 lint / typecheck / test 를 채운다. 예:
#   Python: uv run ruff check . && uv run pytest -q
#   Node:   npm run lint && npm run build && npm test
# 기본값은 하네스 자체 테스트만 실행한다.
verify:
	python3 -m pytest -q scripts
