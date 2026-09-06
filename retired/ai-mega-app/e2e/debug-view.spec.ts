// Smoke spec 2 (docs/FEATURES.md F12, PLAN.md §4.10, §4.16): the debug view
// (`#/debug`) populates a span/trace waterfall after a turn. Skips cleanly
// until web/ + app/debug are wired — this agent builds no frontend/app code.
import { expect, test } from "@playwright/test";

test.beforeEach(() => {
  test.skip(
    process.env.E2E_WEB_BUILT !== "1",
    "web/ not built yet (p1/web-shell not merged) — skipping until it lands"
  );
  test.skip(
    process.env.E2E_BACKEND_READY !== "1",
    "backend (fake llama-swap + app) failed to come up in global-setup"
  );
});

test("debug view shows a span after a chat turn", async ({ page, context }) => {
  await page.goto("/");
  const composer = page.getByTestId("chat-composer-input");
  await expect(composer).toBeVisible({ timeout: 5_000 });
  await composer.fill("ping");
  await page.getByTestId("chat-composer-send").click();

  const debugPage = await context.newPage();
  await debugPage.goto("/#/debug");
  await expect(debugPage.getByTestId("debug-span-row").first()).toBeVisible({
    timeout: 10_000,
  });
});
