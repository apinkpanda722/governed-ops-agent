import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/server/proxy";
import { projectConversation, projectDeleted } from "@/lib/server/project";
import { requireSameOrigin } from "@/lib/server/origin";
export const dynamic = "force-dynamic";
export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return proxyJson(request, { path: `/conversations/${id}`, project: projectConversation, id });
}
export async function DELETE(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const blocked = requireSameOrigin(request); if (blocked) return blocked;
  const { id } = await params;
  return proxyJson(request, { path: `/conversations/${id}`, project: projectDeleted, id });
}
