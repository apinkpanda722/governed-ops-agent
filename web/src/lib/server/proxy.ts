import { NextRequest, NextResponse } from "next/server";
import { requireSameOrigin } from "./origin";
import { clearSessionCookie, readSessionToken } from "./session";
import { object } from "./project";
import { upstreamFetch } from "./upstream";
export function json(value: unknown, status = 200): NextResponse {
  return NextResponse.json(value, { status, headers: { "Cache-Control": "no-store" } });
}
export function error(code: string, status: number): NextResponse { return json({ code }, status); }
export function validId(id: string): boolean { return /^[0-9a-f]{32}$/.test(id); }
export function requestBody(value: unknown): unknown | undefined {
  const b = object(value);
  return Object.keys(b).length === 1 && typeof b.request === "string" && b.request.length >= 1 && b.request.length <= 2000 ? { request: b.request } : undefined;
}
export function loginBody(value: unknown): unknown | undefined {
  const b = object(value);
  return Object.keys(b).length === 1 && typeof b.user === "string" && /^demo-[a-z-]+-g[0-9]+$/.test(b.user) ? { user: b.user } : undefined;
}
export function approvalBody(value: unknown): unknown | undefined {
  const b = object(value);
  if (Object.keys(b).some(k => !["decision", "draft_sha256", "edited"].includes(k)) ||
      typeof b.decision !== "string" || !["approve", "reject", "edit_approve"].includes(b.decision) ||
      typeof b.draft_sha256 !== "string" || !/^[0-9a-f]{64}$/i.test(b.draft_sha256)) return;
  if (b.decision !== "edit_approve" && "edited" in b) return;
  if ("edited" in b && (b.edited === null || typeof b.edited !== "object" || Array.isArray(b.edited) ||
      Object.values(object(b.edited)).some(v => typeof v !== "string"))) return;
  return { decision: b.decision, draft_sha256: b.draft_sha256, ...("edited" in b ? { edited: b.edited } : {}) };
}
export async function upstreamError(response: Response, kind?: "approval" | "login"): Promise<NextResponse> {
  if (response.status === 401) {
    const result = error("unauthenticated", 401); clearSessionCookie(result); return result;
  }
  if (response.status >= 500 || response.status < 400) return error("upstream_unavailable", 502);
  let detail: unknown;
  try { detail = object(await response.json()).detail; } catch {}
  let code = "request_failed";
  if (kind === "approval") code = ({403:"approval_forbidden",404:"task_not_found",409:"approval_conflict",422:"invalid_edit"} as Record<number,string>)[response.status] ?? code;
  else if (kind === "login") {
    if (response.status === 403) code = "loopback_required";
    if (response.status === 404) code = detail === "User not found" ? "user_not_found" : "demo_login_disabled";
  } else if (typeof detail === "string" && ["approval_pending", "turn_in_progress", "turn_limit", "conversation_not_found", "invalid_request"].includes(detail)) code = detail;
  return error(code, response.status);
}
type Spec = { path: string; id?: string; anonymous?: boolean; validate?: (value: unknown) => unknown | undefined; project: (value: unknown) => unknown; kind?: "approval" | "login" };
export async function proxyJson(request: NextRequest, spec: Spec): Promise<NextResponse> {
  const originError = requireSameOrigin(request); if (originError) return originError;
  const token = readSessionToken(request);
  if (!spec.anonymous && !token) {
    const response = error("unauthenticated", 401); clearSessionCookie(response); return response;
  }
  if (spec.id !== undefined && !validId(spec.id)) return error("not_found", 404);
  let body: unknown;
  if (spec.validate) {
    try { body = spec.validate(await request.json()); } catch {}
    if (body === undefined) return error("invalid_request", 422);
  }
  try {
    const response = await upstreamFetch(spec.path, { method: request.method, token, body });
    if (!response.ok) return upstreamError(response, spec.kind);
    return json(spec.project(await response.json()), response.status);
  } catch { return error("upstream_unavailable", 502); }
}
