/**
 * Load the project `.env` before any other server module reads process.env.
 * Import this as the first import of the server entry. Existing environment
 * variables win over the file.
 */
import fs from "fs";
import path from "path";

export function loadEnvFile(envPath = path.join(process.cwd(), ".env")): void {
  try {
    if (!fs.existsSync(envPath)) return;
    const raw = fs.readFileSync(envPath, "utf8");
    for (const line of raw.split(/\r?\n/)) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      const eq = t.indexOf("=");
      if (eq <= 0) continue;
      const k = t.slice(0, eq).trim();
      let v = t.slice(eq + 1).trim();
      if (
        (v.startsWith('"') && v.endsWith('"')) ||
        (v.startsWith("'") && v.endsWith("'"))
      ) {
        v = v.slice(1, -1);
      }
      if (process.env[k] === undefined) process.env[k] = v;
    }
  } catch {
    /* ignore a missing or malformed .env */
  }
}

loadEnvFile();
