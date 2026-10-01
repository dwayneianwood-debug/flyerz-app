/** What the job page may start, and what the proceed button should say. */

export function jobPageMayAutoStart(status: string | undefined): boolean {
  return status !== "processing" && status !== "complete";
}

/** Endpoints the job page is allowed to fire on its own for this status. */
export function autoPostsForJobPage(status: string | undefined): string[] {
  if (!jobPageMayAutoStart(status)) return [];
  return [];
}

export interface ProceedButtonInput {
  allReviewsChecked: boolean;
  selectedBleedMethod?: string;
  prepressSpinnerActive: boolean;
  preCompileState?: string | null;
  preCompileReady: boolean;
  /** Quick mode or an automatic compile already produced a press PDF. */
  hasAutomaticPress: boolean;
}

export interface ProceedButtonState {
  disabled: boolean;
  spinning: boolean;
  label: string;
}

export function proceedButtonState(input: ProceedButtonInput): ProceedButtonState {
  const spinning = input.prepressSpinnerActive || input.preCompileState === "compiling";
  const method = input.selectedBleedMethod || "auto";
  const automaticChosen = method === "auto" && (input.hasAutomaticPress || input.preCompileReady);
  const bleedChoiceReady = method !== "auto" || automaticChosen;
  if (spinning) {
    return { disabled: true, spinning: true, label: "Preparing artwork..." };
  }
  if (!bleedChoiceReady) {
    return { disabled: true, spinning: false, label: "Choose a bleed method to continue" };
  }
  if (!input.allReviewsChecked) {
    return { disabled: true, spinning: false, label: "Review the sections above to continue" };
  }
  if (!input.preCompileReady && !input.hasAutomaticPress) {
    return { disabled: true, spinning: false, label: "Choose a bleed method to continue" };
  }
  return { disabled: false, spinning: false, label: "Proceed to Download" };
}
