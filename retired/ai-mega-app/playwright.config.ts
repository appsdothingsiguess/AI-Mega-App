// Playwright config for the Phase-1 E2E smoke suite (docs/FEATURES.md F12,
// PLAN.md §4.10). Runs against uvicorn (app.main:app) + this branch's
// minimal fake llama-swap (e2e/fake_backend.py) — no GPU, no network to
// model hosts. Specs skip cleanly (not fail) while web/ hasn't been built
// yet by p1/web-shell; see e2e/global-setup.ts.
import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.E2E_APP_PORT ?? "8000";
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 1, // flake beyond one retry is a bug to fix, not mask (F12)
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
