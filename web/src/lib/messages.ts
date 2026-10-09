const errors: Record<string, string> = {
    approval_forbidden: "이 요청을 승인할 권한이 없습니다.",
    task_not_found: "승인 요청이 만료되었거나 이미 처리되었습니다.",
    approval_conflict: "초안이 바뀌었거나 이미 처리 중입니다. 화면을 새로 고쳐 주세요.",
    invalid_edit: "수정한 내용을 확인해 주세요. 비워 둘 수 없고 길이 제한이 있습니다.",
    approval_pending: "이 대화에 처리 중인 승인 요청이 있습니다. 먼저 승인하거나 거부해 주세요.",
    turn_in_progress: "이전 요청을 처리하는 중입니다. 잠시 후 다시 시도해 주세요.",
    turn_limit: "이 대화의 턴 한도에 도달했습니다. 새 대화를 시작해 주세요.",
    conversation_not_found: "대화를 찾을 수 없습니다.",
    invalid_request: "요청을 확인해 주세요. 1자 이상 2000자 이하로 입력해야 합니다.",
    upstream_unavailable: "서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
    demo_login_disabled: "데모 로그인이 꺼져 있습니다.", loopback_required: "로컬 접속에서만 로그인할 수 있습니다.", user_not_found: "사용자를 찾을 수 없습니다."
};
export function errorMessage(code: string) { return errors[code] ?? "요청을 처리하지 못했습니다."; }
export function blockCategory(code?: string) { return code === "injection_direct" ? "지시 변경 시도" : code === "pii_input" ? "개인정보 포함" : "정책 차단"; }
export const APPROVED_RUN_NOTICE = "승인된 내용대로 실행했습니다";
export function statusMessage(status?: string, retryHint?: string, error?: string) {
    if (status === "failed" && error === "max_steps") return "조사 단계 한도(8단계)에 도달했습니다. 질문을 더 구체적으로 나누어 다시 요청해 주세요.";
    return ({ running: "답변을 만드는 중입니다…", failed: "처리 중 오류가 발생했습니다. 다시 요청해 주세요.", interrupted: retryHint ?? "다시 요청해 주세요.", rejected: "승인이 거부되었습니다.", expired: "승인 시간이 지나 종료되었습니다.", awaiting_approval: "승인을 기다리는 중입니다.", completed: "답변이 완료되었습니다." } as Record<string, string>)[status ?? ""] ?? "";
}

export const AGENT_DESCRIPTION = "내 권한으로 문서·티켓을 조회하고, 승인을 받은 뒤에만 발송합니다.";
export const COMPOSER_NOTICE = "AI 답변은 틀릴 수 있습니다. 발송은 승인 후에만 실행됩니다.";
export const GENERIC_CHECK_BLOCK = "정책 검사로 보낼 수 없습니다. 내용을 확인하고 다시 요청해 주세요.";
export function toolLabel(name: string): string {
    return ({
        "get_document": "문서 읽기",
        "lookup_id": "ID 조회",
        "run_sql": "데이터 조회",
        "search_docs": "문서 검색",
        "jira_get_ticket": "티켓 조회",
        "jira_search_tickets": "티켓 검색",
    } as Record<string, string>)[name] ?? name;
}
export function sentResultLabel(tool: string): string {
    return ({
        "slack_send_message": "Slack 메시지 ID",
        "jira_add_comment": "Jira 코멘트 ID",
        "jira_create_followup_ticket": "생성된 티켓 ID",
    } as Record<string, string>)[tool] ?? tool;
}
export function approvalMethodLabel(method: string): string {
    return ({approve: "승인", edit_approve: "수정 후 승인"} as Record<string,string>)[method] ?? method;
}
