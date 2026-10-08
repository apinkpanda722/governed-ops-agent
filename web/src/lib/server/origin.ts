import { NextRequest, NextResponse } from "next/server";
export function requireSameOrigin(request: NextRequest): NextResponse | null {
  if (["GET", "HEAD", "OPTIONS"].includes(request.method)) return null;
  const origin = request.headers.get("origin"), host = request.headers.get("host");
  try {
    if (origin && host && new URL(origin).host === host) return null;
  } catch {}
  return NextResponse.json({ code: "origin_mismatch" }, { status: 403, headers: { "Cache-Control": "no-store" } });
}
