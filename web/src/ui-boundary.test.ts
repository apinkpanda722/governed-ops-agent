import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";
function sources(dir: string): string[] { return readdirSync(dir, { withFileTypes: true }).flatMap(e => e.isDirectory() ? sources(join(dir, e.name)) : /\.tsx?$/.test(e.name) && !e.name.includes(".test.") ? [join(dir, e.name)] : []); }
test("screen code obeys presentation and credential boundaries", () => {
    const files = ["components", "hooks", "app/(app)", "app/login"].flatMap(d => sources(join(import.meta.dirname, d))).concat(["bff", "messages", "types"].map(f => join(import.meta.dirname, `lib/${f}.ts`)));
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) {
        const text = readFileSync(file, "utf8");
        expect(text, file).not.toMatch(new RegExp(["local" + "Storage", "session" + "Storage", "document\\.cookie"].join("|")));
        expect(text, file).not.toMatch(/masked|output_flagged|guardrail_block|stop_reason|evidence_ids|guardrail_signals|guardrail_post|dangerouslySetInnerHTML|@\/lib\/server|next\/headers/);
    }
});
