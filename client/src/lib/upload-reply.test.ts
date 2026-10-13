import test from "node:test";
import assert from "node:assert/strict";
import { messageFromStartResponse } from "./upload-reply.ts";

test("a plain 413 is shown as the server wrote it", () => {
  const body = "This file is 515 MB; the limit is 500 MB";
  assert.equal(messageFromStartResponse(body, false), body);
});

test("a JSON error still uses its message", () => {
  assert.equal(messageFromStartResponse(JSON.stringify({ message: "Add at least one file." }), false), "Add at least one file.");
  assert.equal(messageFromStartResponse("", false), "I couldn't start that. Please try the file again.");
  assert.equal(messageFromStartResponse("This file is 515 MB; the limit is 500 MB", true), "");
});
