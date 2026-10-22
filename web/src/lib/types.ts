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
export type ToolStep = { name: string; ok: boolean };
export type TurnView = {
    can_decide?: boolean;
    limited?: boolean;
    tool_steps?: ToolStep[];
    approval_method?: "approve" | "edit_approve";
    effect_at?: string;
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
    can_decide?: boolean;
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
        ...(typeof t.limited === "boolean" ? {limited: t.limited} : {}),
        ...(Array.isArray(t.tool_steps) ? {tool_steps: t.tool_steps.flatMap(item => {
            const step = asObject(item);
            return typeof step.name === "string" && /^[a-z_]{1,40}$/.test(step.name) && typeof step.ok === "boolean" ? [{name: step.name, ok: step.ok}] : [];
        }).slice(0,20)} : {}),
        ...(t.approval_method === "approve" || t.approval_method === "edit_approve" ? {approval_method: t.approval_method} : {}),
        ...(typeof t.effect_at === "string" && t.effect_at.length <= 64 && Number.isFinite(Date.parse(t.effect_at)) ? {effect_at: t.effect_at} : {}),
        ...(!["blocked_input", "blocked"].includes(t.status ?? "") ? { request: t.request } : {}),
        answer: t.answer, retry_hint: t.retry_hint, expires_at: t.expires_at, cited_doc_ids: t.cited_doc_ids,
        ...(typeof t.can_decide === "boolean" ? {can_decide: t.can_decide} : {}),
        error: t.error, draft_sha256: t.draft_sha256, draft: t.draft, check: t.check, rejected_edit: t.rejected_edit,
        effect: t.effect, guardrail: t.guardrail ? { quarantined: t.guardrail.quarantined, quarantined_at_least: t.guardrail.quarantined_at_least } : undefined,
        block: t.block ? { code: t.block.code, message: t.block.message } : undefined
    } : undefined;
    return { conversation_id: value.conversation_id, turn_index: value.turn_index, history: (value.history ?? []).map(h => ({ role: h.role, content: h.content })), turn };
}


export type ConversationListItem = {
    conversation_id: string;
    title: string;
    status: string;
    awaiting_approval: boolean;
    turn_count: number;
    updated_at: string;
};
function asObject(value: unknown): Record<string, unknown> {
    return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
export function conversationListView(value: unknown): ConversationListItem[] {
    if (!Array.isArray(value)) return [];
    const statuses = ["running", "awaiting_approval", "completed", "failed", "rejected", "expired", "blocked_input", "interrupted", "blocked"];
    return value.flatMap(item => {
        const row = asObject(item);
        if (typeof row.conversation_id !== "string" || !/^[0-9a-f]{32}$/.test(row.conversation_id) ||
            typeof row.title !== "string" || row.title.length > 80 ||
            typeof row.status !== "string" || !statuses.includes(row.status) ||
            typeof row.awaiting_approval !== "boolean" ||
            typeof row.turn_count !== "number" || !Number.isInteger(row.turn_count) || row.turn_count < 0 ||
            typeof row.updated_at !== "string" || row.updated_at.length > 64) return [];
        return [{conversation_id: row.conversation_id, title: row.title || "새 대화", status: row.status,
            awaiting_approval: row.awaiting_approval, turn_count: row.turn_count, updated_at: row.updated_at}];
    }).slice(0,50);
}

export type AuditEvent = {
    seq?: number;
    ts?: string;
    event?: string;
    actor?: string;
    tool?: string;
    tool_name?: string;
    status?: string;
    result_count?: number;
    decision?: string;
    check_result?: string;
    reason?: string;
    code?: string;
    mock_id?: string;
    fallback?: boolean;
    cost_usd?: string;
    hash12?: string;
    draft_hash12?: string;
};
export type AuditView = {
    conversation_id?: string;
    turns?: {turn_id: string; turn_index: number; events?: AuditEvent[]}[];
    truncated?: boolean;
};
export type AuditVerifyView = {
    status?: "pass" | "fail";
    checked_at?: string;
    chain?: {records?: number; files?: number; anchors_checked?: number; problems?: {code: string; seq?: number}[]};
    anchors?: {committed?: number; pending?: number};
    legacy_unverified?: {files?: number; lines?: number};
    known_limit?: string;
};
