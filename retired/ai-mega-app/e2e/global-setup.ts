// Boots the fake llama-swap (e2e/fake_backend.py, port 8080) and the real
// FastAPI app (app.main:app, port 8000 by default) as child processes for
// the Playwright smoke suite. Both are plain uvicorn processes — no GPU, no
// network to model hosts (PLAN.md §4.10). PIDs are stashed on
// `process.env` for global-teardown.ts to reap.
import { ChildProcess, spawn } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

const REPO_ROOT = path.resolve(__dirname, "..");
const APP_PORT = process.env.E2E_APP_PORT ?? "8000";
const FAKE_PORT = process.env.E2E_FAKE_PORT ?? "8080";
const PID_FILE = path.join(REPO_ROOT, ".e2e-pids.json");

function waitForHealth(url: string, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve) => {
    const tryOnce = () => {
      fetch(url)
        .then((r) => resolve(r.ok))
        .catch(() => {
          if (Date.now() > deadline) {
            resolve(false);
          } else {
            setTimeout(tryOnce, 200);
          }
        });
    };
    tryOnce();
  });
}

function spawnUvicorn(target: string, port: string): ChildProcess {
  return spawn(
    "uv",
    ["run", "uvicorn", target, "--host", "127.0.0.1", "--port", port],
    { cwd: REPO_ROOT, stdio: "ignore", env: { ...process.env } }
  );
}

export default async function globalSetup(): Promise<void> {
  const webExists = fs.existsSync(path.join(REPO_ROOT, "web", "index.html"));
  process.env.E2E_WEB_BUILT = webExists ? "1" : "0";

  const fake = spawnUvicorn("e2e.fake_backend:app", FAKE_PORT);
  const app = spawnUvicorn("app.main:app", APP_PORT);

  fs.writeFileSync(
    PID_FILE,
    JSON.stringify({ fake: fake.pid, app: app.pid }),
    "utf-8"
  );

  const [fakeUp, appUp] = await Promise.all([
    waitForHealth(`http://127.0.0.1:${FAKE_PORT}/health`, 15_000),
    waitForHealth(`http://127.0.0.1:${APP_PORT}/health`, 15_000),
  ]);

  if (!fakeUp || !appUp) {
    process.env.E2E_BACKEND_READY = "0";
  } else {
    process.env.E2E_BACKEND_READY = "1";
  }
}
