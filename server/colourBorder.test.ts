import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { BLEED_STRATEGY_IDS } from "../shared/schema.ts";

test("colour border is a bleed strategy alongside the existing ones", () => {
  assert.ok(BLEED_STRATEGY_IDS.includes("colourBorder"));
  assert.deepEqual(
    BLEED_STRATEGY_IDS.filter((id) => id !== "colourBorder"),
    ["bgExtract", "stretch", "mirror", "replicate", "upscale", "ai_outpaint"],
  );
});

test("colour border keeps trim pixels and stamps CMYK bleed", () => {
  const proc = spawnSync("python3", ["server/colour_border_check.py"], {
    cwd: process.cwd(),
    encoding: "utf8",
    timeout: 240000,
  });
  assert.equal(proc.status, 0, `${proc.stdout}\n${proc.stderr}`);
  assert.match(proc.stdout, /all colour border checks passed/);
});
