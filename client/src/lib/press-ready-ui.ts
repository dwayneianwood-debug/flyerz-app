/** Labels for the automatic press-ready choice. Manual bleed styles stay separate. */

export const AUTOMATIC_BLEED_ID = "auto";
export const AUTOMATIC_BLEED_LABEL = "Automatic (recommended)";

export function bleedMethodChoices(manualIds: readonly string[]): string[] {
  return [AUTOMATIC_BLEED_ID, ...manualIds];
}

export function pressReadyHeadline(engine: { passed?: boolean; status?: string } | null | undefined): string {
  if (engine?.passed === true || engine?.status === "ready") return "Ready for press";
  if (engine?.status === "needs-attention") return "Needs attention";
  return AUTOMATIC_BLEED_LABEL;
}

/** The dropdown already says Automatic, so the first visit must still build the press file. */
export function shouldStartAutomaticCompile(input: {
  status?: string;
  selected?: string;
  alreadyStarted?: boolean;
  hasVariants?: boolean;
  canAssess?: boolean;
  artworkGateReady?: boolean;
  pressStatus?: string;
  /** Quick mode or an earlier compile already made the press PDF. */
  hasExistingPress?: boolean;
}): boolean {
  if (input.alreadyStarted) return false;
  if (input.status === "processing" || input.status !== "complete") return false;
  if (input.hasExistingPress) return false;
  if ((input.selected || AUTOMATIC_BLEED_ID) !== AUTOMATIC_BLEED_ID) return false;
  if (input.hasVariants) return false;
  if (input.canAssess && !input.artworkGateReady) return false;
  if (input.pressStatus === "ready" || input.pressStatus === "needs-attention") return false;
  return true;
}
