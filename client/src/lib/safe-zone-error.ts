/**
 * Safe-zone layout rejections: Glitchy speech bubble only — never the destructive toast.
 */
export const SAFE_ZONE_LAYOUT_USER_MESSAGE =
  "Text or logos are too close to the edge to safely auto-fix. Please move text inward by at least 3mm.";

const SAFE_ZONE_LAYOUT_NEEDLE = "too close to the edge";

const MAX_DEPTH = 8;

function tryParseJsonString(s: string): unknown {
  const t = s.trim();
  if (!t || (t[0] !== "{" && t[0] !== "[")) return null;
  try {
    return JSON.parse(t);
  } catch {
    return null;
  }
}

/** Collect every user-visible string from an error object (message, response.data, JSON blobs, etc.). */
function collectErrorStrings(value: unknown, depth = 0, out: string[] = []): string[] {
  if (depth > MAX_DEPTH || value == null) return out;

  if (typeof value === "string") {
    const t = value.trim();
    if (t) out.push(t);
    const parsed = tryParseJsonString(t);
    if (parsed != null) collectErrorStrings(parsed, depth + 1, out);
    return out;
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return out;
  }

  if (value instanceof Error) {
    collectErrorStrings(value.message, depth + 1, out);
    collectErrorStrings((value as { response?: unknown }).response, depth + 1, out);
    collectErrorStrings((value as { data?: unknown }).data, depth + 1, out);
    collectErrorStrings((value as { body?: unknown }).body, depth + 1, out);
    return out;
  }

  if (typeof value === "object") {
    const o = value as Record<string, unknown>;
    collectErrorStrings(o.message, depth + 1, out);
    collectErrorStrings(o.error, depth + 1, out);
    collectErrorStrings(o.description, depth + 1, out);
    collectErrorStrings(o.detail, depth + 1, out);
    collectErrorStrings(o.response, depth + 1, out);
    collectErrorStrings(o.data, depth + 1, out);
    collectErrorStrings(o.body, depth + 1, out);
    if (typeof o.toString === "function" && o.toString !== Object.prototype.toString) {
      try {
        const ts = o.toString();
        if (typeof ts === "string" && ts !== "[object Object]") {
          collectErrorStrings(ts, depth + 1, out);
        }
      } catch {
        /* ignore */
      }
    }
  }

  return out;
}

function matchesLayoutNeedle(text: string): boolean {
  return text.toLowerCase().includes(SAFE_ZONE_LAYOUT_NEEDLE);
}

/** True when any extracted fragment indicates a safe-zone layout rejection. */
export function isSafeZoneLayoutError(error: unknown): boolean {
  const parts = collectErrorStrings(error, 0, []);
  try {
    if (error != null) collectErrorStrings(String(error), 0, parts);
  } catch {
    /* ignore */
  }
  return parts.some(matchesLayoutNeedle);
}

/** @deprecated Use isSafeZoneLayoutError — accepts full error objects, not only strings. */
export function isSafeZoneLayoutMessage(message: unknown): boolean {
  return isSafeZoneLayoutError(message);
}

/** Best user-facing layout message from an error payload, or null if not a layout rejection. */
export function extractSafeZoneLayoutMessage(error: unknown): string | null {
  if (!isSafeZoneLayoutError(error)) return null;

  const parts = collectErrorStrings(error, 0, []);
  try {
    if (error != null) collectErrorStrings(String(error), 0, parts);
  } catch {
    /* ignore */
  }

  const hit = parts.find(matchesLayoutNeedle);
  return hit?.trim() || SAFE_ZONE_LAYOUT_USER_MESSAGE;
}

export function dispatchSafeZoneLayoutGlitchy(message: string): void {
  if (typeof window === "undefined") return;
  const text = message.trim() || SAFE_ZONE_LAYOUT_USER_MESSAGE;
  window.dispatchEvent(
    new CustomEvent("glitchy:compile-error", { detail: { message: text } }),
  );
}

/**
 * Layout rejection: Glitchy only. Returns true when handled (caller must not toast).
 */
export function handleSafeZoneLayoutProcessingError(error: unknown): boolean {
  const msg = extractSafeZoneLayoutMessage(error);
  if (!msg) return false;
  dispatchSafeZoneLayoutGlitchy(msg);
  return true;
}
