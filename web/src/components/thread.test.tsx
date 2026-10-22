import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Thread } from "./thread";
import { Composer } from "./composer";
import { useConversation } from "@/hooks/use-conversation";
import { conversationView, type ConversationView } from "@/lib/types";
import { LIMITED_NOTICE } from "@/lib/messages";
import { testids } from "@/lib/testids";
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
    expect(screen.queryByTestId("approval-card")).toBeNull(); expect(screen.getByTestId("sent-card").textContent).toContain("syn-effect");
    rerender(<Thread view={view("expired")}/>); expect(screen.queryByTestId("approval-card")).toBeNull(); expect(screen.getByTestId("turn-error").textContent).toBe("승인 시간이 지나 종료되었습니다.");
});
test("owner card for a draft the owner cannot approve shows the notice instead of buttons", () => {
    render(<Thread view={view("awaiting_approval", { can_decide: false, draft: { tool: "slack_send_message", args: { channel: "announce", text: "초안" } }, draft_sha256: "a".repeat(64), expires_at: new Date(Date.now() + 60000).toISOString() })}/>);
    expect(screen.getByTestId("approval-not-decidable")).toBeDefined();
    expect(screen.queryByTestId("approval-approve")).toBeNull(); expect(screen.queryByTestId("approval-reject")).toBeNull();
});
test("completed answers and assistant history render markdown without completed status", () => {
    render(<Thread view={{...view("completed", {answer:"**답변**"}),history:[{role:"assistant",content:"**이력**"}]}}/>);
    expect(screen.queryByTestId("turn-status")).toBeNull(); expect(screen.getByTestId("turn-answer").querySelector("strong")?.textContent).toBe("답변"); expect(screen.getByTestId("history-assistant").querySelector("strong")?.textContent).toBe("이력");
});
test("running shows status, skeleton and open safe steps", () => {
    render(<Thread view={view("running", {tool_steps:[{name:"search_docs",ok:true,args:"private-sentinel",result:"private-sentinel"}]})}/>);
    expect(screen.getByTestId("turn-status").textContent).toBe("답변을 만드는 중입니다…"); expect(screen.getByTestId("turn-skeleton").getAttribute("aria-busy")).toBe("true"); expect(screen.getByTestId("turn-skeleton").children).toHaveLength(3); expect(screen.getByTestId("tool-step-row").textContent).toBe("문서 검색"); expect(document.body.innerHTML).not.toContain("private-sentinel");
});
test.each([{check:{message:"검사 안내"},answer:"대체",expected:"검사 안내"},{answer:"대체",expected:"대체"},{answer:undefined,expected:"정책 검사로 보낼 수 없습니다. 내용을 확인하고 다시 요청해 주세요."}])("check block uses ordered fallback", extra => {
    render(<Thread view={view("blocked",extra)}/>); expect(screen.queryByTestId("blocked-card")).toBeNull(); expect(screen.getByTestId("check-blocked-card").textContent).toBe(`발송 차단${extra.expected}`);
});
test.each([["max_steps","조사 단계 한도(8단계)에 도달했습니다. 질문을 더 구체적으로 나누어 다시 요청해 주세요."],["internal_error","처리 중 오류가 발생했습니다. 다시 요청해 주세요."],[undefined,"처리 중 오류가 발생했습니다. 다시 요청해 주세요."]])("failed text depends on error code %s with fallback", (error, message) => {
    render(<Thread view={view("failed", { error })}/>);
    expect(screen.getByTestId("turn-error").textContent).toBe(message);
});
test("fixed approval notice is hidden only while the sent card shows, other answers stay", () => {
    const effect = { mock_id: "syn-effect" };
    const { rerender } = render(<Thread view={view("completed", { answer: "승인된 내용대로 실행했습니다", effect })}/>);
    expect(screen.getByTestId("sent-card").textContent).toContain("syn-effect");
    expect(screen.queryByTestId("turn-answer")).toBeNull();
    rerender(<Thread view={view("completed", { answer: "별도 안내입니다", effect })}/>);
    expect(screen.getByTestId("sent-card")).toBeDefined(); expect(screen.getByTestId("turn-answer").textContent).toContain("별도 안내입니다");
    rerender(<Thread view={view("completed", { answer: "승인된 내용대로 실행했습니다" })}/>);
    expect(screen.queryByTestId("sent-card")).toBeNull(); expect(screen.getByTestId("turn-answer").textContent).toContain("승인된 내용대로 실행했습니다");
});


test("completed limited answer has the fixed notice below it, same answer without limited does not", () => {
 const notice="조사 단계 한도(8단계)에서 멈추고 모은 근거로 답했습니다. 일부 내용이 빠졌을 수 있습니다.";
 expect(LIMITED_NOTICE).toBe(notice);
 const {rerender}=render(<Thread view={conversationView(view("completed",{limited:true}))}/>);
 const answer=screen.getByTestId(testids.turnAnswer);
 const element=screen.getByTestId(testids.turnLimited);
 expect(element).toBeDefined();
 expect(element.textContent).toBe(notice);
 expect(answer.textContent).toBe("답변");
 expect(answer.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
 for(const extra of [{},{limited:false}]) {
 rerender(<Thread view={conversationView(view("completed",extra))}/>);
 expect(screen.getByTestId(testids.turnAnswer).textContent).toBe("답변");
 expect(screen.queryByTestId(testids.turnLimited)).toBeNull();
 expect(screen.queryByText(notice)).toBeNull();
 }
});
test.each([undefined,false,true])("failed max steps shows only the established text even with limited %s", limited => {
 render(<Thread view={conversationView(view("failed",{error:"max_steps",limited}))}/>);
 expect(screen.getByTestId(testids.turnError).textContent).toBe("조사 단계 한도(8단계)에 도달했습니다. 질문을 더 구체적으로 나누어 다시 요청해 주세요.");
 expect(screen.queryByTestId(testids.turnAnswer)).toBeNull();
 expect(screen.queryByTestId(testids.turnLimited)).toBeNull();
 expect(screen.queryByText(LIMITED_NOTICE)).toBeNull();
});
test.each(["running","awaiting_approval","rejected","expired","blocked_input","blocked","interrupted"])("limited notice is absent for status %s", status => {
 render(<Thread view={view(status,{limited:true})}/>);
 expect(screen.queryByTestId(testids.turnLimited)).toBeNull();
 expect(screen.queryByText(LIMITED_NOTICE)).toBeNull();
});
