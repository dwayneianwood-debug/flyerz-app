/** Which file Automatic bleed should compile. Manual styles always keep the original. */

export type AiRebuildState = {
  detected?: boolean;
  skipped?: boolean;
  accepted?: boolean;
  pdfPath?: string;
};

export function choosePressInput(
  strategy: string,
  audit: { aiRebuild?: AiRebuildState } | null | undefined,
  originalPath: string,
  pdfExists: (filePath: string) => boolean,
): string {
  if (strategy !== "auto") return originalPath;
  const saved = audit?.aiRebuild;
  if (!saved?.detected || saved.skipped || saved.accepted !== true) return originalPath;
  const pdf = typeof saved.pdfPath === "string" ? saved.pdfPath : "";
  if (!pdf || !pdfExists(pdf)) return originalPath;
  return pdf;
}
