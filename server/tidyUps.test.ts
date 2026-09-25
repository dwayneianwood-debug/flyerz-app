import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const CUSTOMER_COPY = [
  "server/compile_press_pdf.py",
  "server/routes.ts",
];

test("customer-facing reports spell Intelligence", () => {
  for (const file of CUSTOMER_COPY) {
    const text = fs.readFileSync(file, "utf8");
    assert.equal(text.includes("Intellegence"), false, file);
    assert.match(text, /Flyerz\.co\.za Artwork Intelligence Proof and Report\.pdf/);
  }
});

test("the stress-test download route is gone", () => {
  const routes = fs.readFileSync("server/routes.ts", "utf8");
  assert.equal(routes.includes("/api/test-pdf/download"), false);
  assert.equal(fs.existsSync("stress_test.pdf"), false);
  assert.equal(fs.existsSync("main.py"), false);
  assert.equal(fs.existsSync("add"), false);
});
