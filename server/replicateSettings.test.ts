import test from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";
import { readReplicateSettings, writeReplicateSettings } from "./replicateSettings";

test("Replicate switch defaults On, saves Off, and reads the file each time", () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "rep-")), "data", "replicate-settings.json");
  assert.deepEqual(readReplicateSettings(file), { enabled: true });
  assert.deepEqual(writeReplicateSettings({ enabled: false }, file), { enabled: false });
  assert.deepEqual(readReplicateSettings(file), { enabled: false });
  assert.deepEqual(writeReplicateSettings({ enabled: true }, file), { enabled: true });
  assert.deepEqual(readReplicateSettings(file), { enabled: true });
  assert.throws(() => writeReplicateSettings({ enabled: "no" }, file));
  fs.writeFileSync(file, "garbage");
  assert.deepEqual(readReplicateSettings(file), { enabled: true });
});
