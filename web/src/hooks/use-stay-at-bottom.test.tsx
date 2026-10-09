import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useRef } from "react";
import { useStayAtBottom } from "./use-stay-at-bottom";
const observers: { callback: () => void; disconnect: ReturnType<typeof vi.fn> }[] = [];
beforeEach(() => {
    observers.length = 0;
    vi.stubGlobal("ResizeObserver", class { disconnect = vi.fn(); constructor(callback: () => void) { observers.push({ callback, disconnect: this.disconnect }); } observe() {} unobserve() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function Box({ pinned }: { pinned: { current: boolean } }) {
    const scroll = useRef<HTMLDivElement>(null);
    useStayAtBottom(scroll, pinned);
    return <div ref={scroll} data-testid="box"/>;
}
function box() { return document.querySelector<HTMLDivElement>('[data-testid="box"]')!; }
test("keeps the scroll pinned to the bottom when the viewport shrinks and the user was at the bottom", () => {
    render(<Box pinned={{ current: true }}/>);
    Object.defineProperty(box(), "scrollHeight", { configurable: true, value: 668 });
    expect(observers).toHaveLength(1);
    box().scrollTop = 286; observers[0].callback();
    expect(box().scrollTop).toBe(668);
});
test("does not move the scroll when the user scrolled up", () => {
    render(<Box pinned={{ current: false }}/>);
    Object.defineProperty(box(), "scrollHeight", { configurable: true, value: 668 });
    box().scrollTop = 100; observers[0].callback();
    expect(box().scrollTop).toBe(100);
});
test("disconnects the observer on unmount", () => {
    const view = render(<Box pinned={{ current: true }}/>);
    view.unmount();
    expect(observers[0].disconnect).toHaveBeenCalledOnce();
});
