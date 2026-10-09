import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ApprovalCard, type ApprovalView } from "./approval-card";
import type { Draft } from "@/lib/types";
const draft: Draft = { tool: "slack_send_message", args: { channel: "platform", text: "원래 본문" } };
function view(extra: Partial<ApprovalView> = {}): ApprovalView { return { turn_id: "t", status: "awaiting_approval", draft, draft_sha256: "a".repeat(64), check: { message: "서버 검사 안내" }, expires_at: new Date(Date.now() + 60000).toISOString(), ...extra }; }
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
test.each(["approve", "reject"] as const)("%s sends only decision and current hash", async decision => {
    const f = vi.fn().mockResolvedValue(response({ status: "rejected" })), changed = vi.fn(); vi.stubGlobal("fetch", f);
    render(<ApprovalCard view={view()} onChanged={changed}/>);
    expect(screen.getByTestId("approval-target").textContent).toBe("platform");
    expect(screen.getByTestId("approval-body").textContent).toContain("원래 본문");
    expect(screen.getByTestId("approval-check").textContent).toBe("서버 검사 안내");
    fireEvent.click(screen.getByTestId(`approval-${decision}`));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(f).toHaveBeenCalledWith("/api/tasks/t/approval", expect.objectContaining({ method: "POST", body: JSON.stringify({ decision, draft_sha256: "a".repeat(64) }) }));
    expect(screen.getByTestId("approval-result").textContent).toBe("거부했습니다");
});
test.each([
    [draft, ["text"]],
    [{ tool: "jira_add_comment", args: { ticket_id: "SYN-1", body: "원래 본문" } }, ["body"]],
    [{ tool: "jira_create_followup_ticket", args: { parent_ticket_id: "SYN-1", title: "원래 제목", description: "원래 설명", priority: "high" } }, ["title", "description"]],
] as [Draft, string[]][])("editable fields for %j", async (d, fields) => {
    const f = vi.fn().mockResolvedValue(response({ status: "completed", effect: { mock_id: "syn-effect" } })); vi.stubGlobal("fetch", f);
    render(<ApprovalCard view={view({ draft: d })} onChanged={vi.fn()}/>);
    fireEvent.click(screen.getByTestId("approval-edit-toggle"));
    expect(screen.getAllByRole("textbox").map(e => e.getAttribute("data-testid"))).toEqual(fields.map(f => `approval-edit-input-${f}`));
    expect((screen.getByTestId("approval-edit-submit") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId(`approval-edit-input-${fields[0]}`), { target: { value: "   " } });
    expect((screen.getByTestId("approval-edit-submit") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId(`approval-edit-input-${fields[0]}`), { target: { value: "새 본문" } });
    fireEvent.click(screen.getByTestId("approval-edit-submit"));
    await waitFor(() => expect(screen.getByTestId("approval-result").textContent).toContain("syn-effect"));
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ decision: "edit_approve", draft_sha256: "a".repeat(64), edited: { [fields[0]]: "새 본문" } });
    expect(screen.getByTestId("approval-result").textContent).toContain("처리했습니다");
});
test("double click has one request and disables all buttons until 202", async () => {
    let resolve!: (r: Response) => void;
    const f = vi.fn(() => new Promise<Response>(r => { resolve = r; })), changed = vi.fn(); vi.stubGlobal("fetch", f);
    render(<ApprovalCard view={view()} onChanged={changed}/>);
    fireEvent.click(screen.getByTestId("approval-approve")); fireEvent.click(screen.getByTestId("approval-approve"));
    expect(f).toHaveBeenCalledOnce();
    for (const button of screen.getAllByRole("button")) expect((button as HTMLButtonElement).disabled).toBe(true);
    await act(async () => resolve(response({ status: "running" }, 202)));
    expect(screen.getByTestId("approval-result").textContent).toBe("발송을 처리하는 중입니다…");
    expect(changed).toHaveBeenCalledOnce();
});
test.each([
    [403, "approval_forbidden", "이 요청을 승인할 권한이 없습니다."],
    [404, "task_not_found", "승인 요청이 만료되었거나 이미 처리되었습니다."],
    [409, "approval_conflict", "초안이 바뀌었거나 이미 처리 중입니다. 화면을 새로 고쳐 주세요."],
    [422, "invalid_edit", "수정한 내용을 확인해 주세요. 비워 둘 수 없고 길이 제한이 있습니다."],
])("error %s uses safe message and refreshes", async (status, code, message) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ code, detail: "private-sentinel" }, Number(status)))); const changed = vi.fn();
    render(<ApprovalCard view={view()} onChanged={changed}/>); fireEvent.click(screen.getByTestId("approval-approve"));
    await waitFor(() => expect(screen.getByTestId("approval-result").textContent).toBe(message));
    expect(changed).toHaveBeenCalledOnce(); expect(document.body.textContent).not.toContain("private-sentinel");
});
test("rejected edit retains original draft and next decision uses refreshed hash", async () => {
    const f = vi.fn().mockImplementation(() => Promise.resolve(response({ status: "awaiting_approval" }, 202))); vi.stubGlobal("fetch", f);
    const props = { onChanged: vi.fn() }; const { rerender } = render(<ApprovalCard {...props} view={view()}/>);
    fireEvent.click(screen.getByTestId("approval-edit-toggle")); fireEvent.change(screen.getByTestId("approval-edit-input-text"), { target: { value: "차단될 수정" } }); fireEvent.click(screen.getByTestId("approval-edit-submit"));
    await waitFor(() => expect(props.onChanged).toHaveBeenCalledOnce());
    rerender(<ApprovalCard {...props} view={view({ draft_sha256: "b".repeat(64), rejected_edit: { message: "정책 안내" } })}/>);
    expect(screen.getByTestId("approval-rejected-edit").textContent).toBe("수정한 내용이 검사를 통과하지 못했습니다: 정책 안내");
    expect(screen.getByTestId("approval-body").textContent).toBe("원래 본문");
    fireEvent.click(screen.getByTestId("approval-reject"));
    await waitFor(() => expect(f).toHaveBeenCalledTimes(2));
    expect(JSON.parse(f.mock.calls[1][1].body)).toEqual({ decision: "reject", draft_sha256: "b".repeat(64) });
});
test("countdown expires, refreshes once and disables every button", async () => {
    vi.useFakeTimers(); const changed = vi.fn();
    render(<ApprovalCard view={view({ expires_at: new Date(Date.now() + 2000).toISOString() })} onChanged={changed}/>);
    expect(screen.getByTestId("approval-expires").textContent).toContain("2초");
    await act(async () => vi.advanceTimersByTimeAsync(1000)); expect(screen.getByTestId("approval-expires").textContent).toContain("1초");
    await act(async () => vi.advanceTimersByTimeAsync(1000)); expect(screen.getByTestId("approval-expired").textContent).toBe("승인 시간이 지났습니다");
    for (const button of screen.getAllByRole("button")) expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(changed).toHaveBeenCalledOnce(); await act(async () => vi.advanceTimersByTimeAsync(3000)); expect(changed).toHaveBeenCalledOnce();
});
test("empty content field is omitted when another body field changes", async () => {
    const f = vi.fn().mockResolvedValue(response({ status: "completed" })); vi.stubGlobal("fetch", f);
    render(<ApprovalCard view={view({ draft: { tool: "jira_create_followup_ticket", args: { parent_ticket_id: "SYN-1", title: "제목", description: "설명" } } })} onChanged={vi.fn()}/>);
    fireEvent.click(screen.getByTestId("approval-edit-toggle")); fireEvent.change(screen.getByTestId("approval-edit-input-title"), { target: { value: "새 제목" } }); fireEvent.change(screen.getByTestId("approval-edit-input-description"), { target: { value: "" } }); fireEvent.click(screen.getByTestId("approval-edit-submit"));
    await waitFor(() => expect(f).toHaveBeenCalledOnce()); expect(JSON.parse(f.mock.calls[0][1].body).edited).toEqual({ title: "새 제목" });
});

test.each([["passed", "검사 통과"], ["other", "확인 필요"]])("badge %s and button hierarchy", (status, label) => {
 render(<ApprovalCard view={view({check:{status,message:"안내"},requester_label:"demo-platform-g3"})} onChanged={vi.fn()}/>);
 expect(screen.getByText(label)).toBeDefined();
 expect(screen.getByTestId("approval-requester").textContent).toBe("플랫폼개발팀 · 3급");
 expect(screen.getByTestId("approval-requester").title).toBe("demo-platform-g3");
 expect(screen.getByTestId("approval-expires").textContent).toMatch(/^남은 시간 \d+분 \d+초$/);
 expect(screen.getAllByRole("button").map(b=>b.textContent)).toEqual(["거부","수정 후 승인","승인"]);
 expect(screen.getAllByRole("button").map(b=>b.getAttribute("data-hierarchy"))).toEqual(["danger","secondary","primary"]);
});
test("unparsed requester is unchanged", () => {
 render(<ApprovalCard view={view({requester_label:"syn-requester"})} onChanged={vi.fn()}/>);
 expect(screen.getByTestId("approval-requester").textContent).toBe("syn-requester");
});
