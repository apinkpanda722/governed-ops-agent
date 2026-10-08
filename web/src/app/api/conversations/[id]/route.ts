import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/server/proxy";
import { projectConversation } from "@/lib/server/project";
export const dynamic = "force-dynamic";
export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return proxyJson(request, { path: `/conversations/${id}`, project: projectConversation, id });
}
