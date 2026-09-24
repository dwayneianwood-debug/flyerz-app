import React, { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import { coverCropFrame, offsetFromPointer, type FitChoice } from "@/lib/ai-artwork-fit";

export interface AiArtworkPlan {
  detected?: boolean;
  saved?: boolean;
  reasons?: string[];
  mismatch?: boolean;
  fit?: FitChoice;
  offset?: number;
  bleed?: string;
  bleedOverridden?: boolean;
  edge?: { c: number; m: number; y: number; k: number };
  enhance?: boolean;
  enhanceOverridden?: boolean;
  effectiveDpi?: number | null;
  bright?: boolean;
  brightMessage?: string;
  textStatus?: string;
  textWarnings?: Array<{ word: string; suggestion?: string }>;
  textMessage?: string;
  applied?: string[];
  note?: string;
  blocked?: boolean;
  srcW?: number;
  srcH?: number;
  sourceUrl?: string;
  screenUrl?: string;
  printUrl?: string;
  textStatusRaw?: string;
}

const BLEED_LABELS: Record<string, string> = {
  colourBorder: "Colour Border matched to the edge",
  mirror: "Mirror, because the edges look photographic",
  replicate: "Edge replicate",
  stretch: "Stretch",
  bgExtract: "Background extract",
};

export interface AiArtworkPanelViewProps {
  plan: AiArtworkPlan;
  trimWidthMm: number;
  trimHeightMm: number;
  onFit: (fit: FitChoice) => void;
  onOffset: (offset: number) => void;
}

export function AiArtworkPanelView({ plan, trimWidthMm, trimHeightMm, onFit, onOffset }: AiArtworkPanelViewProps) {
  const frame = coverCropFrame(plan.srcW || 1, plan.srcH || 1, trimWidthMm, trimHeightMm, plan.offset ?? 0.5);
  const dragRef = useRef<HTMLDivElement | null>(null);
  const fit = plan.fit || "crop";

  const moveCrop = (clientX: number, clientY: number) => {
    const node = dragRef.current;
    if (!node || frame.axis === "none") return;
    const rect = node.getBoundingClientRect();
    const ratio = frame.axis === "x"
      ? (clientX - rect.left) / Math.max(rect.width, 1)
      : (clientY - rect.top) / Math.max(rect.height, 1);
    onOffset(offsetFromPointer(frame.axis, ratio, frame));
  };

  return (
    <div className="rounded-xl border border-sky-500/40 bg-sky-50/50 dark:bg-sky-500/5 p-4" data-testid="section-ai-artwork">
      <div className="flex items-start gap-3">
        <div className="w-8 h-8 rounded-full bg-sky-500/15 text-sky-700 dark:text-sky-300 flex items-center justify-center shrink-0">
          <Sparkles className="w-4 h-4" />
        </div>
        <div>
          <h3 className="text-base font-bold text-foreground" data-testid="text-ai-artwork-title">AI-generated artwork detected</h3>
          <p className="text-sm text-muted-foreground mt-0.5" data-testid="text-ai-artwork-intro">
            This looks like artwork from an image generator. Print defaults are already set so it can go to the litho press. Change anything you like.
          </p>
        </div>
      </div>

      {plan.reasons && plan.reasons.length > 0 ? (
        <p className="mt-3 text-xs text-muted-foreground" data-testid="text-ai-artwork-reasons">{plan.reasons.join(" · ")}</p>
      ) : null}

      {plan.applied && plan.applied.length > 0 ? (
        <ul className="mt-3 space-y-1 text-sm text-foreground" data-testid="ai-artwork-applied">
          {plan.applied.map((item) => (
            <li key={item}>• {item}</li>
          ))}
        </ul>
      ) : null}

      {plan.mismatch ? (
        <div className="mt-4">
          <p className="text-sm font-semibold text-foreground">The picture shape doesn’t match this print size</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {([
              ["crop", "Crop to fit"],
              ["extend", "Extend the background"],
              ["border", "Colour border"],
            ] as Array<[FitChoice, string]>).map(([id, label]) => (
              <button
                key={id}
                type="button"
                aria-pressed={fit === id}
                data-testid={`choice-ai-fit-${id}`}
                onClick={() => onFit(id)}
                className={`rounded-md px-3 py-2 text-sm font-semibold border ${fit === id ? "bg-sky-600 text-white border-sky-600" : "bg-white/80 dark:bg-background/40 border-border text-foreground"}`}
              >
                {label}
              </button>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            {fit === "crop"
              ? "Crop to fit is selected. Drag the picture to choose what stays. The dashed box is the safe zone, inside the cut."
              : fit === "extend"
                ? "The whole picture stays. The gaps are filled by extending the edges."
                : "The whole picture stays. The gaps are filled with the colour from the edge."}
          </p>
          {fit === "crop" && plan.sourceUrl ? (
            <div
              ref={dragRef}
              className="relative mt-3 max-w-md overflow-hidden rounded-lg bg-neutral-900 touch-none"
              data-testid="ai-artwork-crop"
              onPointerDown={(event) => {
                (event.currentTarget as HTMLDivElement).setPointerCapture(event.pointerId);
                moveCrop(event.clientX, event.clientY);
              }}
              onPointerMove={(event) => {
                if (!(event.currentTarget as HTMLDivElement).hasPointerCapture(event.pointerId)) return;
                moveCrop(event.clientX, event.clientY);
              }}
            >
              <img src={plan.sourceUrl} alt="Artwork crop preview" className="block w-full h-auto select-none" draggable={false} />
              <div className="pointer-events-none absolute bg-black/45" style={{ left: 0, top: 0, right: 0, height: `${frame.top}%` }} />
              <div className="pointer-events-none absolute bg-black/45" style={{ left: 0, right: 0, top: `${frame.top + frame.height}%`, bottom: 0 }} />
              <div className="pointer-events-none absolute bg-black/45" style={{ left: 0, top: `${frame.top}%`, width: `${frame.left}%`, height: `${frame.height}%` }} />
              <div className="pointer-events-none absolute bg-black/45" style={{ left: `${frame.left + frame.width}%`, right: 0, top: `${frame.top}%`, height: `${frame.height}%` }} />
              <div
                className="pointer-events-none absolute border-2 border-white"
                style={{ left: `${frame.left}%`, top: `${frame.top}%`, width: `${frame.width}%`, height: `${frame.height}%` }}
              >
                <div
                  className="absolute border border-dashed border-amber-300"
                  style={{ inset: `${frame.safeY}% ${frame.safeX}%` }}
                  data-testid="ai-artwork-safe-zone"
                />
                <span className="absolute left-1 top-1 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-semibold text-white">Safe zone</span>
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">The picture shape already matches this print size.</p>
      )}

      <p className="mt-3 text-sm text-foreground" data-testid="text-ai-artwork-bleed">
        Suggested bleed: {BLEED_LABELS[plan.bleed || "mirror"] || plan.bleed}. You can pick a different bleed style below.
      </p>

      {plan.bright && plan.brightMessage ? (
        <div className="mt-3" data-testid="ai-artwork-colour-warning">
          <p className="text-sm text-amber-800 dark:text-amber-300">{plan.brightMessage}</p>
          {plan.screenUrl && plan.printUrl ? (
            <div className="mt-2 grid grid-cols-2 gap-2 max-w-md">
              <figure>
                <img src={plan.screenUrl} alt="Screen colours" className="w-full rounded border border-border" data-testid="img-ai-screen" />
                <figcaption className="mt-1 text-[11px] text-muted-foreground">On screen</figcaption>
              </figure>
              <figure>
                <img src={plan.printUrl} alt="Approximate print colours" className="w-full rounded border border-border" data-testid="img-ai-soft-proof" />
                <figcaption className="mt-1 text-[11px] text-muted-foreground">Approximate print</figcaption>
              </figure>
            </div>
          ) : null}
        </div>
      ) : null}

      {plan.textMessage ? (
        <p
          className={`mt-3 text-sm ${plan.textStatus === "warning" ? "text-amber-800 dark:text-amber-300" : "text-muted-foreground"}`}
          data-testid="ai-artwork-text-warning"
        >
          {plan.textMessage}
        </p>
      ) : null}
    </div>
  );
}

interface AiArtworkPanelProps {
  jobId: number;
  trimWidthMm: number;
  trimHeightMm: number;
  onPlan?: (plan: AiArtworkPlan & { applyBleed?: boolean }) => void;
  onRefit?: () => void;
}

export function AiArtworkPanel({ jobId, trimWidthMm, trimHeightMm, onPlan, onRefit }: AiArtworkPanelProps) {
  const [plan, setPlan] = useState<AiArtworkPlan | null>(null);
  const onPlanRef = useRef(onPlan);
  const onRefitRef = useRef(onRefit);
  onPlanRef.current = onPlan;
  onRefitRef.current = onRefit;
  const offsetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const persist = async (patch: Record<string, unknown>, refit: boolean) => {
    const response = await fetch(`/api/jobs/${jobId}/ai-artwork/choice`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trimW: trimWidthMm, trimH: trimHeightMm, ...patch }),
    });
    const data = await response.json();
    if (data?.detected) setPlan(data);
    if (refit) onRefitRef.current?.();
    return data;
  };

  useEffect(() => {
    let cancel = false;
    const url = `/api/jobs/${jobId}/ai-artwork/assess?trimW=${encodeURIComponent(String(trimWidthMm))}&trimH=${encodeURIComponent(String(trimHeightMm))}`;
    fetch(url)
      .then((response) => response.json())
      .then(async (data) => {
        if (cancel || !data) return;
        if (!data.detected) {
          onPlanRef.current?.({ detected: false, applyBleed: false });
          return;
        }
        let current = data;
        if (!data.saved) {
          const saved = await fetch(`/api/jobs/${jobId}/ai-artwork/choice`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              trimW: trimWidthMm,
              trimH: trimHeightMm,
              fit: data.fit,
              offset: data.offset ?? 0.5,
              enhance: data.enhance,
              bleed: data.bleed,
              textStatus: data.textStatus,
              textWarnings: data.textWarnings,
              textMessage: data.textMessage,
            }),
          });
          const body = await saved.json();
          if (body?.detected) current = body;
        }
        if (cancel) return;
        setPlan(current);
        onPlanRef.current?.({ ...current, applyBleed: !data.saved });
      })
      .catch(() => {
        if (!cancel) onPlanRef.current?.({ detected: false, applyBleed: false });
      });
    return () => {
      cancel = true;
    };
  }, [jobId, trimWidthMm, trimHeightMm]);

  if (!plan?.detected) return null;

  return (
    <AiArtworkPanelView
      plan={plan}
      trimWidthMm={trimWidthMm}
      trimHeightMm={trimHeightMm}
      onFit={(fit) => {
        setPlan((current) => (current ? { ...current, fit } : current));
        void persist({
          fit,
          offset: plan.offset ?? 0.5,
          enhance: plan.enhance,
          bleed: plan.bleed,
          textStatus: plan.textStatus,
          textWarnings: plan.textWarnings,
          textMessage: plan.textMessage,
          enhanceOverridden: !!plan.enhanceOverridden,
          bleedOverridden: !!plan.bleedOverridden,
        }, true);
      }}
      onOffset={(offset) => {
        setPlan((current) => (current ? { ...current, offset } : current));
        if (offsetTimer.current) clearTimeout(offsetTimer.current);
        offsetTimer.current = setTimeout(() => {
          void persist({
            fit: plan.fit,
            offset,
            enhance: plan.enhance,
            bleed: plan.bleed,
            textStatus: plan.textStatus,
            textWarnings: plan.textWarnings,
            textMessage: plan.textMessage,
            enhanceOverridden: !!plan.enhanceOverridden,
            bleedOverridden: !!plan.bleedOverridden,
          }, true);
        }, 400);
      }}
    />
  );
}
