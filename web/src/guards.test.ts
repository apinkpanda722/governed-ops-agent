import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "vitest";

type Violation = "public-secret" | "browser-storage" | "direct-fastapi";

function inspectSource(source: string): Violation[] {
  const violations: Violation[] = [];
  const publicNames = source.match(/\bNEXT_PUBLIC_[A-Z0-9_]+\b/g) ?? [];
  if (publicNames.some((name) => /TOKEN|SECRET|KEY|SESSION|PASSWORD/.test(name))) {
    violations.push("public-secret");
  }
  if (source.split(/\r?\n/).some((line) =>
    /\b(?:localStorage|sessionStorage)\b/.test(line) && /token|session/i.test(line)
  )) {
    violations.push("browser-storage");
  }
  if (/^\s*["']use client["']\s*;?/m.test(source) &&
    /127\.0\.0\.1|localhost:8000|FASTAPI|API_BASE/.test(source)) {
    violations.push("direct-fastapi");
  }
  return violations;
}

test.each(["TOKEN", "SECRET", "KEY", "SESSION", "PASSWORD"])(
  "rejects public environment variables containing %s",
  (name) => {
    expect(inspectSource(`const value = process.env.NEXT_PUBLIC_DEMO_${name};`))
      .toContain("public-secret");
    expect(inspectSource(`const value = process.env["NEXT_PUBLIC_${name}"];`))
      .toContain("public-secret");
  },
);

test.each(["localStorage", "sessionStorage"])(
  "rejects token and session access through %s",
  (storage) => {
    for (const name of ["token", "TOKEN", "session", "Session"]) {
      expect(inspectSource(`${storage}.setItem("${name}", value);`))
        .toContain("browser-storage");
    }
  },
);

test.each(["127.0.0.1", "localhost:8000", "FASTAPI", "API_BASE"])(
  "rejects %s in client files",
  (address) => {
    for (const directive of ["'use client';", '"use client";']) {
      expect(inspectSource(`${directive}\nfetch("${address}/tasks");`))
        .toContain("direct-fastapi");
    }
  },
);

test("allows public non-secret settings, harmless storage, and BFF requests", () => {
  expect(inspectSource(`
    'use client';
    const title = process.env.NEXT_PUBLIC_APP_TITLE;
    localStorage.setItem("theme", "dark");
    fetch("/api/tasks");
  `)).toEqual([]);
});

test("allows server-side FastAPI configuration", () => {
  expect(inspectSource('const API_BASE = "http://127.0.0.1:8000";')).toEqual([]);
});

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return entry.isFile() && /\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

test("web sources obey the token and BFF boundaries", () => {
  const ownPath = fileURLToPath(import.meta.url);
  const violations = sourceFiles(dirname(ownPath))
    .filter((path) => path !== ownPath)
    .flatMap((path) => inspectSource(readFileSync(path, "utf8"))
      .map((rule) => ({ path, rule })));

  expect(violations).toEqual([]);
});
