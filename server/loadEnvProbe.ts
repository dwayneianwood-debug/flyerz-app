/**
 * Child entry for the .env test. The first import loads `.env` from the
 * process working directory, then a Python child must see that environment.
 */
import "./loadEnv";
import { spawnSync } from "child_process";
import { pythonChildEnv } from "./pythonChildEnv";

const pythonBin = process.env.PYTHON_BIN || (process.platform === "win32" ? "python" : "python3");
const proc = spawnSync(
  pythonBin,
  [
    "-c",
    "import os; print(os.environ.get('FLYERZ_DOTENV_PROBE','')); print(os.environ.get('FLYERZ_DOTENV_KEEP',''))",
  ],
  { encoding: "utf8", env: pythonChildEnv() },
);

process.stdout.write(proc.stdout || "");
if (proc.status !== 0) {
  process.stderr.write(proc.stderr || proc.error?.message || "python probe failed");
  process.exit(proc.status || 1);
}
