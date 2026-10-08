import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";
export default defineConfig({
  testDir: "./e2e", fullyParallel: false, workers: 1, retries: 0,
  timeout: 90_000, expect: { timeout: 15_000 }, reporter: "list", outputDir: "test-results",
  use: { baseURL: "http://127.0.0.1:3200", trace: "off", screenshot: "off", video: "off" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "uv run python tests/e2e_backend.py --port 8200 --probe-port 8201",
      cwd: fileURLToPath(new URL("..", import.meta.url)),
      url: "http://127.0.0.1:8200/demo/users", timeout: 60_000,
      reuseExistingServer: false, stdout: "ignore",
      env: { AUDIT_KEY_FILE: "", AUDIT_DIR: "", KODOCS_MCP_URL: "", KODOCS_MCP_TOKENS_FILE: "", MOCK_TOKENS_FILE: "", MOCK_JIRA_URL: "", MOCK_SLACK_URL: "", GOVOPS_DATABASE_URL: "" },
    },
    {
      command: "pnpm exec next build && pnpm exec next start -H 127.0.0.1 -p 3200",
      cwd: fileURLToPath(new URL(".", import.meta.url)),
      env: { GOVOPS_API_URL: "http://127.0.0.1:8200" },
      url: "http://127.0.0.1:3200/login", timeout: 300_000, reuseExistingServer: false,
    },
  ],
});
