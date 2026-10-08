import { NextRequest } from "next/server";
import { proxyJson, approvalBody } from "@/lib/server/proxy";
import { projectTurn } from "@/lib/server/project";
import { requireSameOrigin } from "@/lib/server/origin";
export const dynamic = "force-dynamic";
export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const blocked = requireSameOrigin(request); if (blocked) return blocked;
  const { id } = await params;
  return proxyJson(request, { path: `/tasks/${id}/approval`, project: projectTurn, id, validate: approvalBody, kind: "approval" });
}
