/** Picks the edit / AI enlarge / upscale outcome lines out of a job's saved decisions. UI only. */
export type EditOutcome = { text: string; tone: "ok" | "warn" };

const OK = /was changed before the press file|edited with Replicate|Real-ESRGAN|AI enhancement was applied/i;
const WARN = /small text edit could not|can't safely change|couldn't read that|could not find a|send the editable file|have not changed anything|Replicate is not set up|Replicate is ready\. This file stays|Replicate did not return|Replicate edit could not|local enlargement was used|enlarged with Lanczos|was not enhanced|enhancement was not run/i;

export function editOutcomes(decisions: string[] | undefined): EditOutcome[] {
  const out: EditOutcome[] = [];
  const seen = new Set<string>();
  for (const raw of decisions || []) {
    const text = String(raw || "").trim();
    if (!text || seen.has(text)) continue;
    // Plain "left off" lines just mean the box was not ticked.
    if (/Upscale with AI was left off/i.test(text)) continue;
    if (WARN.test(text)) { out.push({ text, tone: "warn" }); seen.add(text); }
    else if (OK.test(text)) { out.push({ text, tone: "ok" }); seen.add(text); }
  }
  return out;
}

export function isPictureFile(name: string): boolean {
  return /\.(jpe?g|png|webp|tiff?|bmp)$/i.test(name || "");
}
