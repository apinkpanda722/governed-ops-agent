// @vitest-environment node
import { expect, test } from "vitest";
import { NextRequest } from "next/server";
import { requireSameOrigin } from "./origin";
test.each(["POST", "PUT", "PATCH", "DELETE"])("%s origin gate", method => {
 for (const [origin, host, ok] of [
  [undefined, "127.0.0.1:3100", false],
  ["bad", "127.0.0.1:3100", false],
  ["http://evil.test", "127.0.0.1:3100", false],
  ["http://127.0.0.1:3100", undefined, false],
  ["http://127.0.0.1:3100", "127.0.0.1:3100", true],
 ] as const) {
  const headers = new Headers();
  if(origin) headers.set("origin",origin); if(host) headers.set("host",host);
  const result = requireSameOrigin(new NextRequest("http://localhost:3100/api", {method, headers}));
  if(ok) expect(result).toBeNull(); else expect(result?.status).toBe(403);
 }
});
test.each(["GET","HEAD","OPTIONS"])("%s skips origin", method => {
 expect(requireSameOrigin(new NextRequest("http://localhost/api", {method}))).toBeNull();
});
