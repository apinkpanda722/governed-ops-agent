import { afterEach, expect, test, vi } from "vitest";
import { bff, setOnUnauthenticated } from "./bff";
afterEach(() => { vi.unstubAllGlobals(); setOnUnauthenticated(); });
test("401 invokes replaceable hook and exposes only status and code", async () => {
    const hook = vi.fn();
    setOnUnauthenticated(hook);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "unauthenticated", detail: "private-body" }), { status: 401 })));
    await expect(bff("/api/session")).rejects.toEqual({ status: 401, code: "unauthenticated" });
    expect(hook).toHaveBeenCalledOnce();
});
test("relative requests use same origin and JSON mutations", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal("fetch", fetcher);
    await bff("/api/conversations", { method: "POST", body: { request: "hello" } });
    expect(fetcher).toHaveBeenCalledWith("/api/conversations", expect.objectContaining({ credentials: "same-origin", body: '{"request":"hello"}', headers: { "Content-Type": "application/json" } }));
    for (const path of ["https://example.test/api/session", "//example.test/api/session", "/other", "/api/../other", "/api/thing?x=1"])
        await expect(bff(path)).rejects.toEqual({ status: 0, code: "invalid_request" });
    expect(fetcher).toHaveBeenCalledTimes(1);
});
test("network and arbitrary response details never escape", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("private-body")));
    await expect(bff("/api/session")).rejects.toEqual({ status: 0, code: "upstream_unavailable" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "private body", detail: "private-body" }), { status: 500 })));
    await expect(bff("/api/session")).rejects.toEqual({ status: 500, code: "request_failed" });
});
test("delete mutations also send a JSON body", async () => {
 const fetcher = vi.fn().mockResolvedValue(new Response('{}'));
 vi.stubGlobal("fetch", fetcher);
 await bff("/api/session", {method:"DELETE"});
 expect(fetcher).toHaveBeenCalledWith("/api/session", expect.objectContaining({method:"DELETE", body:"{}", headers:{"Content-Type":"application/json"}, credentials:"same-origin"}));
});
