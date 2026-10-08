import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/server/proxy";
import { projectApprovals } from "@/lib/server/project";
export const dynamic = "force-dynamic";
export async function GET(request: NextRequest) {
  return proxyJson(request, { path: `/approvals`, project: projectApprovals });
}
