import { getFlyerzTempRoot } from "./envPaths";
import { progressFileFromContext } from "./jobProgress";

/** Built at call time so a `.env` loaded during startup is included. */
export function pythonChildEnv(): Record<string, string> {
  const env: Record<string, string> = {
    ...(process.env as Record<string, string>),
    PYTHONUNBUFFERED: "1",
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  };
  if (!env.FAI_TEMP_DIR?.trim()) {
    env.FAI_TEMP_DIR = getFlyerzTempRoot();
  }
  const progressFile = progressFileFromContext();
  if (progressFile) env.JOB_PROGRESS_FILE = progressFile;
  return env;
}
