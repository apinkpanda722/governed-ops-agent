import { NextRequest } from "next/server";
import { requireSameOrigin } from "@/lib/server/origin";
import { clearSessionCookie, readSessionToken, setSessionCookie } from "@/lib/server/session";
import { error, json, loginBody, proxyJson, upstreamError } from "@/lib/server/proxy";
import { object, projectUser } from "@/lib/server/project";
import { upstreamFetch } from "@/lib/server/upstream";
export const dynamic = "force-dynamic";
export async function GET(request: NextRequest) {
  return proxyJson(request, { path: "/sessions/me", project: projectUser });
}
export async function POST(request: NextRequest) {
  const blocked = requireSameOrigin(request); if (blocked) return blocked;
  let body: unknown;
  try { body = loginBody(await request.json()); } catch {}
  if (body === undefined) return error("invalid_request", 422);
  try {
    const upstream = await upstreamFetch("/sessions", { method: "POST", body });
    if (!upstream.ok) return upstreamError(upstream, "login");
    const data = object(await upstream.json());
    if (typeof data.session_token !== "string" || typeof data.expires_at !== "number") return error("upstream_unavailable", 502);
    const response = json({ ok: true, user_id: object(body).user }, upstream.status);
    if (!setSessionCookie(response, data.session_token, { expiresAt: data.expires_at, origin: request.headers.get("origin")! })) return error("unauthenticated", 401);
    return response;
  } catch { return error("upstream_unavailable", 502); }
}
export async function DELETE(request: NextRequest) {
  const blocked = requireSameOrigin(request); if (blocked) return blocked;
  const token = readSessionToken(request);
  let response = json({ ok: true });
  if (token) {
    try {
      const upstream = await upstreamFetch("/sessions", { method: "DELETE", token });
      if (!upstream.ok && upstream.status !== 401) response = await upstreamError(upstream);
    } catch { response = error("upstream_unavailable", 502); }
  }
  clearSessionCookie(response); return response;
}
