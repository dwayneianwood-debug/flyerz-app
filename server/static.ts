import express, { type Express } from "express";
import fs from "fs";
import path from "path";
import { cacheControlForStaticFile } from "./staticCache";

export function serveStatic(app: Express) {
  const distPath = path.resolve(__dirname, "public");
  if (!fs.existsSync(distPath)) {
    throw new Error(
      `Could not find the build directory: ${distPath}, make sure to build the client first`,
    );
  }

  app.use(express.static(distPath, {
    etag: true,
    maxAge: 0,
    setHeaders(res, filePath) {
      res.setHeader("Cache-Control", cacheControlForStaticFile(filePath));
    },
  }));

  // fall through to index.html if the file doesn't exist
  app.use("/{*path}", (_req, res) => {
    res.setHeader("Cache-Control", "no-cache");
    res.sendFile(path.resolve(distPath, "index.html"));
  });
}
