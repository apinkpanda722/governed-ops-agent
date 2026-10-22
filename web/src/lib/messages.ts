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
export const SELF_APPROVAL_NOTICE = "전사 공지는 요청자 본인이 승인할 수 없습니다. 같은 팀 4급 이상의 다른 승인자가 승인함에서 승인해야 합니다.";
export const APPROVED_RUN_NOTICE = "승인된 내용대로 실행했습니다";
export const LIMITED_NOTICE = "조사 단계 한도(8단계)에서 멈추고 모은 근거로 답했습니다. 일부 내용이 빠졌을 수 있습니다.";
export const DELETE_CONFIRM_NOTICE = "대화를 삭제할까요? 원문은 다음 정리 때 제거되고, 감사 기록과 이미 발송된 효과는 남는다.";
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

export const RECORDS_NOT_FOUND = "기록을 찾을 수 없습니다. 정리된 대화는 기록 화면에서 볼 수 없고 체인은 audit-verify로 확인합니다";
export const RECORDS_TAIL_NOTICE = "마지막 고정점 이후는 탐지되지 않습니다";
export const RECORDS_HOST_NOTICE = "이 호스트의 체인 기준입니다";
export const RECORDS_TRUNCATED_NOTICE = "이벤트가 많아 일부 기록만 표시합니다.";
export function auditEventLabel(code: string): string {
    if (code.startsWith("guardrail.")) return "Guardrail 판정";
    return ({
        "model.call": "모델 호출", "conversation.deleted": "대화 삭제",
        "tool.call": "도구 호출",
        "side_effect.draft": "발송 초안", "side_effect.attempt": "발송 시도", "side_effect.result": "발송 결과",
        "side_effect.marker_lookup": "표식 조회", "approval.requested": "승인 요청", "approval.approved": "승인 완료",
        "approval.edited": "초안 수정", "approval.rejected": "승인 거부", "approval.expired": "승인 만료",
    } as Record<string,string>)[code] ?? code;
}
export function auditCodeLabel(code: string): string {
    return ({
        ok: "성공", tool_error: "도구 오류 결과", tool_not_available: "허용되지 않은 도구",
        invalid_argument: "인자 오류", tool_unavailable: "도구 호출 실패",
        proposal_invalid: "잘못된 발송 제안", not_executed: "실행하지 않음",
        approve: "승인", edit_approve: "수정 후 승인", reject: "거부", expired: "만료",
        passed: "검사 통과", blocked: "차단", recipient_not_allowed: "허용되지 않은 수신처",
        pii_blocked_by_team_policy: "팀 개인정보 정책 차단", scope_wider_than_evidence: "근거보다 넓은 발송 범위",
        evidence_scope_unknown: "근거 범위 확인 불가", ticket_not_found: "티켓 없음",
        audit_gap: "감사 기록 누락", already_succeeded: "이미 실행 완료", already_failed: "이미 실행 실패",
        in_progress: "처리 중", audit_write_failed: "감사 기록 실패", circuit_open: "연결 일시 중단",
        connect: "연결 실패", rate_limited: "호출 제한", uncertain: "결과 불확실", definitive: "확정 실패",
        unsent_confirmed: "미발송 확인", injection_direct: "지시 변경 시도", pii_input: "개인정보 포함",
        guardrail_unavailable: "Guardrail 확인 불가", team_unknown: "팀 확인 불가",
        guardrail_header_missing: "Guardrail 응답 누락", guardrail_stale: "Guardrail 설정 불일치",
        no_chain: "체인 없음", unterminated_tail: "미완성 레코드", invalid_json: "레코드 형식 오류",
        invalid_record: "유효하지 않은 레코드", hash_mismatch: "내용 해시 불일치", prev_hash_mismatch: "체인 연결 불일치",
        seq_order: "레코드 순서 오류", seq_gap: "레코드 누락", kid_unknown: "알 수 없는 서명 키",
        sig_invalid: "서명 오류", genesis_missing: "시작 레코드 없음", legacy_sha256_mismatch: "이전 기록 해시 불일치",
        tail_truncated: "꼬리 잘림", anchor_missing: "고정점 없음", anchor_hash_mismatch: "고정점 해시 불일치",
        anchor_sig_invalid: "고정점 서명 오류", anchor_dir_missing: "고정점 체인 없음", anchor_file_invalid: "고정점 파일 오류",
    } as Record<string,string>)[code] ?? code;
}
export function auditEventTitle(code: string, checkResult?: string): string {
    if (code === "side_effect.draft" && checkResult) return "발송 초안 검사";
    return auditEventLabel(code);
}
export function auditActorLabel(code: string): string {
    if (code === "approval.requested") return "요청자";
    if (["approval.approved", "approval.edited", "approval.rejected", "approval.expired"].includes(code)) return "결정자";
    return "사용자";
}
