import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/server/proxy";
import { projectUsers } from "@/lib/server/project";
export const dynamic = "force-dynamic";
export async function GET(request: NextRequest) {
  return proxyJson(request, { path: `/demo/users`, project: projectUsers, anonymous: true });
}
