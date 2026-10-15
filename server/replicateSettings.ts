import fs from "fs";
import path from "path";

/** The in-app Replicate On / Off switch. A tiny file in the data folder, read on every job. Default On. */
export type ReplicateSettings = { enabled: boolean };

export function replicateSettingsPath(): string {
  return process.env.REPLICATE_SETTINGS_PATH
    ? path.resolve(process.env.REPLICATE_SETTINGS_PATH)
    : path.join(process.cwd(), "data", "replicate-settings.json");
}

export function readReplicateSettings(filePath = replicateSettingsPath()): ReplicateSettings {
  try {
    const parsed = JSON.parse(fs.readFileSync(filePath, "utf8")) as { enabled?: unknown };
    if (parsed && parsed.enabled === false) return { enabled: false };
  } catch {
    // A missing or broken file means On.
  }
  return { enabled: true };
}

export function writeReplicateSettings(input: { enabled?: unknown }, filePath = replicateSettingsPath()): ReplicateSettings {
  if (typeof input.enabled !== "boolean") throw new Error("Send enabled as true or false.");
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify({ enabled: input.enabled }), "utf8");
  return { enabled: input.enabled };
}
