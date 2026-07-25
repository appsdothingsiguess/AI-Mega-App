// Reaps the fake llama-swap + app processes started by global-setup.ts.
import * as fs from "node:fs";
import * as path from "node:path";

const REPO_ROOT = path.resolve(__dirname, "..");
const PID_FILE = path.join(REPO_ROOT, ".e2e-pids.json");

export default async function globalTeardown(): Promise<void> {
  if (!fs.existsSync(PID_FILE)) return;
  try {
    const { fake, app } = JSON.parse(fs.readFileSync(PID_FILE, "utf-8"));
    for (const pid of [fake, app]) {
      if (typeof pid === "number") {
        try {
          process.kill(pid, "SIGTERM");
        } catch {
          // already exited — fine
        }
      }
    }
  } finally {
    fs.rmSync(PID_FILE, { force: true });
  }
}
