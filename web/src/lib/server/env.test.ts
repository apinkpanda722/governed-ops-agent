// @vitest-environment node
import { afterEach, expect, test, vi } from "vitest";
import { apiBase, envOr } from "./env";
afterEach(() => vi.unstubAllEnvs());
test.each([undefined, "", "   "])("empty configuration defaults: %s", value => {
  vi.stubEnv("GOVOPS_API_URL", value);
  expect(apiBase()).toBe("http://127.0.0.1:8000");
  expect(envOr("GOVOPS_API_URL", "fallback")).toBe("fallback");
});
test.each(["http://localhost:8000", "http://example.com", "https://127.0.0.1", "http://127.0.0.1@evil.test", "bad"])("rejects %s", value => {
 vi.stubEnv("GOVOPS_API_URL", value); expect(() => apiBase()).toThrow();
});
