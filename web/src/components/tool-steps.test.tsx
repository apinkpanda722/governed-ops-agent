import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { ToolSteps } from "./tool-steps";
afterEach(cleanup);
test("summary, expansion and labels render only allowed fields", () => {
    const steps = [{name:"search_docs",ok:true,args:"private-sentinel",result:"private-sentinel"},{name:"unknown_tool",ok:false}];
    render(<ToolSteps steps={steps} defaultOpen={false}/>);
    const trigger = screen.getByRole("button");
    expect(trigger.textContent).toBe("도구 2단계 사용 (실패 1)"); expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("tool-step-row")).toBeNull();
    fireEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true"); expect(screen.getAllByTestId("tool-step-row")).toHaveLength(2);
    expect(screen.getAllByTestId("tool-step-row")[0].textContent).toBe("문서 검색"); expect(screen.getAllByTestId("tool-step-row")[0].title).toBe("search_docs");
    expect(screen.getAllByTestId("tool-step-row")[1].textContent).toBe("unknown_tool"); expect(document.body.innerHTML).not.toContain("private-sentinel");
});
test("defaultOpen exposes successful steps", () => { render(<ToolSteps steps={[{name:"get_document",ok:true}]} defaultOpen/>); expect(screen.getByRole("button").textContent).toBe("도구 1단계 사용"); expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true"); expect(screen.getByTestId("tool-step-row").textContent).toBe("문서 읽기"); });
