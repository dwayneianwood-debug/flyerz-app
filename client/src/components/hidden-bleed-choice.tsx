import { Card } from "@/components/ui/card";

export type HiddenBleedChoiceValue = "use" | "ignore";

export interface HiddenBleedFinding {
  found: boolean;
  recoverable: boolean;
  perSideMm: { top: number; bottom: number; left: number; right: number };
  reason?: string;
  message?: string;
  previews?: { use?: string; ignore?: string };
}

const fmt = (v: number) => `${Math.round(v * 10) / 10} mm`;

/** Staff choice shown only when the quick check found recoverable hidden bleed (FLYERZ_HIDDEN_BLEED=1). */
export function HiddenBleedChoice({
  finding,
  value,
  onChange,
}: {
  finding?: HiddenBleedFinding | null;
  value: HiddenBleedChoiceValue;
  onChange: (value: HiddenBleedChoiceValue) => void;
}) {
  if (!finding?.recoverable) return null;
  const p = finding.perSideMm;
  const options: { id: HiddenBleedChoiceValue; title: string; body: string; img?: string }[] = [
    {
      id: "use",
      title: "Use the hidden bleed",
      body: "The page grows 5 mm on every side onto artwork that's already in the file. Nothing is redrawn.",
      img: finding.previews?.use,
    },
    {
      id: "ignore",
      title: "Ignore it and use my bleed options",
      body: "Throw the hidden part away and add bleed the normal way.",
      img: finding.previews?.ignore,
    },
  ];
  return (
    <Card className="p-4 border border-sky-200 bg-sky-50/60 dark:bg-sky-950/20" data-testid="card-hidden-bleed">
      <p className="text-sm font-semibold text-foreground">{finding.message || "Hidden bleed found"}</p>
      <p className="text-xs text-muted-foreground mt-1" data-testid="text-hidden-bleed-sides">
        Artwork past the page edge: top {fmt(p.top)}, bottom {fmt(p.bottom)}, left {fmt(p.left)}, right {fmt(p.right)}.
      </p>
      <div className="grid grid-cols-2 gap-3 mt-3" role="radiogroup" aria-label="Hidden bleed">
        {options.map((o) => (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={value === o.id}
            onClick={() => onChange(o.id)}
            data-testid={`button-hidden-bleed-${o.id}`}
            className={`text-left rounded-lg border p-2 transition ${value === o.id ? "border-sky-500 ring-2 ring-sky-300 bg-white dark:bg-background" : "border-border/60 bg-white/60 dark:bg-background/60"}`}
          >
            {o.img && <img src={o.img} alt={`${o.title} edge preview with trim line`} className="w-full h-auto rounded mb-2" />}
            <span className="block text-xs font-semibold text-foreground">
              {o.title}
              {o.id === "use" && <span className="ml-1 font-normal text-muted-foreground">(recommended)</span>}
            </span>
            <span className="block text-[11px] text-muted-foreground mt-0.5">{o.body}</span>
          </button>
        ))}
      </div>
    </Card>
  );
}
