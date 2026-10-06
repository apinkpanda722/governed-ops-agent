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

# phase 실행 중(execute.py가 .harness/executing에 PID를 적어 둔다)이면 중간 작업 트리를
# 검증하지 않는다. 그 PID가 죽어 있으면(비정상 종료) 잠금은 무효다. 실행기의 자손
# 프로세스(step을 실행하는 Codex 세션)는 계속 검증해 스스로 고치게 한다.
lock="$cwd/.harness/executing"
if [[ -f "$lock" ]]; then
  lock_pid=$(tr -dc '0-9' < "$lock" 2>/dev/null | head -c 10)
  if [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" 2>/dev/null; then
    owned=false
    p=$$
    for _ in $(seq 1 30); do
      p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
      [[ -z "$p" || "$p" -le 1 ]] && break
      if [[ "$p" == "$lock_pid" ]]; then owned=true; break; fi
    done
    [[ "$owned" == "false" ]] && exit 0
  fi
fi

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
