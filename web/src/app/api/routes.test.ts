// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { expect, test } from "vitest";
const root=new URL(".",import.meta.url).pathname;
function walk(path:string):string[] {
 return readdirSync(path,{withFileTypes:true}).flatMap(e=>e.isDirectory()?walk(join(path,e.name)):[join(path,e.name)]);
}
test("route file names and server boundaries", () => {
 const files=walk(root).filter(p=>p.endsWith("/route.ts"));
 expect(files.map(p=>relative(root,p)).sort()).toEqual([
 "demo-users/route.ts","session/route.ts","conversations/route.ts","conversations/[id]/route.ts",
 "conversations/[id]/turns/route.ts","conversations/[id]/events/route.ts","approvals/route.ts","tasks/[id]/approval/route.ts"
 ].sort());
 for(const path of files) {
 const source=readFileSync(path,"utf8");
 expect(source).toContain('export const dynamic = "force-dynamic"');
 expect(source).not.toContain("next/headers");
 if(/export (?:async )?function (POST|PUT|PATCH|DELETE)/.test(source)) expect(source).toMatch(/requireSameOrigin\(request\)/);
 }
 for(const path of walk(new URL("../../",import.meta.url).pathname).filter(p=>/\.tsx?$/.test(p))) {
 const source=readFileSync(path,"utf8");
 if(/^\s*["']use client["']/m.test(source)) expect(source).not.toContain("@/lib/server");
 }
});
