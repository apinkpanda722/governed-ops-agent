import { NextRequest } from "next/server";
import { proxyJson, requestBody } from "@/lib/server/proxy";
import { projectCreated } from "@/lib/server/project";
import { requireSameOrigin } from "@/lib/server/origin";
export const dynamic = "force-dynamic";
export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const blocked = requireSameOrigin(request); if (blocked) return blocked;
  const { id } = await params;
  return proxyJson(request, { path: `/conversations/${id}/turns`, project: projectCreated, id, validate: requestBody });
}
