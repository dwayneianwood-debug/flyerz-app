import { getFlyerzTempRoot } from "./envPaths";
import { progressFileFromContext } from "./jobProgress";

/** Join pipe chunks before decoding. A split × or – byte must not become U+FFFD. */
export function decodeChildText(chunks: Buffer[]): string {
  if (!chunks.length) return "";
  return Buffer.concat(chunks).toString("utf8");
}

/** Built at call time so a `.env` loaded during startup is included. */
export function pythonChildEnv(): Record<string, string> {
  const env: Record<string, string> = {
    ...(process.env as Record<string, string>),
    PYTHONUNBUFFERED: "1",
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  };
  // en-ZA uses a decimal comma. LC_ALL would hide LC_NUMERIC from potrace and Ghostscript.
  delete env.LC_ALL;
  env.LC_NUMERIC = "C";
  if (!env.FAI_TEMP_DIR?.trim()) {
    env.FAI_TEMP_DIR = getFlyerzTempRoot();
  }
  const progressFile = progressFileFromContext();
  if (progressFile) env.JOB_PROGRESS_FILE = progressFile;
  return env;
}
