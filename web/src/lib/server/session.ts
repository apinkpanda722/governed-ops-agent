import { NextRequest, NextResponse } from "next/server";
const name = "govops_session";
export function readSessionToken(request: NextRequest): string | undefined {
  const value = request.cookies.get(name)?.value;
  return value && /^[A-Za-z0-9_-]+$/.test(value) ? value : undefined;
}
export function setSessionCookie(response: NextResponse, token: string, options: { expiresAt: number; origin: string }): boolean {
  const maxAge = Math.max(0, Math.min(28800, Math.floor(options.expiresAt - Date.now() / 1000)));
  if (!Number.isFinite(maxAge) || maxAge === 0 || !/^[A-Za-z0-9_-]+$/.test(token)) return false;
  response.cookies.set(name, token, { httpOnly: true, sameSite: "strict", path: "/", secure: new URL(options.origin).protocol === "https:", maxAge });
  return true;
}
export function clearSessionCookie(response: NextResponse): void {
  response.cookies.delete(name);
}
