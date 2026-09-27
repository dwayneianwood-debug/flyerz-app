/** Cache policy for the built client. Hashed Vite files live under assets/. */
export function cacheControlForStaticFile(filePath: string): string {
  const normalized = filePath.replace(/\\/g, "/");
  const base = normalized.slice(normalized.lastIndexOf("/") + 1);
  if (base === "index.html") return "no-cache";
  if (normalized.includes("/assets/")) return "public, max-age=31536000, immutable";
  return "public, max-age=3600";
}
