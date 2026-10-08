import { readFileSync, readdirSync } from "node:fs";
import { expect, test } from "vitest";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const root = dirname(fileURLToPath(import.meta.url));
const read = (path: string) => readFileSync(resolve(root, path), "utf8");
test("e2e uses isolated loopback servers without sensitive artifacts", () => {
  const config = read("../playwright.config.ts");
  for (const option of ['trace: "off"', 'screenshot: "off"', 'video: "off"', 'reuseExistingServer: false', '-H 127.0.0.1', '3200', '8200', '8201']) expect(config).toContain(option);
  expect(config.match(/reuseExistingServer: false/g)).toHaveLength(2);
  expect(read("../next.config.ts")).not.toMatch(/distDir/);
  const specs = readdirSync(resolve(root, "../e2e")).filter(name => name.endsWith(".spec.ts"));
  expect(specs).toHaveLength(2);
  for (const name of [...specs, "support.ts"]) {
    const source = read(`../e2e/${name}`);
    expect(source).not.toMatch(/console\.|storageState|waitForTimeout|data-testid|\b(?:trace|screenshot|video)\s*:/);
    expect(source.replace(/document\.cookie\.length/g, "")).not.toContain("document.cookie");
    if (/cookies\(/.test(source)) expect(source).not.toContain(".value");
    expect(source).toContain('from "../src/lib/testids"');
    expect(source).not.toMatch(/getByTestId\(["'`]/);
  }
});
