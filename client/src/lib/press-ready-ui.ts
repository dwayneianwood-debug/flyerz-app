/** Labels for the automatic press-ready choice. Manual bleed styles stay separate. */

export const AUTOMATIC_BLEED_ID = "auto";
export const AUTOMATIC_BLEED_LABEL = "Automatic, recommended";

export function bleedMethodChoices(manualIds: readonly string[]): string[] {
  return [AUTOMATIC_BLEED_ID, ...manualIds];
}

export function pressReadyHeadline(engine: { passed?: boolean; status?: string } | null | undefined): string {
  if (engine?.passed === true || engine?.status === "ready") return "Ready for press";
  if (engine?.status === "needs-attention") return "Needs attention";
  return AUTOMATIC_BLEED_LABEL;
}
