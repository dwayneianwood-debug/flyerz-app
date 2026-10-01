export interface GlitchyPlacementInput {
  narrow: boolean;
  formFocused: boolean;
  shareOrDownloadInView: boolean;
  userOpened: boolean;
  viewportHeight: number;
  obstacleTops: number[];
}

export interface GlitchyPlacement {
  collapsed: boolean;
  /** Pixels from the bottom of the screen. Raised when a button would sit under the cat. */
  bottom: number;
}

const BASE_BOTTOM = 12;
const CLEARANCE = 8;

/** Keep Glitchy a small corner control, and lift it when it would cover Share or a form. */
export function glitchyPlacement(input: GlitchyPlacementInput): GlitchyPlacement {
  const busy = input.formFocused || input.shareOrDownloadInView;
  const collapsed = busy || (input.narrow && !input.userOpened);
  let bottom = BASE_BOTTOM;
  for (const top of input.obstacleTops) {
    if (!Number.isFinite(top)) continue;
    const fromBottom = input.viewportHeight - top + CLEARANCE;
    if (fromBottom > bottom) bottom = fromBottom;
  }
  const maxLift = Math.max(BASE_BOTTOM, Math.round(input.viewportHeight * 0.45));
  return { collapsed, bottom: Math.min(bottom, maxLift) };
}
