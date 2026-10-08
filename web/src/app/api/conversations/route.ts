import { NextRequest } from "next/server";
import { proxyJson, requestBody } from "@/lib/server/proxy";
import { projectCreated } from "@/lib/server/project";
import { requireSameOrigin } from "@/lib/server/origin";
export const dynamic = "force-dynamic";
export async function POST(request: NextRequest) {
  const blocked = requireSameOrigin(request); if (blocked) return blocked;
  return proxyJson(request, { path: `/conversations`, project: projectCreated, validate: requestBody });
}
