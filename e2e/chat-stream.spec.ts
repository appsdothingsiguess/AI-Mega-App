// Smoke spec 1 (docs/FEATURES.md F12, PLAN.md §4.10): send a message and see
// streamed text. Skips cleanly — not a failure — until p1/web-shell has
// built web/ and p1/chat-sse has wired POST /api/chat, since this agent's
// NON-GOALS exclude frontend/app code.
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

test("sending a message renders streamed assistant text", async ({ page }) => {
  await page.goto("/");

  const composer = page.getByTestId("chat-composer-input");
  await expect(composer).toBeVisible({ timeout: 5_000 });
  await composer.fill("Hello");
  await page.getByTestId("chat-composer-send").click();

  const lastMessage = page.getByTestId("chat-message-assistant").last();
  await expect(lastMessage).toContainText(/.+/, { timeout: 10_000 });
});
