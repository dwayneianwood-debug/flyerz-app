import test from "node:test";
import assert from "node:assert/strict";
import {
  INVALID_UPLOAD_MESSAGE,
  PRINT_TOOL_REJECTION,
  isAllowedPrintTool,
  isAllowedUpload,
  isIllustratorName,
  isIllustratorType,
} from "./illustratorIntake";

test("upload allow-list accepts Illustrator and EPS by extension and MIME", () => {
  assert.equal(isAllowedUpload("logo.ai", "application/octet-stream"), true);
  assert.equal(isAllowedUpload("logo.ai", "application/pdf"), true);
  assert.equal(isAllowedUpload("logo.ai", "application/postscript"), true);
  assert.equal(isAllowedUpload("logo.ai", "application/vnd.adobe.illustrator"), true);
  assert.equal(isAllowedUpload("logo.eps", "image/x-eps"), true);
  assert.equal(isAllowedUpload("logo.eps", ""), true);
  assert.equal(isAllowedUpload("logo.AI", "application/pdf"), true);
  assert.equal(isAllowedUpload("logo.ai", "text/html"), false);
  assert.equal(isAllowedUpload("logo.exe", "application/octet-stream"), false);
  assert.equal(isAllowedUpload("notes.docx", "application/zip"), true);
  assert.equal(isAllowedPrintTool("art.ai", "application/octet-stream"), true);
  assert.equal(isAllowedPrintTool("notes.docx", "application/zip"), false);
  assert.equal(isIllustratorName("Board.AI"), true);
  assert.equal(isIllustratorName("mark.eps"), true);
  assert.equal(isIllustratorName("mark.pdf"), false);
  assert.equal(isIllustratorType("eps"), true);
  assert.match(INVALID_UPLOAD_MESSAGE, /AI/);
  assert.match(INVALID_UPLOAD_MESSAGE, /EPS/);
  assert.match(PRINT_TOOL_REJECTION, /AI/);
});
