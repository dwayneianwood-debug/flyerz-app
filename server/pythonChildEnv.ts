import { getFlyerzTempRoot } from "./envPaths";

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
  return env;
}
