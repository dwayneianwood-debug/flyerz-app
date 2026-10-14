import { useState } from "react";
import { ChevronDown } from "lucide-react";
import type { QuickPrintCard } from "@shared/quickPrint";
import { QuickResultCard } from "./print-ready-view";

const DOT: Record<string, string> = {
  green: "bg-emerald-500",
  amber: "bg-amber-400",
  red: "bg-red-500",
};

function sizeLabel(card: QuickPrintCard): string {
  if (card.productLabel) return card.productLabel;
  if (card.trimWidthMm && card.trimHeightMm) return `${card.trimWidthMm} × ${card.trimHeightMm} mm`;
  return "";
}

export function DoneJobRow(props: {
  card: QuickPrintCard;
  copied?: boolean;
  onApprove?: (id: number) => void;
  onCopy?: (id: number, message: string) => void;
}) {
  const { card } = props;
  const [open, setOpen] = useState(false);
  const light = card.light || "";
  const status = light ? light.toUpperCase() : (card.stage || card.status || "").toString();
  const size = sizeLabel(card);
  return (
    <div className="rounded-xl border border-border bg-background" data-testid={`done-job-${card.id}`}>
      <button
        type="button"
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-muted/50 rounded-xl"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        data-testid={`button-done-toggle-${card.id}`}
      >
        <span className={`w-3 h-3 rounded-full shrink-0 ${DOT[light] || "bg-slate-300"}`} aria-hidden />
        <span className="font-semibold truncate flex-1 min-w-0" title={card.filename}>{card.filename}</span>
        {size && <span className="text-sm text-muted-foreground shrink-0 hidden sm:inline">{size}</span>}
        <span className="text-xs font-bold tracking-[0.15em] shrink-0" data-testid={`text-done-status-${card.id}`}>{status}</span>
        <span className="text-xs text-muted-foreground shrink-0">#{card.id}</span>
        <ChevronDown className={`w-4 h-4 shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="px-2 pb-2" data-testid={`done-job-body-${card.id}`}>
          <QuickResultCard card={card} copied={props.copied} onApprove={props.onApprove} onCopy={props.onCopy} />
        </div>
      )}
    </div>
  );
}

export function DoneJobsList(props: {
  cards: QuickPrintCard[];
  copiedId: number | null;
  onApprove: (id: number) => void;
  onCopy: (id: number, message: string) => void;
}) {
  if (!props.cards.length) return null;
  return (
    <section className="mt-8" data-testid="section-done-jobs">
      <h2 className="text-lg font-bold font-display mb-3" data-testid="text-done-heading">Fixed artwork done</h2>
      <div className="space-y-2">
        {props.cards.map((card) => (
          <DoneJobRow
            key={card.id}
            card={card}
            copied={props.copiedId === card.id}
            onApprove={props.onApprove}
            onCopy={props.onCopy}
          />
        ))}
      </div>
    </section>
  );
}
