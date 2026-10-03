#!/usr/bin/env bash
# Stop hook: `make verify` 가 실패하면 에이전트가 멈추지 않고 계속 수정하도록 되돌린다.
#
# Claude Code와 Codex 모두 Stop hook 이 exit 2 + stderr(계속할 이유)일 때 세션을 이어간다.
set -uo pipefail

input=$(cat 2>/dev/null || true)
stop_hook_active=$(jq -r '.stop_hook_active // false' <<< "$input" 2>/dev/null)

# 이미 Stop hook 때문에 이어진 세션이면 다시 검증하지 않는다 (무한 루프 방지).
[[ "$stop_hook_active" == "true" ]] && exit 0

cwd=$(jq -r '.cwd // empty' <<< "$input" 2>/dev/null)
[[ -z "$cwd" ]] && cwd="$(pwd)"

# 검증 진입점이 없거나, 이번 세션에서 바뀐 파일이 없으면(읽기 전용 리뷰 등) 통과시킨다.
[[ -f "$cwd/Makefile" ]] || exit 0
[[ -z "$(git -C "$cwd" status --porcelain 2>/dev/null)" ]] && exit 0

output=$(cd "$cwd" && make verify 2>&1)
status=$?

if [[ $status -ne 0 ]]; then
  echo "make verify 가 실패했습니다. 아래 출력을 확인하고 수정을 계속하세요:" >&2
  echo "$output" | tail -n 80 >&2
  exit 2
fi

exit 0
