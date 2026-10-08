import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useApprovals } from "./use-approvals";
import { AppShell } from "@/components/app-shell";
import { setOnUnauthenticated } from "@/lib/bff";
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setOnUnauthenticated(); });
test.each([5000, 10000])("polls every %s, pauses hidden tabs, refreshes and cleans up", async interval => {
    vi.useFakeTimers(); let hidden = false; vi.spyOn(document, "visibilityState", "get").mockImplementation(() => hidden ? "hidden" : "visible");
    const f = vi.fn().mockImplementation(() => Promise.resolve(new Response('{"approvals":[{"turn_id":"t","requester_label":"demo-platform-g3","request":"private-sentinel"}]}'))); vi.stubGlobal("fetch", f);
    const { result, unmount } = renderHook(() => useApprovals(interval));
    await act(async () => vi.advanceTimersByTimeAsync(0)); expect(result.current.approvals).toHaveLength(1); expect(JSON.stringify(result.current.approvals)).not.toContain("private-sentinel");
    await act(async () => vi.advanceTimersByTimeAsync(interval - 1)); expect(f).toHaveBeenCalledOnce();
    await act(async () => vi.advanceTimersByTimeAsync(1)); expect(f).toHaveBeenCalledTimes(2);
    hidden = true; act(() => document.dispatchEvent(new Event("visibilitychange"))); await act(async () => vi.advanceTimersByTimeAsync(interval * 2)); expect(f).toHaveBeenCalledTimes(2);
    hidden = false; act(() => document.dispatchEvent(new Event("visibilitychange"))); await act(async () => vi.advanceTimersByTimeAsync(0)); expect(f).toHaveBeenCalledTimes(3);
    await act(async () => result.current.refresh()); expect(f).toHaveBeenCalledTimes(4);
    const refresh = result.current.refresh; unmount(); expect(vi.getTimerCount()).toBe(0); await act(async () => { await refresh(); await vi.advanceTimersByTimeAsync(interval * 2); }); expect(f).toHaveBeenCalledTimes(4);
});
test("401 uses BFF login hook and stops polling", async () => {
    vi.useFakeTimers(); const login = vi.fn(); setOnUnauthenticated(login); const f = vi.fn().mockImplementation(() => Promise.resolve(new Response('{"code":"unauthenticated"}', { status: 401 }))); vi.stubGlobal("fetch", f);
    renderHook(() => useApprovals()); await act(async () => vi.advanceTimersByTimeAsync(15000)); expect(login).toHaveBeenCalledOnce(); expect(f).toHaveBeenCalledOnce();
});
test("hidden tab resumes only one poll and aborts in-flight read on unmount", async () => {
    vi.useFakeTimers(); let hidden = false; vi.spyOn(document, "visibilityState", "get").mockImplementation(() => hidden ? "hidden" : "visible");
    let resolve!: (r: Response) => void; let signal: AbortSignal | undefined;
    const f = vi.fn((_path: string, options: RequestInit) => { signal = options.signal as AbortSignal; return new Promise<Response>(r => { resolve = r; }); }); vi.stubGlobal("fetch", f);
    const { unmount } = renderHook(() => useApprovals());
    hidden = true; act(() => document.dispatchEvent(new Event("visibilitychange"))); hidden = false; act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(f).toHaveBeenCalledOnce(); unmount(); expect(signal?.aborted).toBe(true);
    await act(async () => { resolve(new Response('{"approvals":[]}')); await vi.advanceTimersByTimeAsync(15000); }); expect(f).toHaveBeenCalledOnce(); expect(vi.getTimerCount()).toBe(0);
});
test("menu badge shows pending count and hides zero", async () => {
    const f = vi.fn().mockResolvedValueOnce(new Response('{"approvals":[{"turn_id":"a"},{"turn_id":"b"}]}')).mockResolvedValueOnce(new Response('{"approvals":[]}')); vi.stubGlobal("fetch", f);
    const first = render(<AppShell user="demo-platform-g3">본문</AppShell>);
    await waitFor(() => expect(screen.getByTestId("nav-approvals-count").textContent).toBe("2"));
    first.unmount(); render(<AppShell user="demo-platform-g3">본문</AppShell>);
    await waitFor(() => expect(f).toHaveBeenCalledTimes(2)); expect(screen.queryByTestId("nav-approvals-count")).toBeNull();
});
