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
  expect(specs.sort()).toEqual(["approval-flow.spec.ts", "blocked-and-isolation.spec.ts", "delete-conversation.spec.ts"]);
  for (const name of [...specs, "support.ts"]) {
    const source = read(`../e2e/${name}`);
    expect(source).not.toMatch(/console\.|storageState|waitForTimeout|data-testid|\b(?:trace|screenshot|video)\s*:/);
    expect(source.replace(/document\.cookie\.length/g, "")).not.toContain("document.cookie");
    if (/cookies\(/.test(source)) expect(source).not.toContain(".value");
    expect(source).toContain('from "../src/lib/testids"');
    expect(source).not.toContain("switchUser");
    expect(source).not.toMatch(/getByTestId\(["'`]/);
  }
});

// 이 파일은 Vite(ESM)가 읽으므로 import.meta를 쓸 수 있지만, Playwright는 web/package.json에 "type":"module"이
// 없어 설정·스펙을 CommonJS로 변환해 불러온다. 그래서 e2e 파일에는 ESM 전용 구문을 두지 않는다.
// 실제 Playwright 로더로 설정·스펙의 로드·수집을 확인하는 검사는 `make verify`의 playwright test --list다(FP-068).
test("playwright config and e2e files avoid ESM-only syntax", () => {
  const sources = ["../playwright.config.ts", "../e2e/support.ts", ...readdirSync(resolve(root, "../e2e")).filter(name => name.endsWith(".spec.ts")).map(name => `../e2e/${name}`)];
  expect(sources.length).toBeGreaterThanOrEqual(4);
  for (const path of sources) expect(read(path), path).not.toMatch(/\bimport\.meta\b/);
});

test("obsolete user switch is removed while logout remains", () => {
  expect(read("lib/testids.ts")).not.toContain("switchUser");
  expect(read("components/user-card.tsx")).not.toContain("switchUser");
  expect(read("components/user-card.tsx")).toContain("testids.logout");
});
