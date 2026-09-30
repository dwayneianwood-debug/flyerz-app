import React, { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import { Switch } from "@/components/ui/switch";

export interface AiRebuildBlock {
  id: string;
  text: string;
  color_hex?: string;
  bold?: boolean;
}

export interface AiRebuildStep {
  name: string;
  engine: string;
  note?: string;
  ok?: boolean;
}

export interface AiRebuildPlan {
  detected?: boolean;
  skipped?: boolean;
  refused?: boolean;
  accepted?: boolean;
  reasons?: string[];
  recommendation?: string;
  blocks?: AiRebuildBlock[];
  steps?: AiRebuildStep[];
  ocrText?: string;
  message?: string;
  beforeUrl?: string;
  afterUrl?: string;
  ready?: boolean;
}

const ENGINE_LABEL: Record<string, string> = {
  local: "on this computer",
  replicate: "Replicate",
  gemini: "Gemini",
  review: "staff edit",
  hook: "on this computer",
  none: "skipped",
};

export function AiRebuildPanelView({
  plan,
  busy,
  onToggle,
  onText,
  onSave,
  onPreview,
  onApprove,
}: {
  plan: AiRebuildPlan;
  busy?: boolean;
  onToggle: (enabled: boolean) => void;
  onText: (id: string, text: string) => void;
  onSave: () => void;
  onPreview?: () => void;
  onApprove?: () => void;
}) {
  const enabled = !plan.skipped;
  return (
    <div className="rounded-xl border border-violet-500/40 bg-violet-50/60 dark:bg-violet-500/10 p-4 mb-4" data-testid="section-ai-rebuild">
      <div className="flex items-start gap-3">
        <div className="w-8 h-8 rounded-full bg-violet-500/15 text-violet-700 dark:text-violet-200 flex items-center justify-center shrink-0">
          <Sparkles className="w-4 h-4" />
        </div>
        <div className="flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-base font-bold text-foreground" data-testid="text-ai-rebuild-title">Original lettering</h3>
            <span className="text-[11px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full bg-violet-600 text-white" data-testid="badge-ai-rebuild">
              Words stay as drawn
            </span>
          </div>
          <p className="text-sm text-muted-foreground mt-1" data-testid="text-ai-rebuild-recommendation">
            {plan.recommendation || "The picture is enlarged and the original lettering is kept. Retyping is optional and has to be approved."}
          </p>
        </div>
      </div>
      <div className="mt-3">
        <button
          type="button"
          onClick={onPreview}
          disabled={busy}
          className="text-sm font-semibold px-3 py-2 rounded-md bg-violet-600 text-white disabled:opacity-50"
          data-testid="button-rebuild-text"
        >
          {busy ? "Checking the lettering…" : "Rebuild text as sharp type"}
        </button>
        <p className="mt-1 text-xs text-muted-foreground">Optional. You will see a before and after, and nothing changes until you approve it.</p>
      </div>

      {plan.reasons && plan.reasons.length > 0 ? (
        <p className="mt-3 text-xs text-muted-foreground" data-testid="text-ai-rebuild-reasons">{plan.reasons.join(" · ")}</p>
      ) : null}

      {plan.ready && !plan.refused ? (
        <div className="mt-3">
          <button
            type="button"
            onClick={onApprove}
            disabled={busy || plan.accepted}
            className="text-sm font-semibold px-3 py-2 rounded-md border border-violet-600 text-violet-800 disabled:opacity-50"
            data-testid="button-approve-rebuild"
          >
            {plan.accepted ? "Approved for the press file" : "Approve this rebuild"}
          </button>
        </div>
      ) : null}

      <div className="mt-3 flex items-center justify-between gap-3 rounded-lg bg-white/70 dark:bg-background/40 px-3 py-2">
        <div>
          <p className="text-sm font-medium">Keep the original lettering</p>
          <p className="text-xs text-muted-foreground">On by default. Turn this off only after you have approved a rebuild.</p>
        </div>
        <Switch checked={enabled && !plan.accepted} onCheckedChange={onToggle} disabled={busy} data-testid="switch-ai-rebuild" />
      </div>

      {plan.blocks && plan.blocks.length > 0 ? (
        <div className="mt-3 space-y-2" data-testid="ai-rebuild-review">
          <p className="text-xs font-semibold text-foreground">Check the spelling. The job can also finish with these words as they were read.</p>
          {plan.blocks.map((block) => (
            <input
              key={block.id}
              value={block.text}
              onChange={(event) => onText(block.id, event.target.value)}
              className="w-full rounded-md border border-border bg-background px-2 py-1 text-sm"
              data-testid={`input-ai-rebuild-${block.id}`}
            />
          ))}
          <button
            type="button"
            onClick={onSave}
            disabled={busy || !enabled}
            className="text-xs font-semibold px-3 py-1.5 rounded-md bg-violet-600 text-white disabled:opacity-50"
            data-testid="button-ai-rebuild-save"
          >
            {busy ? "Rebuilding…" : "Save spelling and rebuild"}
          </button>
        </div>
      ) : null}

      {plan.steps && plan.steps.length > 0 ? (
        <ul className="mt-3 space-y-1 text-xs text-muted-foreground" data-testid="list-ai-rebuild-steps">
          {plan.steps.map((step) => (
            <li key={step.name}>
              <span className="font-semibold text-foreground">{step.name}</span>
              {" · "}
              {ENGINE_LABEL[step.engine] || step.engine}
              {step.note ? ` — ${step.note}` : ""}
            </li>
          ))}
        </ul>
      ) : null}

      {plan.ocrText ? (
        <p className="mt-2 text-xs" data-testid="text-ai-rebuild-ocr">Text found: {plan.ocrText}</p>
      ) : null}

      {(plan.beforeUrl || plan.afterUrl) ? (
        <div className="mt-3 grid grid-cols-2 gap-2" data-testid="ai-rebuild-thumbs">
          {plan.beforeUrl ? (
            <figure>
              <img src={plan.beforeUrl} alt="Artwork before AI Rebuild" className="w-full rounded-md border border-border" />
              <figcaption className="text-[11px] text-muted-foreground mt-1">Before</figcaption>
            </figure>
          ) : null}
          {plan.afterUrl ? (
            <figure>
              <img src={plan.afterUrl} alt="Artwork after AI Rebuild" className="w-full rounded-md border border-border" />
              <figcaption className="text-[11px] text-muted-foreground mt-1">After</figcaption>
            </figure>
          ) : null}
        </div>
      ) : null}

      {plan.message ? <p className="mt-2 text-xs text-muted-foreground" data-testid="text-ai-rebuild-message">{plan.message}</p> : null}
    </div>
  );
}

export function AiRebuildPanel({ jobId, trimWidthMm, trimHeightMm }: { jobId: number; trimWidthMm: number; trimHeightMm: number }) {
  const [plan, setPlan] = useState<AiRebuildPlan | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancel = false;
    const load = async () => {
      const response = await fetch(`/api/jobs/${jobId}/ai-rebuild?trimW=${trimWidthMm}&trimH=${trimHeightMm}`);
      const data = await response.json();
      if (cancel) return;
      setPlan(data?.detected ? data : null);
    };
    load().catch(() => {
      if (!cancel) setPlan(null);
    });
    return () => {
      cancel = true;
    };
  }, [jobId, trimWidthMm, trimHeightMm]);

  if (!plan?.detected) return null;

  const send = async (body: Record<string, unknown>) => {
    setBusy(true);
    try {
      const response = await fetch(`/api/jobs/${jobId}/ai-rebuild/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trimW: trimWidthMm, trimH: trimHeightMm, ...body }),
      });
      const data = await response.json();
      if (data) setPlan(data.detected ? data : { ...plan, ...data });
    } finally {
      setBusy(false);
    }
  };

  const preview = async () => {
    setBusy(true);
    try {
      const response = await fetch(`/api/jobs/${jobId}/ai-rebuild/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trimW: trimWidthMm, trimH: trimHeightMm, optIn: true }),
      });
      const data = await response.json();
      if (data) setPlan(data.detected ? data : { ...(plan || {}), ...data, detected: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <AiRebuildPanelView
      plan={plan}
      busy={busy}
      onPreview={() => { void preview(); }}
      onApprove={() => { void send({ accept: true, optIn: true }); }}
      onToggle={(enabled) => {
        setPlan({ ...plan, skipped: !enabled });
        void send({ skipped: !enabled, blocks: plan.blocks });
      }}
      onText={(id, text) => {
        setPlan({
          ...plan,
          blocks: (plan.blocks || []).map((block) => (block.id === id ? { ...block, text } : block)),
        });
      }}
      onSave={() => void send({ skipped: false, blocks: plan.blocks })}
    />
  );
}
