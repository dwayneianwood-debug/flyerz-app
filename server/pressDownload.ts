/** Where a press PDF lives for the download routes. */

export interface PressJobFile {
  correctedPath?: string | null;
  auditResults?: {
    compiledPdfPath?: string;
    quickPrint?: { pressPath?: string };
  } | null;
}

export function pressFileCandidates(job: PressJobFile): string[] {
  const audit = job.auditResults;
  const found: string[] = [];
  const push = (value?: string | null) => {
    const filePath = (value || "").trim();
    if (filePath && !found.includes(filePath)) found.push(filePath);
  };
  push(audit?.compiledPdfPath);
  push(audit?.quickPrint?.pressPath);
  if (audit?.quickPrint) push(job.correctedPath);
  return found;
}

export function firstExistingPressFile(
  job: PressJobFile,
  isSafe: (filePath: string) => boolean,
  exists: (filePath: string) => boolean,
): string | null {
  for (const candidate of pressFileCandidates(job)) {
    if (isSafe(candidate) && exists(candidate)) return candidate;
  }
  return null;
}
