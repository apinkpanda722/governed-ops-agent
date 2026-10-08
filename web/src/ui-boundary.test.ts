import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";
function sources(dir: string): string[] { return readdirSync(dir, { withFileTypes: true }).flatMap(e => e.isDirectory() ? sources(join(dir, e.name)) : /\.tsx?$/.test(e.name) && !e.name.includes(".test.") ? [join(dir, e.name)] : []); }
test("screen code obeys presentation and credential boundaries", () => {
    const files = ["components", "hooks", "app/(app)", "app/login"].flatMap(d => sources(join(import.meta.dirname, d))).concat(["bff", "messages", "types", "theme", "teams", "conversation-groups"].map(f => join(import.meta.dirname, `lib/${f}.ts`)));
    expect(files.length).toBeGreaterThan(0);
    expect(existsSync(join(import.meta.dirname, "lib/theme.ts"))).toBe(true);
    for (const file of files) {
        const text = readFileSync(file, "utf8");
        expect(storageViolations(file, text), file).toEqual([]);
        expect(text, file).not.toMatch(/masked|output_flagged|guardrail_block|stop_reason|evidence_ids|guardrail_signals|guardrail_post|dangerouslySetInnerHTML|@\/lib\/server|next\/headers/);
    }
});

const forbiddenThemeNames = new RegExp(["session" + "Storage", "document\\.cookie", "token", "session"].join("|"), "i");
function storageViolations(file: string, text: string): string[] {
    const themePath = join(import.meta.dirname, "lib/theme.ts");
    if (file !== themePath) {
        return new RegExp(["local" + "Storage", "session" + "Storage", "document\\.cookie"].join("|")).test(text) ? ["storage"] : [];
    }
    const errors: string[] = [];
    const accessCount = (text.match(/\blocalStorage\b/g) ?? []).length;
    const calls = [...text.matchAll(/\blocalStorage\.(?:getItem|setItem)\(\s*["']([^"']+)["']/g)];
    if (calls.length !== accessCount || calls.some(call => call[1] !== "govops-theme")) errors.push("invalid-key");
    if (!text.includes("govops-theme") || forbiddenThemeNames.test(text)) errors.push("invalid-theme");
    // Track lexical try scopes rather than allowing an access merely because a try exists elsewhere.
    const lexemes = text.match(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\btry\b|[{}]|\blocalStorage\b/g) ?? [];
    const scopes: boolean[] = [];
    let pendingTry = false;
    for (const lexeme of lexemes) {
        if (lexeme === "try") pendingTry = true;
        else if (lexeme === "{") { scopes.push(pendingTry); pendingTry = false; }
        else if (lexeme === "}") scopes.pop();
        else if (lexeme === "localStorage" && !scopes.includes(true)) errors.push("unguarded");
    }
    return errors;
}
test("theme is the only narrowly guarded storage exception", () => {
    const file = join(import.meta.dirname, "lib/theme.ts");
    expect(existsSync(file)).toBe(true);
    const text = readFileSync(file, "utf8");
    expect(text).toContain("govops-theme");
    expect(text).not.toMatch(forbiddenThemeNames);
    expect(storageViolations(file,text)).toEqual([]);
    expect(storageViolations(file,'try { local' + 'Storage.setItem("other","dark"); } catch {}')).toContain("invalid-key");
    expect(storageViolations(file,'try {} catch {} local' + 'Storage.getItem("govops-theme")')).toContain("unguarded");
    expect(storageViolations(join(import.meta.dirname,"lib/teams.ts"),'try { local' + 'Storage.setItem("govops-theme","dark"); } catch {}')).toContain("storage");
});
