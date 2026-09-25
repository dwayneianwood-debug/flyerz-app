import React, { useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, Sparkles } from "lucide-react";

export const AI_UPSCALE_EXPLANATION = "Make blurry or low-resolution artwork crisp and clear.";

export type AiUpscalePhase = "idle" | "running" | "ready" | "accepted" | "kept" | "fallback";

export interface AiUpscalePreview {
  beforeUrl?: string | null;
  afterUrl?: string | null;
  basic?: boolean;
  provider?: string | null;
  message?: string;
  effectiveDpi?: number | null;
  enhancedDpi?: number | null;
}

export interface AiUpscaleAssess {
  eligible?: boolean;
  suggestion?: boolean;
  strong?: boolean;
  message?: string;
  effectiveDpi?: number | null;
  effective_dpi?: number | null;
}

interface AiUpscalePanelViewProps {
  enabled: boolean;
  phase: AiUpscalePhase;
  assess: AiUpscaleAssess | null;
  preview: AiUpscalePreview | null;
  slider: number;
  onToggle: (next: boolean) => void;
  onSlider: (value: number) => void;
  onAccept: () => void;
  onKeep: () => void;
}

export function AiUpscalePanelView({
  enabled,
  phase,
  assess,
  preview,
  slider,
  onToggle,
  onSlider,
  onAccept,
  onKeep,
}: AiUpscalePanelViewProps) {
  const ineligible = assess?.eligible === false;
  const showCompare = !!preview?.beforeUrl && !!preview?.afterUrl && (phase === "ready" || phase === "accepted" || phase === "kept");
  const providerLabel = preview?.basic || preview?.provider === "basic"
    ? "Basic enhancement"
    : preview?.provider === "replicate" || preview?.provider === "stub"
      ? "AI enhancement"
      : null;

  return (
    <div className="rounded-xl border border-violet-500/30 bg-violet-50/40 dark:bg-violet-500/5 p-4" data-testid="section-ai-upscale">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="w-8 h-8 rounded-full bg-violet-500/15 text-violet-600 dark:text-violet-300 flex items-center justify-center shrink-0">
            <Sparkles className="w-4 h-4" />
          </div>
          <div>
            <h3 className="text-base font-bold text-foreground" data-testid="text-ai-upscale-title">AI Enhance / Upscale</h3>
            <p className="text-sm text-muted-foreground mt-0.5" data-testid="text-ai-upscale-explanation">{AI_UPSCALE_EXPLANATION}</p>
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label="AI Enhance / Upscale"
          disabled={ineligible || phase === "running"}
          data-testid="switch-ai-upscale"
          onClick={() => onToggle(!enabled)}
          className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${enabled ? "bg-violet-600" : "bg-muted-foreground/30"} disabled:opacity-50`}
        >
          <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${enabled ? "translate-x-5" : "translate-x-0.5"}`} />
        </button>
      </div>

      {assess?.suggestion && assess.message ? (
        <p
          className={`mt-3 text-sm font-medium ${assess.strong ? "text-amber-800 dark:text-amber-300" : "text-amber-700 dark:text-amber-400"}`}
          data-testid="ai-upscale-suggestion"
        >
          {assess.message}
        </p>
      ) : null}

      {ineligible && assess?.message ? (
        <p className="mt-3 text-sm text-muted-foreground" data-testid="ai-upscale-ineligible">{assess.message}</p>
      ) : null}

      {phase === "running" && (
        <div className="mt-4 flex items-center gap-2 text-sm text-violet-700 dark:text-violet-300" data-testid="ai-upscale-progress">
          <Loader2 className="w-4 h-4 animate-spin" />
          <span>Improving the artwork… this can take a moment.</span>
        </div>
      )}

      {phase === "fallback" && preview?.message ? (
        <p className="mt-3 text-sm text-amber-800 dark:text-amber-300" data-testid="ai-upscale-fallback">{preview.message}</p>
      ) : null}

      {showCompare && preview?.beforeUrl && preview?.afterUrl && (
        <div className="mt-4 space-y-3">
          {providerLabel ? (
            <span className="inline-flex items-center rounded-full bg-white/80 dark:bg-background/60 border border-violet-500/30 px-2 py-0.5 text-xs font-semibold text-violet-700 dark:text-violet-300" data-testid="badge-ai-upscale-provider">
              {providerLabel}
            </span>
          ) : null}
          <div className="relative aspect-square max-h-[420px] overflow-hidden rounded-lg bg-neutral-900" data-testid="ai-upscale-compare">
            <img src={preview.beforeUrl} alt="Original artwork" className="absolute inset-0 h-full w-full object-cover" data-testid="img-ai-upscale-before" />
            <img
              src={preview.afterUrl}
              alt="Enhanced artwork"
              className="absolute inset-0 h-full w-full object-cover"
              style={{ clipPath: `inset(0 ${100 - slider}% 0 0)` }}
              data-testid="img-ai-upscale-after"
            />
            <div
              className="pointer-events-none absolute inset-y-0 z-10 w-0.5 bg-white shadow-[0_0_0_1px_rgba(0,0,0,0.55)]"
              style={{ left: `${slider}%` }}
              data-testid="ai-upscale-divider"
            />
            <div className="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-2 py-0.5 text-[10px] font-semibold text-white">Original</div>
            <div className="pointer-events-none absolute right-2 top-2 rounded bg-black/60 px-2 py-0.5 text-[10px] font-semibold text-white">Enhanced</div>
          </div>
          <label className="block text-xs text-muted-foreground">
            Drag to compare before and after
            <input
              type="range"
              min={0}
              max={100}
              value={slider}
              aria-label="Compare original and enhanced artwork"
              data-testid="slider-ai-upscale"
              className="mt-1 w-full accent-violet-600"
              onChange={(event) => onSlider(Number(event.target.value))}
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              data-testid="button-accept-ai-upscale"
              onClick={onAccept}
              className={`inline-flex items-center gap-1 rounded-md px-3 py-2 text-sm font-semibold text-white ${phase === "accepted" ? "bg-green-600" : "bg-violet-600"}`}
            >
              {phase === "accepted" ? <CheckCircle2 className="w-4 h-4" /> : null}
              Accept enhancement
            </button>
            <button
              type="button"
              data-testid="button-keep-original"
              onClick={onKeep}
              className={`rounded-md border px-3 py-2 text-sm font-semibold ${phase === "kept" ? "border-green-600 text-green-700" : "border-border text-foreground"}`}
            >
              Keep original
            </button>
          </div>
          {phase === "accepted" ? (
            <p className="text-sm text-green-700 dark:text-green-400" data-testid="text-ai-upscale-accepted">Enhanced artwork will be used for the press-ready PDF.</p>
          ) : null}
          {phase === "kept" ? (
            <p className="text-sm text-muted-foreground" data-testid="text-ai-upscale-kept">Keeping your original artwork.</p>
          ) : null}
        </div>
      )}
    </div>
  );
}

interface AiUpscalePanelProps {
  jobId: number;
  trimWidthMm: number;
  trimHeightMm: number;
  onApplied?: () => void;
  /** When AI artwork is under 300 DPI, turn enhancement on and accept it. The switch still works. */
  autoStart?: boolean;
  onEnhanceChoice?: (accepted: boolean) => void;
}

export function AiUpscalePanel({ jobId, trimWidthMm, trimHeightMm, onApplied, autoStart = false, onEnhanceChoice }: AiUpscalePanelProps) {
  const [enabled, setEnabled] = useState(false);
  const [phase, setPhase] = useState<AiUpscalePhase>("idle");
  const [assess, setAssess] = useState<AiUpscaleAssess | null>(null);
  const [preview, setPreview] = useState<AiUpscalePreview | null>(null);
  const [slider, setSlider] = useState(55);
  const userTouched = useRef(false);
  const autoStarted = useRef(false);
  const autoAccepted = useRef(false);
  const onEnhanceChoiceRef = useRef(onEnhanceChoice);
  onEnhanceChoiceRef.current = onEnhanceChoice;

  useEffect(() => {
    let cancel = false;
    const url = `/api/jobs/${jobId}/ai-upscale/assess?trimW=${encodeURIComponent(String(trimWidthMm))}&trimH=${encodeURIComponent(String(trimHeightMm))}`;
    fetch(url)
      .then((response) => response.json())
      .then((data) => {
        if (cancel || !data) return;
        setAssess({
          eligible: data.eligible,
          suggestion: data.suggestion,
          strong: data.strong,
          message: data.message,
          effectiveDpi: data.effective_dpi ?? data.effectiveDpi ?? null,
        });
        if (data.saved?.accepted && data.saved?.enhancedPath) {
          setEnabled(true);
          setPhase("accepted");
          setPreview({
            beforeUrl: `/api/jobs/${jobId}/ai-upscale/image?which=before`,
            afterUrl: `/api/jobs/${jobId}/ai-upscale/image?which=after`,
            basic: data.saved.provider === "basic",
            provider: data.saved.provider,
            message: data.saved.message,
          });
        } else if (data.saved && data.saved.accepted === false && !data.saved.note) {
          setEnabled(false);
          setPhase("kept");
          userTouched.current = true;
        }
      })
      .catch(() => {});
    return () => {
      cancel = true;
    };
  }, [jobId, trimWidthMm, trimHeightMm]);

  const saveDecision = async (accepted: boolean) => {
    await fetch(`/api/jobs/${jobId}/ai-upscale/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accepted }),
    });
  };

  const onToggle = async (next: boolean, fromAuto = false) => {
    if (!fromAuto) userTouched.current = true;
    if (!next) {
      setEnabled(false);
      setPhase("idle");
      setPreview(null);
      try {
        await saveDecision(false);
        onApplied?.();
      } catch {
        /* original artwork stays in place */
      }
      return;
    }
    setEnabled(true);
    setPhase("running");
    try {
      const response = await fetch(`/api/jobs/${jobId}/ai-upscale/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trimW: trimWidthMm, trimH: trimHeightMm }),
      });
      const data = await response.json();
      if (data?.used_original || !data?.beforeUrl || !data?.afterUrl) {
        setPreview({ message: data?.message || "The AI upscaler isn't available right now, so we'll keep your original artwork and continue.", provider: "original" });
        setPhase("fallback");
        return;
      }
      setPreview({
        beforeUrl: data.beforeUrl,
        afterUrl: data.afterUrl,
        basic: !!data.basic,
        provider: data.provider,
        message: data.message,
        effectiveDpi: data.effectiveDpi,
        enhancedDpi: data.enhancedDpi,
      });
      setPhase("ready");
    } catch {
      setPreview({ message: "The AI upscaler isn't available right now, so we'll keep your original artwork and continue.", provider: "original" });
      setPhase("fallback");
    }
  };

  useEffect(() => {
    if (!autoStart || userTouched.current || autoStarted.current) return;
    if (!assess || assess.eligible === false) return;
    if (phase !== "idle") return;
    autoStarted.current = true;
    void onToggle(true, true);
  }, [autoStart, assess, phase]);

  useEffect(() => {
    if (!autoStarted.current || userTouched.current || autoAccepted.current) return;
    if (phase !== "ready") return;
    autoAccepted.current = true;
    void (async () => {
      try {
        await saveDecision(true);
        await onEnhanceChoiceRef.current?.(true);
        setPhase("accepted");
        onApplied?.();
      } catch {
        setPhase("fallback");
      }
    })();
  }, [phase]);

  const onAccept = async () => {
    userTouched.current = true;
    try {
      await saveDecision(true);
      await onEnhanceChoiceRef.current?.(true);
      setPhase("accepted");
      onApplied?.();
    } catch {
      setPreview((current) => ({ ...(current || {}), message: "Couldn't save that choice. Your original artwork is still safe." }));
      setPhase("fallback");
    }
  };

  const onKeep = async () => {
    userTouched.current = true;
    try {
      await saveDecision(false);
      await onEnhanceChoiceRef.current?.(false);
      setPhase("kept");
      onApplied?.();
    } catch {
      setPhase("kept");
    }
  };

  return (
    <AiUpscalePanelView
      enabled={enabled}
      phase={phase}
      assess={assess}
      preview={preview}
      slider={slider}
      onToggle={(next) => { void onToggle(next); }}
      onSlider={setSlider}
      onAccept={() => { void onAccept(); }}
      onKeep={() => { void onKeep(); }}
    />
  );
}
