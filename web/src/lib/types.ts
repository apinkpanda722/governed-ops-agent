export type ApiError = {
    status: number;
    code: string;
};
export type BlockView = {
    code?: string;
    message?: string;
};
export type CheckView = {
    status?: string;
    message?: string;
};
export type Draft = {
    tool: "slack_send_message" | "jira_add_comment" | "jira_create_followup_ticket";
    args: {
        channel?: string;
        text?: string;
        ticket_id?: string;
        body?: string;
        parent_ticket_id?: string;
        title?: string;
        description?: string;
        priority?: string;
    };
};
export type TurnView = {
    turn_id?: string;
    status?: string;
    request?: string | null;
    answer?: string;
    retry_hint?: string;
    expires_at?: string;
    cited_doc_ids?: string[];
    error?: string;
    draft_sha256?: string;
    draft?: Draft;
    check?: CheckView;
    rejected_edit?: CheckView;
    effect?: {
        state?: string;
        mock_id?: string;
    };
    guardrail?: {
        quarantined?: number;
        quarantined_at_least?: boolean;
    };
    block?: BlockView;
};
export type ConversationView = {
    conversation_id?: string;
    turn_index?: number;
    history: {
        role: "user" | "assistant";
        content: string;
    }[];
    turn?: TurnView;
};
export type ApprovalRow = {
    turn_id?: string;
    status?: string;
    requester_label?: string;
    conversation_id?: string;
    draft?: Draft;
    draft_sha256?: string;
    check?: CheckView;
    expires_at?: string;
};
export type BlockedCardView = {
    turn_id: string;
    block: BlockView;
    afterMessages: number;
};
export type SubmitResult = {
    code?: string;
    conversation_id?: string;
};
// Copy the presentation contract before putting a response into React state.
export function conversationView(value: ConversationView): ConversationView {
    const t = value.turn;
    const turn: TurnView | undefined = t ? {
        turn_id: t.turn_id, status: t.status,
        ...(!["blocked_input", "blocked"].includes(t.status ?? "") ? { request: t.request } : {}),
        answer: t.answer, retry_hint: t.retry_hint, expires_at: t.expires_at, cited_doc_ids: t.cited_doc_ids,
        error: t.error, draft_sha256: t.draft_sha256, draft: t.draft, check: t.check, rejected_edit: t.rejected_edit,
        effect: t.effect, guardrail: t.guardrail ? { quarantined: t.guardrail.quarantined, quarantined_at_least: t.guardrail.quarantined_at_least } : undefined,
        block: t.block ? { code: t.block.code, message: t.block.message } : undefined
    } : undefined;
    return { conversation_id: value.conversation_id, turn_index: value.turn_index, history: (value.history ?? []).map(h => ({ role: h.role, content: h.content })), turn };
}
