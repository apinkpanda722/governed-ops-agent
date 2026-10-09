import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { SentCard } from "./sent-card";
import type { Draft, TurnView } from "@/lib/types";
afterEach(cleanup);
test.each([
 ["slack_send_message","발송 완료","Slack 메시지 ID",{channel:"syn-channel",text:"수정본"}],
 ["jira_add_comment","코멘트 등록 완료","Jira 코멘트 ID",{ticket_id:"SYN-1",body:"수정본"}],
 ["jira_create_followup_ticket","티켓 생성 완료","생성된 티켓 ID",{parent_ticket_id:"SYN-1",title:"제목",description:"수정본"}],
] as [Draft["tool"],string,string,Draft["args"]][])("sent %s", (tool,title,label,args) => {
 const turn = {status:"completed",draft:{tool,args},approval_method:"edit_approve",effect_at:new Date(2026,9,8,3,4).toISOString(),effect:{mock_id:"syn-effect",reason:"private-sentinel"},request:"private-sentinel"} as TurnView;
 render(<SentCard turn={turn}/>);
 expect(screen.getByText(title)).toBeDefined(); expect(screen.getByTestId("sent-result-id").textContent).toBe(`${label}: syn-effect`);
 expect(screen.getByTestId("sent-method").textContent).toBe("수정 후 승인"); expect(screen.getByTestId("sent-at").textContent).toContain("03:04");
 expect(screen.getByTestId("sent-body").textContent).toContain("수정본"); expect(screen.getByTestId("sent-target").textContent).toBe(args.channel ?? args.ticket_id ?? args.parent_ticket_id);
 expect(document.body.innerHTML).not.toMatch(/private-sentinel|reason/);
});
test("long body stays in DOM when folded", () => {
 const text = "긴 본문".repeat(60);
 render(<SentCard turn={{draft:{tool:"slack_send_message",args:{text}},approval_method:"approve",effect:{mock_id:"syn-id"}}}/>);
 expect(screen.getByTestId("sent-method").textContent).toBe("승인");
 const button = screen.getByRole("button"); expect(button.textContent).toBe("펼치기"); expect(button.getAttribute("aria-expanded")).toBe("false");
 expect(screen.getByTestId("sent-body").textContent).toBe(text); fireEvent.click(button); expect(button.getAttribute("aria-expanded")).toBe("true"); expect(button.textContent).toBe("접기"); fireEvent.click(button); expect(screen.getByTestId("sent-body").textContent).toBe(text);
});
test("missing values omit rows and draftless receipt shows ID only", () => {
 render(<SentCard turn={{effect:{mock_id:"syn-id"}}}/>);
 expect(screen.getByTestId("sent-result-id").textContent).toContain("syn-id");
 for(const id of ["sent-target","sent-body","sent-method","sent-at"]) expect(screen.queryByTestId(id)).toBeNull();
});

test("seven lines fold, invalid time and empty fields omit rows", () => {
 const text = "a\nb\nc\nd\ne\nf\ng";
 render(<SentCard turn={{draft:{tool:"jira_add_comment",args:{body:text}},effect_at:"invalid",effect:{}}}/>);
 expect(screen.getByTestId("sent-body").textContent).toBe(text);
 expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("false");
 for(const id of ["sent-target","sent-method","sent-at","sent-result-id"]) expect(screen.queryByTestId(id)).toBeNull();
});
