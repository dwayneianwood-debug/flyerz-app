import React from "react";
import { QUICK_PRINT_PRODUCTS, type QuickPrintCard } from "@shared/quickPrint";
import { BLEED_METHOD_LABELS } from "@/components/bleed-method-selector";
import { BLEED_STRATEGY_IDS } from "@shared/schema";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { FlowDropzone } from "./flow-dropzone";
import { editOutcomes, isPictureFile } from "@/lib/edit-outcomes";

export type FlowTab = "ready" | "manual";
export type FlowStage = "upload" | "check" | "progress" | "result";

export interface IntakeItem {
  id: string;
  num: string;
  name: string;
  detail: string;
  severity: "red" | "amber" | "green";
}

export interface FixRow {
  id: string;
  label: string;
  percent: number;
}

const ROW = {
  green: "border-emerald-300 bg-emerald-50 text-emerald-950",
  amber: "border-amber-300 bg-amber-50 text-amber-950",
  red: "border-red-300 bg-red-50 text-red-950",
};

export const BLEED_CHOICES = [
  { id: "auto", label: BLEED_METHOD_LABELS.auto.label },
  ...BLEED_STRATEGY_IDS.map((id) => ({
    id,
    label: BLEED_METHOD_LABELS[id].label,
  })),
];

export function FlowTabs(props: { active: FlowTab; onReady: () => void; onManual: () => void }) {
  const tabClass = (on: boolean) =>
    `min-h-11 px-4 py-2 rounded-full text-sm font-bold ${on ? "bg-primary text-primary-foreground" : "bg-muted text-foreground"}`;
  return (
    <div className="flex flex-wrap gap-2 mb-5" role="tablist" data-testid="flow-tabs">
      <button type="button" role="tab" aria-selected={props.active === "ready"} className={tabClass(props.active === "ready")} data-testid="tab-print-ready" onClick={props.onReady}>
        Make file print ready
      </button>
      <button type="button" role="tab" aria-selected={props.active === "manual"} className={tabClass(props.active === "manual")} data-testid="tab-manual" onClick={props.onManual}>
        Fix my artwork manually
      </button>
    </div>
  );
}

export function PrintFlowView(props: {
  stage: FlowStage;
  productId: string;
  customW: string;
  customH: string;
  fileLabel: string;
  busy: boolean;
  items: IntakeItem[];
  acknowledged: Record<string, boolean>;
  replicate: boolean;
  cat: boolean;
  catText: string;
  upscale: boolean;
  fixes: FixRow[];
  card: QuickPrintCard | null;
  bleedOpen: boolean;
  bleedBusy: boolean;
  copied: boolean;
  error: string;
  onProduct: (id: string) => void;
  onCustomW: (value: string) => void;
  onCustomH: (value: string) => void;
  onFiles: (files: FileList | null) => void;
  onCheck: () => void;
  onAck: (id: string, checked: boolean) => void;
  onReplicate: (value: boolean) => void;
  onCat: (value: boolean) => void;
  onCatText: (value: string) => void;
  onUpscale: (value: boolean) => void;
  onMake: () => void;
  onBleedOpen: () => void;
  onBleed: (id: string) => void;
  onCopy: () => void;
}) {
  const openReds = props.items.filter((item) => item.severity === "red" && !props.acknowledged[item.id]);
  const makeDisabled = props.busy || openReds.length > 0;
  const pictureOnly = isPictureFile(props.fileLabel);
  return (
    <div data-testid="print-flow">
      {props.stage === "upload" && (
        <Card className="p-5 sm:p-6 border-primary/20" data-testid="card-quick-check-upload">
          <h1 className="text-3xl font-extrabold font-display tracking-tight">Quick check</h1>
          <p className="text-sm text-muted-foreground mt-2 mb-5">
            The full checklist runs first. Bleed stays 5 mm. Nothing is changed until you ask for the press file.
          </p>
          <label className="block text-sm font-semibold mb-2" htmlFor="flow-file">Artwork</label>
          <FlowDropzone onFiles={props.onFiles} fileLabel={props.fileLabel} />
          <label className="block text-sm font-semibold mb-2" htmlFor="flow-product">Product</label>
          <select
            id="flow-product"
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm mb-4"
            value={props.productId}
            data-testid="select-flow-product"
            onChange={(event) => props.onProduct(event.target.value)}
          >
            {QUICK_PRINT_PRODUCTS.map((product) => (
              <option key={product.id} value={product.id}>
                {product.label} ({product.widthMm} × {product.heightMm} mm)
              </option>
            ))}
            <option value="custom">Custom size</option>
          </select>
          {props.productId === "custom" && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
              <input aria-label="Custom width mm" className="rounded-md border border-border px-3 py-2 text-sm" value={props.customW} placeholder="Width mm" data-testid="input-flow-width" onChange={(event) => props.onCustomW(event.target.value)} />
              <input aria-label="Custom height mm" className="rounded-md border border-border px-3 py-2 text-sm" value={props.customH} placeholder="Height mm" data-testid="input-flow-height" onChange={(event) => props.onCustomH(event.target.value)} />
            </div>
          )}
          <Button type="button" className="w-full text-base font-bold min-h-12" disabled={props.busy} data-testid="button-run-quick-check" onClick={props.onCheck}>
            {props.busy ? "Checking the file…" : "Run quick check"}
          </Button>
        </Card>
      )}

      {props.stage === "check" && (
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_280px] gap-4">
          <Card className="p-5 border-border" data-testid="card-quick-check-results">
            <h1 className="text-2xl font-extrabold font-display">Quick check</h1>
            <p className="text-sm text-muted-foreground mt-1 mb-4">{props.fileLabel || "Artwork"}</p>
            <ul className="space-y-2" data-testid="list-intake-checks">
              {props.items.map((item) => (
                <li key={item.id} className={`rounded-md border px-3 py-2 text-sm ${ROW[item.severity]}`} data-testid={`check-row-${item.id}`}>
                  <div className="flex items-start gap-2">
                    {item.severity === "red" && (
                      <input
                        type="checkbox"
                        className="mt-1 h-5 w-5 shrink-0"
                        aria-label={`Accept ${item.name}`}
                        checked={props.acknowledged[item.id] === true}
                        data-testid={`ack-${item.id}`}
                        onChange={(event) => props.onAck(item.id, event.target.checked)}
                      />
                    )}
                    <div>
                      <p className="font-semibold">
                        {item.severity === "green" && "Pass"}
                        {item.severity === "amber" && "Strong recommendation"}
                        {item.severity === "red" && (props.acknowledged[item.id] ? "Accepted, now a recommendation" : "Stop")}
                        {" — "}
                        {item.num ? `${item.num}. ` : ""}
                        {item.name}
                      </p>
                      {item.detail && <p className="mt-1">{item.detail}</p>}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
            {openReds.length > 0 && (
              <p className="mt-3 text-sm font-semibold" data-testid="text-red-stops">
                Tick each red item to continue. The press file is not made until you do.
              </p>
            )}
          </Card>
          <Card className="p-4 border-primary/30 h-fit" data-testid="panel-pre-fix">
            <h2 className="text-lg font-bold font-display">Before the fix</h2>
            <p className="text-xs text-muted-foreground mt-1 mb-3">These run once, then the press file is made.</p>
            <label className="flex items-start gap-2 text-sm mb-3">
              <input type="checkbox" className="mt-1 h-5 w-5" checked={props.replicate} data-testid="option-replicate" onChange={(event) => props.onReplicate(event.target.checked)} />
              <span>AI enlarge (pictures only)</span>
            </label>
            <label className="flex items-start gap-2 text-sm mb-2">
              <input type="checkbox" className="mt-1 h-5 w-5" checked={props.cat && !pictureOnly} disabled={pictureOnly} data-testid="option-cat" onChange={(event) => props.onCat(event.target.checked)} />
              <span className={pictureOnly ? "text-muted-foreground" : ""}>Small time, date, or number edit</span>
            </label>
            <p className={`text-xs mb-2 ${pictureOnly ? "text-amber-700" : "text-muted-foreground"}`} data-testid="text-cat-hint">
              {pictureOnly
                ? "Not available: this is a picture file, so the text is part of the picture. Picture-only files need the editable file."
                : "Only works on PDFs with editable text. Picture-only files need the editable file."}
            </p>
            {props.cat && !pictureOnly && (
              <input
                aria-label="Time date or number edit"
                className="w-full rounded-md border border-border px-3 py-2 text-sm mb-3"
                placeholder="Change the date to 12 October"
                value={props.catText}
                data-testid="input-cat-edit"
                onChange={(event) => props.onCatText(event.target.value)}
              />
            )}
            <label className="flex items-start gap-2 text-sm mb-4">
              <input type="checkbox" className="mt-1 h-5 w-5" checked={props.upscale} data-testid="option-upscale" onChange={(event) => props.onUpscale(event.target.checked)} />
              <span>Upscale with AI</span>
            </label>
            <Button type="button" className="w-full font-bold min-h-12" disabled={makeDisabled} data-testid="button-make-print-ready" onClick={props.onMake}>
              Make file print ready
            </Button>
          </Card>
        </div>
      )}

      {props.stage === "progress" && (
        <Card className="p-5 border-border" data-testid="card-fix-progress">
          <h1 className="text-2xl font-extrabold font-display">Making the press file</h1>
          <p className="text-sm text-muted-foreground mt-1 mb-4">{props.card?.stage || "Working through each fix."}</p>
          <ul className="space-y-2" data-testid="list-fix-progress">
            {props.fixes.map((fix) => (
              <li key={fix.id} className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-sm" data-testid={`fix-${fix.id}`}>
                <span className="font-semibold">{fix.label}</span>
                <span data-testid={`fix-percent-${fix.id}`}>{fix.percent}%</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {props.stage === "result" && props.card && (
        <ResultScreen card={props.card} bleedOpen={props.bleedOpen} bleedBusy={props.bleedBusy} copied={props.copied} onBleedOpen={props.onBleedOpen} onBleed={props.onBleed} onCopy={props.onCopy} />
      )}
      {props.error && <p className="mt-3 text-sm text-red-700" data-testid="text-flow-error">{props.error}</p>}
    </div>
  );
}

function ResultScreen(props: {
  card: QuickPrintCard;
  bleedOpen: boolean;
  bleedBusy: boolean;
  copied: boolean;
  onBleedOpen: () => void;
  onBleed: (id: string) => void;
  onCopy: () => void;
}) {
  const { card } = props;
  const revision = card.bleedRevision || 0;
  const message = card.customerMessage || card.clientMessage || "";
  const bleed = card.bleedNote || card.decisions.find((line) => line.startsWith("Bleed:")) || "";
  const outcomes = editOutcomes(card.decisions);
  return (
    <Card className="p-5 border-2 border-primary/30" data-testid="card-print-result">
      <h1 className="text-2xl font-extrabold font-display">Print-ready artwork</h1>
      <p className="text-sm mt-1">{card.filename}{card.productLabel ? ` · ${card.productLabel}` : ""}</p>
      {outcomes.length > 0 && (
        <ul className="mt-3 space-y-2" data-testid="list-edit-outcomes">
          {outcomes.map((line, index) => (
            <li
              key={index}
              className={`rounded-md border px-3 py-2 text-sm font-semibold ${line.tone === "warn" ? "border-amber-300 bg-amber-50 text-amber-950" : "border-emerald-300 bg-emerald-50 text-emerald-950"}`}
              data-testid={`edit-outcome-${line.tone}`}
            >
              {line.tone === "warn" ? "Not applied: " : "Done: "}{line.text}
            </li>
          ))}
        </ul>
      )}
      {bleed && <p className="mt-3 text-sm font-semibold" data-testid="text-result-bleed-note">{bleed}</p>}
      <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
        <figure>
          <figcaption className="text-xs font-bold tracking-wide mb-1">Before</figcaption>
          {card.hasBefore ? (
            <img src={`/api/quick-print/${card.id}/before?v=${revision}`} alt="Artwork before the press file" className="w-full rounded-md border bg-white" data-testid="img-before" />
          ) : (
            <p className="text-sm text-muted-foreground" data-testid="text-before-missing">The original preview will show here when the picture is ready.</p>
          )}
        </figure>
        <figure>
          <figcaption className="text-xs font-bold tracking-wide mb-1">After</figcaption>
          {card.hasProof ? (
            <img src={`/api/quick-print/${card.id}/proof?v=${revision}`} alt="Press file after the fixes" className="w-full rounded-md border bg-white" data-testid="img-after" />
          ) : (
            <p className="text-sm" data-testid="text-after-missing">The press preview is not available for this file.</p>
          )}
        </figure>
      </div>
      {card.hasProof && (
        <figure className="mt-4" data-testid="cutter-preview">
          <figcaption className="text-sm font-semibold mb-2">Cutter preview</figcaption>
          <img src={`/api/quick-print/${card.id}/proof?v=${revision}`} alt="Cutter preview. The pink line is the trim." className="w-full max-w-md rounded-md border bg-white" data-testid="img-cutter" />
          <p className="text-sm mt-2">The pink line is the trim. The area outside that line is the 5 mm bleed.</p>
        </figure>
      )}
      <div className="mt-4" data-testid="change-bleed">
        <button type="button" className="min-h-11 rounded-md border border-border px-3 py-2 text-sm font-semibold" data-testid="button-change-bleed" onClick={props.onBleedOpen}>
          {props.bleedBusy ? "Changing the bleed…" : "Change bleed type"}
        </button>
        {props.bleedOpen && (
          <ul className="mt-2 rounded-md border border-border bg-background p-1" role="listbox" data-testid="menu-change-bleed">
            {BLEED_CHOICES.map((choice) => (
              <li key={choice.id}>
                <button type="button" className="w-full text-left px-3 py-2 text-sm hover:bg-muted min-h-11" data-testid={`bleed-method-${choice.id}`} onClick={() => props.onBleed(choice.id)}>
                  {choice.label}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {card.hasPress && (
        <a className="mt-4 inline-flex" href={`/api/jobs/${card.id}/download/press-ready`}>
          <Button type="button" className="font-bold min-h-12" data-testid="link-proceed-download">Proceed to download print-ready artwork</Button>
        </a>
      )}
      <div className="mt-5">
        <p className="text-sm font-semibold mb-2">Message for the customer</p>
        <textarea readOnly className="w-full min-h-32 rounded-md border border-border bg-white p-3 text-sm" value={message} data-testid="text-customer-message" />
        <Button type="button" variant="outline" className="mt-2 min-h-11" data-testid="button-copy-customer" onClick={props.onCopy}>
          {props.copied ? "Copied" : "Copy message"}
        </Button>
      </div>
    </Card>
  );
}
