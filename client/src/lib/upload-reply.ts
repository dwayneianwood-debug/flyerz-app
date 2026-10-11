/** The start button shows the server's words, including a plain-text 413. */
export function messageFromStartResponse(body: string, ok: boolean): string {
  if (ok) return "";
  const trimmed = String(body || "").trim();
  if (!trimmed) return "Could not start.";
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      const payload = JSON.parse(trimmed) as { message?: unknown };
      if (typeof payload?.message === "string" && payload.message.trim()) return payload.message;
    } catch {
      return trimmed;
    }
    return "Could not start.";
  }
  return trimmed;
}
