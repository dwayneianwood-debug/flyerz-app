import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import express from "express";
import { UPLOAD_LIMIT_BYTES, uploadTooLargeMessage } from "../shared/uploadLimit.ts";
import { diskUpload, rejectOversizedUpload, uploadLimitError } from "./uploadLimit.ts";

function capture() {
  const state = { status: 0, body: "", type: "" };
  const res = {
    headersSent: false,
    status(code: number) {
      state.status = code;
      return res;
    },
    type(value: string) {
      state.type = value;
      return res;
    },
    send(value: string) {
      state.body = value;
      return res;
    },
  };
  return { res, state };
}

test("the plain message names the file size and the 500 MB limit", () => {
  assert.equal(UPLOAD_LIMIT_BYTES, 500 * 1024 * 1024);
  assert.equal(uploadTooLargeMessage(200 * 1024 * 1024), "This file is 200 MB; the limit is 500 MB");
  assert.equal(uploadTooLargeMessage(600 * 1024 * 1024), "This file is 600 MB; the limit is 500 MB");
});

test("a 200 MB upload is allowed through and a larger one is a plain 413", () => {
  let passed = false;
  const small = capture();
  rejectOversizedUpload(
    { headers: { "content-length": String(200 * 1024 * 1024) } } as express.Request,
    small.res as unknown as express.Response,
    () => {
      passed = true;
    },
  );
  assert.equal(passed, true);
  assert.equal(small.state.status, 0);

  const big = capture();
  let continued = false;
  rejectOversizedUpload(
    { headers: { "content-length": String(600 * 1024 * 1024) } } as express.Request,
    big.res as unknown as express.Response,
    () => {
      continued = true;
    },
  );
  assert.equal(continued, false);
  assert.equal(big.state.status, 413);
  assert.equal(big.state.body, "This file is 600 MB; the limit is 500 MB");
  assert.match(big.state.type, /text\/plain/);
});

test("multer's file-too-large error is 413, not 500", () => {
  const caught = capture();
  let continued = false;
  uploadLimitError(
    { code: "LIMIT_FILE_SIZE", message: "File too large" },
    { headers: { "content-length": String(620 * 1024 * 1024) } } as express.Request,
    caught.res as unknown as express.Response,
    () => {
      continued = true;
    },
  );
  assert.equal(continued, false);
  assert.equal(caught.state.status, 413);
  assert.equal(caught.state.body, "This file is 620 MB; the limit is 500 MB");
  assert.notEqual(caught.state.status, 500);
});

test("an upload is streamed onto disk", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "flyerz-upload-"));
  const upload = diskUpload(dir);
  const app = express();
  app.post("/upload", upload.single("file"), (req, res) => {
    const file = req.file;
    res.status(201).json({
      path: file?.path || "",
      size: file?.size || 0,
      onDisk: file ? fs.existsSync(file.path) : false,
    });
  });
  const server = http.createServer(app);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  const boundary = "----flyerzboundary";
  const payload = Buffer.from("poster-bytes-".repeat(1000));
  const body = Buffer.concat([
    Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="poster.pdf"\r\nContent-Type: application/pdf\r\n\r\n`,
    ),
    payload,
    Buffer.from(`\r\n--${boundary}--\r\n`),
  ]);
  const response = await new Promise<{ status: number; json: { path: string; size: number; onDisk: boolean } }>((resolve, reject) => {
    const req = http.request(
      {
        hostname: "127.0.0.1",
        port,
        path: "/upload",
        method: "POST",
        headers: {
          "content-type": `multipart/form-data; boundary=${boundary}`,
          "content-length": String(body.length),
        },
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk) => chunks.push(chunk as Buffer));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          resolve({ status: res.statusCode || 0, json: JSON.parse(raw) });
        });
      },
    );
    req.on("error", reject);
    req.end(body);
  });
  server.close();
  assert.equal(response.status, 201);
  assert.equal(response.json.onDisk, true);
  assert.equal(response.json.size, payload.length);
  assert.equal(fs.readFileSync(response.json.path).length, payload.length);
  assert.ok(response.json.path.startsWith(dir));
});
