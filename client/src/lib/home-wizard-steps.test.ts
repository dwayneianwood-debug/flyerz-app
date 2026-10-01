import assert from "node:assert/strict";
import test from "node:test";
import { HOME_WIZARD_STEPS } from "./home-wizard-steps";

test("home page steps follow the upload wizard", () => {
  assert.deepEqual(
    HOME_WIZARD_STEPS.map((step) => step.label),
    ["Upload", "Size", "Crop & Submit"],
  );
});
