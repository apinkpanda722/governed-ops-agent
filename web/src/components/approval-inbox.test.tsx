import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ApprovalInbox } from "./approval-inbox";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const row = (id: string) => ({ turn_id: id, status: "awaiting_approval", requester_label: `demo-${id}`, draft: { tool: "slack_send_message", args: { channel: "announce", text: `본문-${id}` } }, draft_sha256: "a".repeat(64), check: { message: `검사-${id}` }, expires_at: new Date(Date.now() + (id === "first" ? 60000 : 120000)).toISOString(), request: "private-request", answer: "private-answer", history: [{ content: "private-history" }] });
test("empty inbox has no rows", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"approvals":[]}'))); render(<ApprovalInbox/>);
    await waitFor(() => expect(screen.getByTestId("inbox-empty").textContent).toBe("대기 중인 승인 요청이 없습니다."));
    expect(screen.queryAllByTestId("inbox-row")).toHaveLength(0);
});
test("rows keep server expiry order, project allowed fields, link only requester conversation and refresh after decision", async () => {
    const f = vi.fn().mockImplementation((path: string) => Promise.resolve(new Response(JSON.stringify(path === "/api/approvals" ? { approvals: [{ ...row("first"), conversation_id: "conv" }, row("second")] } : { status: "rejected" })))); vi.stubGlobal("fetch", f);
    render(<ApprovalInbox/>); await waitFor(() => expect(screen.getAllByTestId("inbox-row")).toHaveLength(2));
    const rows = screen.getAllByTestId("inbox-row");
    rows.forEach((r, i) => { const id = i ? "second" : "first"; expect(within(r).getByTestId("approval-requester").textContent).toBe(`demo-${id}`); expect(within(r).getByTestId("approval-body").textContent).toBe(`본문-${id}`); expect(within(r).getByTestId("approval-target").textContent).toBe("announce"); expect(within(r).getByTestId("approval-check").textContent).toBe(`검사-${id}`); });
    expect(screen.getAllByTestId("approval-open-conversation")).toHaveLength(1); expect(screen.getByTestId("approval-open-conversation").getAttribute("href")).toBe("/c/conv");
    expect(document.body.textContent).not.toMatch(/private-request|private-answer|private-history/);
    fireEvent.click(within(rows[0]).getByTestId("approval-reject")); await waitFor(() => expect(f.mock.calls.filter(c => c[0] === "/api/approvals")).toHaveLength(2));
});
