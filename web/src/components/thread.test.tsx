import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Thread } from "./thread";
import { Composer } from "./composer";
import { useConversation } from "@/hooks/use-conversation";
import type { ConversationView } from "@/lib/types";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function view(status = "completed", extra = {}): ConversationView { return { conversation_id: "id", history: [], turn: { turn_id: "t", status, answer: "답변", cited_doc_ids: ["syn-doc"], ...extra } }; }
function Harness() { const c = useConversation("id"); return <><Thread view={c.view} blockedCards={c.blockedCards}/><Composer onSubmit={c.submitTurn}/></>; }
test("blocked card survives subsequent turn without retaining blocked input", async () => {
    const sentinel = "blocked-private-sentinel";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(view()))).mockResolvedValueOnce(new Response('{}', { status: 202 })).mockResolvedValueOnce(new Response(JSON.stringify(view("blocked_input", { request: sentinel, block: { code: "injection_direct", message: "요청을 바꾸어 주세요." } })))).mockResolvedValueOnce(new Response('{}', { status: 202 })).mockResolvedValueOnce(new Response(JSON.stringify({ ...view(), history: [{ role: "user", content: "다음 요청" }, { role: "assistant", content: "답변" }] }))));
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("turn-answer")).toBeDefined(), { timeout: 1000 });
    const input = screen.getByTestId("composer-input") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: sentinel } });
    fireEvent.click(screen.getByTestId("composer-submit"));
    await waitFor(() => expect(screen.getByTestId("blocked-card")).toBeDefined(), { timeout: 1000 });
    expect(screen.getAllByTestId("blocked-card")).toHaveLength(1);
    expect(screen.getByTestId("blocked-card").outerHTML.includes(sentinel)).toBe(false);
    expect(input.value.length).toBe(0);
    fireEvent.change(input, { target: { value: "다음 요청" } });
    fireEvent.click(screen.getByTestId("composer-submit"));
    await waitFor(() => expect(screen.getByTestId("history-user")).toBeDefined(), { timeout: 1000 });
    expect(screen.getByTestId("blocked-card")).toBeDefined();
    expect(document.body.outerHTML.includes(sentinel)).toBe(false);
    expect(input.value.length).toBe(0);
});
test.each([0, 2])("quarantine count %s", count => { render(<Thread view={view("completed", { guardrail: { quarantined: count, quarantined_at_least: true } })}/>); if (count) {
    expect(screen.getByTestId("quarantine-note").textContent).toContain("참고 문서 일부 격리");
    expect(screen.getByTestId("quarantine-note").textContent).toContain("2건 이상");
}
else
    expect(screen.queryByTestId("quarantine-note")).toBeNull(); });
test("unknown keys and blocked request never render", () => {
    const sentinel = "private-sentinel";
    const v = view("blocked_input", { request: sentinel, block: { code: "other", message: "안내" }, masked: sentinel, stop_reason: sentinel, rules: sentinel, observed_doc_ids: [sentinel] });
    render(<Thread view={v}/>);
    expect(screen.getByTestId("blocked-card")).toBeDefined();
    expect(document.body.outerHTML.includes(sentinel)).toBe(false);
});
test.each([["failed", "처리 중 오류가 발생했습니다. 다시 요청해 주세요."], ["rejected", "승인이 거부되었습니다."], ["expired", "승인 시간이 지나 종료되었습니다."], ["interrupted", "다시 시도 안내"]])("status %s shows safe text", (status, message) => { render(<Thread view={view(status, { error: "internal_code", retry_hint: "다시 시도 안내" })}/>); expect(screen.getByTestId("turn-error").textContent).toBe(message); });
test("citations contain only IDs and cards appear at their history position", () => {
    render(<Thread view={{ ...view(), history: [{ role: "user", content: "첫째" }, { role: "assistant", content: "둘째" }] }} blockedCards={[{ turn_id: "b", afterMessages: 1, block: { code: "other", message: "차단" } }]}/>);
    expect(screen.getByTestId("turn-cited-doc").textContent).toBe("syn-doc");
    expect(screen.getByTestId("thread").textContent).toMatch(/첫째[\s\S]*차단[\s\S]*둘째/);
});
test("approval slot forwards refresh and shows completed effect only after approval", async () => {
    const changed = vi.fn(); vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"status":"running"}', { status: 202 })));
    const { rerender } = render(<Thread onChanged={changed} view={view("awaiting_approval", { draft: { tool: "slack_send_message", args: { channel: "platform", text: "초안" } }, draft_sha256: "a".repeat(64), expires_at: new Date(Date.now() + 60000).toISOString() })}/>);
    fireEvent.click(screen.getByTestId("approval-approve")); await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    rerender(<Thread view={view("completed", { effect: { mock_id: "syn-effect" } })}/>);
    expect(screen.queryByTestId("approval-card")).toBeNull(); expect(screen.getByTestId("approval-result").textContent).toContain("syn-effect");
    rerender(<Thread view={view("expired")}/>); expect(screen.queryByTestId("approval-card")).toBeNull(); expect(screen.getByTestId("turn-error").textContent).toBe("승인 시간이 지나 종료되었습니다.");
});
