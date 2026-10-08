import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import Login from "./page";
const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });
test("groups users, retains other labels and logs in", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ users: ["demo-platform-g3", "demo-platform-g4", "demo-support-g2", "odd-label"] }))).mockResolvedValueOnce(new Response('{}'));
    vi.stubGlobal("fetch", fetcher);
    render(<Login />);
    await waitFor(() => expect(screen.getByTestId("login-user-demo-platform-g3")).toBeDefined(), { timeout: 1000 });
    expect(screen.getByRole("heading", { name: "platform" })).toBeDefined();
    expect(screen.getByRole("heading", { name: "기타" })).toBeDefined();
    expect(screen.getByTestId("login-user-odd-label")).toBeDefined();
    fireEvent.click(screen.getByTestId("login-user-demo-platform-g3"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/c"), { timeout: 1000 });
    expect(fetcher).toHaveBeenLastCalledWith("/api/session", expect.objectContaining({ method: "POST", body: '{"user":"demo-platform-g3"}' }));
});
test.each(["empty", "demo_login_disabled", "loopback_required"])("shows login error for %s", async (code) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(code === "empty" ? { users: [] } : { code }), { status: code === "empty" ? 200 : 404 })));
    render(<Login />);
    await waitFor(() => expect(screen.getByTestId("login-error").textContent).toBe(code === "demo_login_disabled" ? "데모 로그인이 꺼져 있습니다." : code === "loopback_required" ? "로컬 접속에서만 로그인할 수 있습니다." : "요청을 처리하지 못했습니다."), { timeout: 1000 });
});
