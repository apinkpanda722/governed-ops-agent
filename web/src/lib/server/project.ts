type Obj = Record<string, unknown>;
export function object(value: unknown): Obj {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Obj : {};
}
const text = (v: unknown): v is string => typeof v === "string" && v.length <= 2000;
const id = (v: unknown): v is string => typeof v === "string" && /^[A-Za-z0-9._-]{1,80}$/.test(v);
const hex = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{64}$/i.test(v);
const statuses = ["running", "awaiting_approval", "completed", "failed", "rejected", "expired", "blocked_input", "interrupted", "blocked"];
function strings(input: Obj, keys: string[], validate = text): Obj {
  const result: Obj = {};
  for (const key of keys) if (validate(input[key])) result[key] = input[key];
  return result;
}
function check(value: unknown): Obj {
  return strings(object(value), ["status", "message"]);
}
function draft(value: unknown): Obj | undefined {
  const d = object(value);
  if (typeof d.tool !== "string" || !["slack_send_message", "jira_add_comment", "jira_create_followup_ticket"].includes(d.tool)) return;
  return { tool: d.tool, args: strings(object(d.args), ["channel", "text", "ticket_id", "body", "parent_ticket_id", "title", "description", "priority"]) };
}
export function projectTurn(value: unknown): Obj {
  const t = object(value);
  const out: Obj = strings(t, ["turn_id"], id);
  if (!out.turn_id && id(t.task_id)) out.turn_id = t.task_id;
  if (typeof t.status === "string" && statuses.includes(t.status)) out.status = t.status;
  if (typeof t.limited === "boolean") out.limited = t.limited;
  if (typeof t.can_decide === "boolean") out.can_decide = t.can_decide;
  Object.assign(out, strings(t, ["answer", "retry_hint", "expires_at"]));
  if (!["blocked_input", "blocked"].includes(String(t.status)) && text(t.request)) out.request = t.request;
  if (Array.isArray(t.cited_doc_ids)) out.cited_doc_ids = t.cited_doc_ids.filter(id);
  if (typeof t.error === "string" && /^[a-z_]{1,40}$/.test(t.error)) out.error = t.error;
  if (hex(t.draft_sha256)) out.draft_sha256 = t.draft_sha256;
  const d = draft(t.draft); if (d) out.draft = d;
  for (const key of ["check", "rejected_edit"]) if (t[key] !== undefined && Object.keys(check(t[key])).length) out[key] = check(t[key]);
  if (t.effect !== undefined) out.effect = strings(object(t.effect), ["state", "mock_id"], id);
  if (t.guardrail !== undefined) {
    const g = object(t.guardrail), result: Obj = {};
    if (typeof g.quarantined === "number" && Number.isInteger(g.quarantined) && g.quarantined >= 0) result.quarantined = g.quarantined;
    if (typeof g.quarantined_at_least === "boolean") result.quarantined_at_least = g.quarantined_at_least;
    out.guardrail = result;
  }
  if (["blocked_input", "blocked"].includes(String(t.status)) && t.block !== undefined) {
    const b = object(t.block), result = strings(b, ["message"]);
    if (typeof b.code === "string" && ["injection_direct", "pii_input", "other"].includes(b.code)) result.code = b.code;
    out.block = result;
  }
  if (Array.isArray(t.tool_steps)) out.tool_steps = t.tool_steps.flatMap(item => {
    const step = object(item);
    return typeof step.name === "string" && /^[a-z_]{1,40}$/.test(step.name) && typeof step.ok === "boolean"
      ? [{ name: step.name, ok: step.ok }] : [];
  }).slice(0, 20);
  if (typeof t.approval_method === "string" && ["approve", "edit_approve"].includes(t.approval_method)) out.approval_method = t.approval_method;
  if (typeof t.effect_at === "string" && t.effect_at.length <= 64 && Number.isFinite(Date.parse(t.effect_at))) out.effect_at = t.effect_at;
  return out;
}
export function projectConversation(value: unknown): Obj {
  const v = object(value), out = strings(v, ["conversation_id"], id);
  if (typeof v.turn_index === "number" && Number.isInteger(v.turn_index) && v.turn_index >= 0) out.turn_index = v.turn_index;
  if (Array.isArray(v.history)) out.history = v.history.flatMap(item => {
    const h = object(item);
    return typeof h.role === "string" && ["user", "assistant"].includes(h.role) && text(h.content) ? [{ role: h.role, content: h.content }] : [];
  });
  if (v.turn !== undefined) out.turn = projectTurn(v.turn);
  return out;
}
export function projectCreated(value: unknown): Obj {
  const v = object(value), out = strings(v, ["conversation_id", "turn_id"], id);
  if (typeof v.turn_index === "number" && Number.isInteger(v.turn_index) && v.turn_index >= 0) out.turn_index = v.turn_index;
  if (typeof v.status === "string" && statuses.includes(v.status)) out.status = v.status;
  return out;
}
export function projectDeleted(value: unknown): { deleted: boolean } {
  return { deleted: object(value).deleted === true };
}
export function projectApprovals(value: unknown): Obj {
  const v = object(value);
  if (!Array.isArray(v.approvals)) return {};
  return { approvals: v.approvals.map(item => {
    const row = object(item), result: Obj = strings(row, ["requester_label"], id);
    if (id(row.task_id)) result.turn_id = row.task_id;
    if (typeof row.status === "string" && statuses.includes(row.status)) result.status = row.status;
    const d = draft(row.draft); if (d) result.draft = d;
    if (typeof row.can_decide === "boolean") result.can_decide = row.can_decide;
    if (hex(row.draft_sha256)) result.draft_sha256 = row.draft_sha256;
    if (Object.keys(check(row.check)).length) result.check = check(row.check);
    if (text(row.expires_at)) result.expires_at = row.expires_at;
    if (typeof row.conversation_id === "string" && /^[0-9a-f]{32}$/.test(row.conversation_id)) result.conversation_id = row.conversation_id;
    return result;
  }) };
}
export function projectUser(value: unknown): Obj { return strings(object(value), ["user_id"], id); }
export function projectUsers(value: unknown): Obj {
  const users = object(value).users;
  return Array.isArray(users) ? { users: users.filter(v => typeof v === "string" && /^demo-[a-z-]+-g[0-9]+$/.test(v)) } : {};
}

export function projectConversationList(value: unknown): Obj {
  const v = object(value);
  if (!Array.isArray(v.conversations)) return {};
  return { conversations: v.conversations.flatMap(item => {
    const row = object(item);
    if (typeof row.conversation_id !== "string" || !/^[0-9a-f]{32}$/.test(row.conversation_id) ||
        typeof row.title !== "string" || row.title.length > 80 ||
        typeof row.status !== "string" || !statuses.includes(row.status) ||
        typeof row.awaiting_approval !== "boolean" ||
        typeof row.turn_count !== "number" || !Number.isInteger(row.turn_count) || row.turn_count < 0 ||
        typeof row.updated_at !== "string" || row.updated_at.length > 64) return [];
    return [{
      conversation_id: row.conversation_id, title: row.title || "새 대화",
      status: row.status, awaiting_approval: row.awaiting_approval,
      turn_count: row.turn_count, updated_at: row.updated_at,
    }];
  }).slice(0, 50) };
}

const auditId = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{32}$/.test(v);
const count = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
function auditString(pattern: RegExp) {
  return (v: unknown): v is string => typeof v === "string" && !/[0-9a-f]{64}/i.test(v) && pattern.test(v);
}
const auditCode = auditString(/^[a-z_]{1,40}$/);
const auditTime = (v: unknown): v is string => typeof v === "string" && v.length <= 64 &&
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(v) && Number.isFinite(Date.parse(v));
function counts(input: unknown, keys: string[]): Obj {
  const v = object(input), out: Obj = {};
  for (const key of keys) if (count(v[key])) out[key] = v[key];
  return out;
}
function projectAuditEvent(value: unknown): Obj {
  const v = object(value), out: Obj = counts(v, ["seq"]);
  Object.assign(out, strings(v, ["ts"], auditTime),
    strings(v, ["event"], auditString(/^[a-z_.]{1,40}$/)),
    strings(v, ["actor"], auditString(/^demo-[a-z-]+-g[0-9]+$/)),
    strings(v, ["tool", "decision", "check_result", "reason", "code", "tool_name", "status"], auditCode),
    strings(v, ["mock_id"], auditString(/^[A-Za-z0-9._-]{1,40}$/)),
    strings(v, ["cost_usd"], auditString(/^[0-9]+\.[0-9]{6}$/)),
    strings(v, ["hash12", "draft_hash12"], auditString(/^[0-9a-f]{12}$/)));
  if (typeof v.fallback === "boolean") out.fallback = v.fallback;
  if (count(v.result_count) && v.result_count <= 10000) out.result_count = v.result_count;
  return out;
}
export function projectAudit(value: unknown): Obj {
  const v = object(value), out = strings(v, ["conversation_id"], auditId);
  if (typeof v.truncated === "boolean") out.truncated = v.truncated;
  if (Array.isArray(v.turns)) out.turns = v.turns.flatMap(value => {
    const turn = object(value);
    if (!auditId(turn.turn_id) || !count(turn.turn_index)) return [];
    return [{turn_id: turn.turn_id, turn_index: turn.turn_index,
      ...(Array.isArray(turn.events) ? {events: turn.events.map(projectAuditEvent)} : {})}];
  });
  return out;
}
export function projectVerify(value: unknown): Obj {
  const v = object(value), out: Obj = strings(v, ["checked_at"], auditTime);
  if (v.status === "pass" || v.status === "fail") out.status = v.status;
  if (v.chain !== undefined) {
    const chain = object(v.chain), result = counts(chain, ["records", "files", "anchors_checked"]);
    if (Array.isArray(chain.problems)) result.problems = chain.problems.flatMap(value => {
      const problem = object(value);
      return auditCode(problem.code) ? [{code: problem.code, ...counts(problem, ["seq"])}] : [];
    });
    out.chain = result;
  }
  if (v.anchors !== undefined) out.anchors = counts(v.anchors, ["committed", "pending"]);
  if (v.legacy_unverified !== undefined) out.legacy_unverified = counts(v.legacy_unverified, ["files", "lines"]);
  if (v.known_limit === "tail_after_last_anchor_not_detected") out.known_limit = v.known_limit;
  return out;
}
