import { NextRequest } from "next/server";
import { error, validId } from "@/lib/server/proxy";
import { clearSessionCookie, readSessionToken } from "@/lib/server/session";
import { upstreamFetch } from "@/lib/server/upstream";
export const dynamic = "force-dynamic";
export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const token = readSessionToken(request);
  if (!token) { const response = error("unauthenticated", 401); clearSessionCookie(response); return response; }
  const { id } = await params;
  if (!validId(id)) return error("not_found", 404);
  try {
    const upstream = await upstreamFetch(`/conversations/${id}/events`, { token, signal: request.signal });
    if (upstream.status === 401) {
      const response = error("unauthenticated", 401); clearSessionCookie(response); return response;
    }
    if (upstream.status === 404) return error("conversation_not_found", 404);
    if (!upstream.ok || !upstream.headers.get("content-type")?.startsWith("text/event-stream") || !upstream.body) return error("upstream_unavailable", 502);
    return new Response(upstream.body, { status: upstream.status, headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no" } });
  } catch { return error("upstream_unavailable", 502); }
}
