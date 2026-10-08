// @vitest-environment node
import { randomBytes } from "node:crypto";
import { afterEach, expect, test, vi } from "vitest";
import { NextRequest, NextResponse } from "next/server";
import { setSessionCookie, clearSessionCookie, readSessionToken } from "./session";
afterEach(() => vi.useRealTimers());
test("cookie attributes, cap, expiry and deletion", () => {
 vi.useFakeTimers(); vi.setSystemTime(1000000);
 const token = randomBytes(32).toString("base64url");
 for(const origin of ["http://127.0.0.1:3100","https://127.0.0.1:3100"]) {
 const response = NextResponse.json({});
 expect(setSessionCookie(response,token,{expiresAt:1000+40000,origin})).toBe(true);
 const cookie=response.headers.get("set-cookie")!;
 expect(cookie).toContain("HttpOnly"); expect(cookie).toContain("SameSite=strict");
 expect(cookie).toContain("Path=/"); expect(cookie).toContain("Max-Age=28800");
 expect(cookie.includes("Secure")).toBe(origin.startsWith("https:"));
 }
 const short=NextResponse.json({});
 expect(setSessionCookie(short,token,{expiresAt:1010.9,origin:"http://127.0.0.1"})).toBe(true);
 expect(short.headers.get("set-cookie")).toContain("Max-Age=10");
 for(const expiresAt of [999,1000]) {
 const response=NextResponse.json({});
 expect(setSessionCookie(response,token,{expiresAt,origin:"http://127.0.0.1"})).toBe(false);
 expect(response.headers.get("set-cookie")).toBeNull();
 }
 const deleted=NextResponse.json({}); clearSessionCookie(deleted);
 expect(deleted.headers.get("set-cookie")).toContain("Expires=Thu, 01 Jan 1970");
 expect(readSessionToken(new NextRequest("http://localhost",{headers:{cookie:`govops_session=${token}`}})) === token).toBe(true);
 for(const value of ["bad.value","", "%20"]) expect(readSessionToken(new NextRequest("http://localhost",{headers:{cookie:`govops_session=${value}`}}))).toBeUndefined();
});
