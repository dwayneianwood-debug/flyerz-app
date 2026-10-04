import React from "react";
import { CheckCircle2, Loader2, Sparkles } from "lucide-react";
import { ColourBorderPicker } from "@/components/colour-border-picker";
import type { ColourBorderChoice } from "@/lib/colour-border";
import { AUTOMATIC_BLEED_LABEL, pressReadyHeadline } from "@/lib/press-ready-ui";
import { BLEED_STRATEGY_IDS } from "@shared/schema";

export const BLEED_METHOD_LABELS = {
  auto: {
    label: AUTOMATIC_BLEED_LABEL,
    description: "The press-ready engine checks each edge and fills only the bleed. You can still pick a style yourself below.",
  },
  bgExtract: {
    label: "Background Extract",
    description: "Extends only the background colour, ideal for artwork with text near the edge.",
  },
  stretch: {
    label: "Pixel-Drift Stretch",
    description: "Projects edge pixels outward with gentle drift, best for complex images.",
  },
  mirror: {
    label: "Mirror + Blend",
    description: "Mirrors the edge and cross-fades the seam, great for busy textures.",
  },
  replicate: {
    label: "Edge Replication",
    description: "Repeats the outermost pixel row, cleanest for solid colours.",
  },
  upscale: {
    label: "Upscale",
    description: "Smoothly scales entire artwork to include bleed — perfectly smooth boundaries.",
  },
  ai_outpaint: {
    label: "AI Outpaint",
    description:
      "Fast proxy inpainting extends bleed colors softly; your 300 DPI artwork stays pixel-perfect in the center.",
  },
  colourBorder: {
    label: "Colour Border",
    description: "Keeps the artwork at trim size and fills the bleed with a solid colour you choose.",
  },
  gradient_extrapolate: {
    label: "Gradient Extrapolate",
    description: "Continues a colour gradient past the edge.",
  },
  frequency_separated: {
    label: "Frequency Separated",
    description: "Extends the edge by separating smooth colour from fine detail.",
  },
} as const;

export function BleedMethodSelector({ jobId, variants, recommended, selected, onSelect, loading, colourBorder, onColourBorderChange, pressEngine, beforeUrl, afterUrl, pressDownloadHref, proofPage = 0, onProofPage, variantPages }: {
  jobId: number;
  variants: Record<string, string>;
  recommended: string | null;
  selected: string;
  onSelect: (method: string) => void;
  loading: boolean;
  colourBorder: ColourBorderChoice;
  onColourBorderChange: (next: ColourBorderChoice) => void;
  pressEngine?: {
    passed?: boolean;
    status?: string;
    headline?: string;
    reason?: string;
    fix?: string;
    rescue?: { note?: string };
    edges?: Array<{ side: string; note: string }>;
  } | null;
  beforeUrl?: string | null;
  afterUrl?: string | null;
  pressDownloadHref?: string | null;
  proofPage?: number;
  onProofPage?: (page: number) => void;
  variantPages?: Record<string, string[]> | null;
}) {
  /** Automatic is the default. The older styles stay as manual overrides. */
  const methods = ["auto", ...BLEED_STRATEGY_IDS];
  const activeMethod = selected || "auto";
  const activeInfo = activeMethod ? BLEED_METHOD_LABELS[activeMethod as keyof typeof BLEED_METHOD_LABELS] : null;
  const engineAttention = pressEngine?.status === "needs-attention";

  if (methods.length === 0) return null;

  return (
    <div data-testid="section-bleed-method-selector">
      <div className="flex items-center gap-2 mb-3">
        <Sparkles className="w-4 h-4 text-primary" />
        <h4 className="text-sm font-bold text-foreground">Choose Your Bleed Style</h4>
        {loading && (
          <div className="flex items-center gap-2 ml-auto">
            <Loader2 className="w-4 h-4 animate-spin text-primary" />
            <span className="text-xs font-medium text-primary animate-pulse" data-testid="text-bleed-processing">Processing...</span>
          </div>
        )}
      </div>
      <div className="flex flex-col gap-3">
        <select
          value={activeMethod || ""}
          onChange={(e) => onSelect(e.target.value)}
          disabled={loading}
          className="w-full px-3 py-2.5 rounded-lg border-2 border-border/60 bg-white dark:bg-background text-sm font-medium text-foreground focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all disabled:opacity-50 disabled:cursor-not-allowed appearance-none cursor-pointer"
          style={{ backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='none' viewBox='0 0 24 24' stroke='%236b7280' stroke-width='2'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' d='M19 9l-7 7-7-7'/%3E%3C/svg%3E")`, backgroundRepeat: "no-repeat", backgroundPosition: "right 12px center" }}
          data-testid="select-bleed-method"
        >
          {methods.map((method) => {
            const info = BLEED_METHOD_LABELS[method as keyof typeof BLEED_METHOD_LABELS];
            void recommended;
            return (
              <option key={method} value={method} data-testid={`option-bleed-method-${method}`}>
                {info?.label ?? method}
              </option>
            );
          })}
        </select>
        {activeInfo && (
          <p className="text-xs text-muted-foreground" data-testid="text-bleed-description">
            {activeInfo.description}
          </p>
        )}
        {activeMethod === "auto" && (
          <div className="rounded-lg border border-border/60 bg-white dark:bg-background p-3 space-y-2" data-testid="panel-press-ready">
            <p className={`text-sm font-semibold ${engineAttention ? "text-amber-700" : "text-green-700"}`} data-testid="text-press-ready-headline">
              {pressReadyHeadline(pressEngine)}
            </p>
            {pressDownloadHref && (
              <a href={pressDownloadHref} className="inline-flex text-sm font-semibold underline" data-testid="link-job-press-download">Download press PDF</a>
            )}
            {pressEngine?.reason && (
              <p className="text-xs text-muted-foreground" data-testid="text-press-ready-reason">{pressEngine.reason} {pressEngine.fix}</p>
            )}
            {pressEngine?.rescue?.note && (
              <p className="text-xs text-muted-foreground" data-testid="text-press-ready-rescue">{pressEngine.rescue.note}</p>
            )}
            <ul className="space-y-1" data-testid="list-press-ready-edges">
              {(pressEngine?.edges || []).map((edge) => (
                <li key={edge.side} className="text-xs text-foreground">{edge.note}</li>
              ))}
            </ul>
            {(beforeUrl || afterUrl) && (
              <div className="grid grid-cols-2 gap-2">
                {beforeUrl && <img src={beforeUrl} alt="Artwork before bleed" className="w-full h-24 object-contain bg-muted rounded" data-testid="img-press-before" />}
                {afterUrl && <img src={afterUrl} alt="Artwork with bleed and cut line" className="w-full h-24 object-contain bg-muted rounded" data-testid="img-press-after" />}
              </div>
            )}
          </div>
        )}
        {activeMethod === "colourBorder" && (
          <ColourBorderPicker value={colourBorder} onChange={onColourBorderChange} disabled={loading} />
        )}
        {activeMethod && activeMethod !== "auto" && (
          <div className={`relative rounded-lg border-2 border-primary/30 overflow-hidden bg-gray-100 dark:bg-gray-800 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}>
            {(variantPages?.[activeMethod]?.length || 0) > 1 && (
              <div className="flex items-center gap-2 flex-wrap p-2" data-testid="style-page-selector">
                {variantPages?.[activeMethod]?.map((_, index) => (
                  <button
                    key={index}
                    type="button"
                    className={`text-xs px-2 py-1 rounded border ${proofPage === index ? "bg-primary text-primary-foreground" : "bg-background"}`}
                    onClick={() => onProofPage?.(index)}
                    data-testid={`button-style-page-${index + 1}`}
                  >
                    Page {index + 1}
                  </button>
                ))}
              </div>
            )}
            <div className="aspect-[16/9]">
              {activeMethod === "colourBorder" ? (
              <img
                src={`/api/jobs/${jobId}/colour-border-preview?c=${colourBorder.c}&m=${colourBorder.m}&y=${colourBorder.y}&k=${colourBorder.k}&lines=0`}
                alt="Colour border preview"
                className="w-full h-full object-contain"
                data-testid="img-bleed-variant-colourBorder"
              />
              ) : variants[activeMethod] ? (
              <img
                src={`/api/jobs/${jobId}/bleed-variant/${activeMethod}?page=${proofPage || 0}`}
                alt={activeInfo?.label || activeMethod}
                className="w-full h-full object-contain"
                data-testid={`img-bleed-variant-${activeMethod}`}
              />
              ) : (
                <div
                  className="w-full h-full flex items-center justify-center px-4 text-center text-xs text-muted-foreground"
                  data-testid={`placeholder-bleed-variant-${activeMethod}`}
                >
                  No cached preview for this strategy — select &quot;Apply&quot; or re-run processing to generate it.
                </div>
              )}
              {loading && (
                <div className="absolute inset-0 bg-black/30 flex items-center justify-center" data-testid="overlay-loading-bleed">
                  <Loader2 className="w-6 h-6 animate-spin text-white" />
                </div>
              )}
            </div>
            <div className="absolute top-2 left-2 flex items-center gap-1 px-2 py-1 rounded-full bg-primary/90 text-primary-foreground text-[10px] font-bold">
              <CheckCircle2 className="w-3 h-3" />
              {activeInfo?.label || activeMethod}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
