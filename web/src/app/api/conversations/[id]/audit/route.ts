import { NextRequest } from "next/server";
import { error, proxyJson } from "@/lib/server/proxy";
import { projectAudit } from "@/lib/server/project";
export const dynamic = "force-dynamic";
export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  // These read-only records also reject an explicitly mismatched Origin.
  const origin = request.headers.get("origin");
  if (origin !== null) {
    try {
      if (new URL(origin).host !== request.headers.get("host")) return error("origin_mismatch", 403);
    } catch { return error("origin_mismatch", 403); }
  }
  const { id } = await params;
  return proxyJson(request, { path: `/conversations/${id}/audit`, project: projectAudit, id });
}
