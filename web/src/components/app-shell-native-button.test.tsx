import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AppShell } from "./app-shell";
vi.mock("next/navigation", () => ({ usePathname: () => "/c", useRouter: () => ({ replace: vi.fn() }) }));
vi.mock("./conversation-list-context", () => ({ useConversationList: () => ({ items: [], loading: false, error: undefined, refresh: vi.fn() }) }));
beforeEach(() => {
    Object.defineProperty(Element.prototype, "getAnimations", { configurable: true, value: () => [] });
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"approvals":[]}')));
});
afterEach(() => { cleanup(); Reflect.deleteProperty(Element.prototype, "getAnimations"); vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear(); });
// Base UI는 같은 경고를 모듈당 한 번만 낸다. 이 파일은 AppShell을 처음 렌더하는 테스트 하나만 두어 경고가 소진되지 않게 한다.
test("sidebar and topbar link buttons render without Base UI nativeButton warnings", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<AppShell user="demo-finance-g2">본문</AppShell>);
    expect(screen.getByTestId("sidebar-new-chat").tagName).toBe("A");
    expect(screen.getByTestId("nav-new-conversation").tagName).toBe("A");
    expect(spy.mock.calls.filter(call => String(call[0]).includes("nativeButton"))).toEqual([]);
});
