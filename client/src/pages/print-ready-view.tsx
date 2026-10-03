import React from "react";
import { QUICK_PRINT_PRODUCTS, type QuickPrintCard } from "@shared/quickPrint";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const LIGHT_STYLE = {
  green: "bg-emerald-50 border-emerald-300 text-emerald-950",
  amber: "bg-amber-50 border-amber-300 text-amber-950",
  red: "bg-red-50 border-red-300 text-red-950",
};

export function QuickPrintForm(props: {
  productId: string;
  customW: string;
  customH: string;
  quantity: string;
  notes: string;
  fileLabel: string;
  busy: boolean;
  onProduct: (id: string) => void;
  onCustomW: (value: string) => void;
  onCustomH: (value: string) => void;
  onQuantity: (value: string) => void;
  onNotes: (value: string) => void;
  onFiles: (files: FileList | null) => void;
  onSubmit: () => void;
}) {
  return (
    <Card className="p-5 sm:p-6 border-primary/20" data-testid="quick-print-form">
      <h1 className="text-3xl font-extrabold font-display tracking-tight">Make it print-ready</h1>
      <p className="text-sm text-muted-foreground mt-2 mb-5">
        Drop in the client's file, pick the product, and press the button. Bleed is 5 mm. The press file is made without any extra questions.
      </p>
      <label className="block text-sm font-semibold mb-2" htmlFor="quick-files">Artwork</label>
      <input
        id="quick-files"
        type="file"
        multiple
        accept=".pdf,.jpg,.jpeg,.png,.ai,.eps,.docx,.pptx"
        className="block w-full text-sm mb-4"
        data-testid="input-quick-files"
        onChange={(event) => props.onFiles(event.target.files)}
      />
      {props.fileLabel && <p className="text-sm mb-4" data-testid="text-quick-files">{props.fileLabel}</p>}
      <label className="block text-sm font-semibold mb-2" htmlFor="quick-product">Product</label>
      <select
        id="quick-product"
        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm mb-4"
        value={props.productId}
        data-testid="select-product"
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
        <div className="grid grid-cols-2 gap-3 mb-4">
          <input
            aria-label="Custom width mm"
            className="rounded-md border border-border px-3 py-2 text-sm"
            value={props.customW}
            placeholder="Width mm"
            data-testid="input-custom-width"
            onChange={(event) => props.onCustomW(event.target.value)}
          />
          <input
            aria-label="Custom height mm"
            className="rounded-md border border-border px-3 py-2 text-sm"
            value={props.customH}
            placeholder="Height mm"
            data-testid="input-custom-height"
            onChange={(event) => props.onCustomH(event.target.value)}
          />
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
        <input
          aria-label="Quantity"
          className="rounded-md border border-border px-3 py-2 text-sm"
          value={props.quantity}
          placeholder="Quantity (optional)"
          data-testid="input-quantity"
          onChange={(event) => props.onQuantity(event.target.value)}
        />
        <input
          aria-label="Notes"
          className="rounded-md border border-border px-3 py-2 text-sm"
          value={props.notes}
          placeholder="Notes (optional)"
          data-testid="input-notes"
          onChange={(event) => props.onNotes(event.target.value)}
        />
      </div>
      <Button
        type="button"
        className="w-full text-base font-bold h-12"
        disabled={props.busy}
        data-testid="button-make-print-ready"
        onClick={props.onSubmit}
      >
        {props.busy ? "Making the press file…" : "Make it print-ready"}
      </Button>
    </Card>
  );
}

export function QuickResultCard(props: {
  card: QuickPrintCard;
  onApprove?: (id: number) => void;
  onCopy?: (id: number, message: string) => void;
  copied?: boolean;
}) {
  const { card } = props;
  const light = card.light;
  if (!light || card.status === "pending" || card.status === "processing") {
    return (
      <Card className="p-5 border-border" data-testid={`card-quick-result-${card.id}`}>
        <p className="font-semibold">{card.filename}</p>
        <p className="text-sm text-muted-foreground mt-1" data-testid={`text-quick-stage-${card.id}`}>
          {card.stage || "Making the press file…"}
          {card.elapsedSec != null ? ` · ${card.elapsedSec}s` : ""}
        </p>
        {card.note ? <p className="text-xs text-muted-foreground mt-1">{card.note}</p> : null}
      </Card>
    );
  }
  return (
    <Card className={`p-5 border-2 ${LIGHT_STYLE[light]}`} data-testid={`card-quick-result-${card.id}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-bold tracking-[0.2em]" data-testid={`text-quick-light-${card.id}`}>{light.toUpperCase()}</p>
          <h2 className="text-xl font-bold font-display mt-1">{card.filename}</h2>
          {card.productLabel && <p className="text-sm mt-1">{card.productLabel}{card.quantity ? ` · qty ${card.quantity}` : ""}</p>}
        </div>
      </div>
      {light !== "red" && card.hasProof && (
        <img
          src={`/api/quick-print/${card.id}/proof`}
          alt={`Proof of ${card.filename}`}
          className="mt-4 w-full max-w-md rounded-md border border-black/10 bg-white"
          data-testid={`img-proof-${card.id}`}
        />
      )}
      {light === "green" && (
        <p className="mt-3 text-sm">Print-ready. Download the press PDF.</p>
      )}
      {card.checklist && card.checklist.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm" data-testid={`list-checklist-${card.id}`}>
          {card.checklist.map((item) => (
            <li key={item.id} data-testid={`check-${item.id}-${card.id}`}>
              <span className="font-semibold">{item.passed ? "Pass" : "Fail"}</span>
              {" — "}
              {item.label}
              {item.detail ? ` — ${item.detail}` : ""}
            </li>
          ))}
        </ul>
      )}
      {light === "amber" && (
        <div className="mt-3">
          <p className="text-sm font-semibold">A press file was made. Glance at this before it goes to print:</p>
          <ul className="mt-2 list-disc pl-5 text-sm">
            {card.reasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        </div>
      )}
      {light === "red" && (
        <div className="mt-3">
          <p className="text-sm font-semibold mb-2">Send this to the client:</p>
          <textarea
            readOnly
            className="w-full min-h-28 rounded-md border border-red-200 bg-white p-3 text-sm text-foreground"
            value={card.clientMessage}
            data-testid={`text-client-message-${card.id}`}
          />
          <Button
            type="button"
            variant="outline"
            className="mt-2"
            data-testid={`button-copy-client-${card.id}`}
            onClick={() => props.onCopy?.(card.id, card.clientMessage)}
          >
            {props.copied ? "Copied" : "Copy message for the client"}
          </Button>
        </div>
      )}
      {card.decisions.length > 0 && (
        <ul className="mt-4 text-xs space-y-1 opacity-80" data-testid={`list-decisions-${card.id}`}>
          {card.decisions.map((line) => <li key={line}>{line}</li>)}
        </ul>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        {light !== "red" && card.hasPress && (
          <a href={`/api/jobs/${card.id}/download/press-ready`}>
            <Button type="button" className="font-bold" data-testid={`link-download-press-${card.id}`}>Download press PDF</Button>
          </a>
        )}
        {light !== "red" && card.hasProof && (
          <a href={`/api/quick-print/${card.id}/proof?kind=pdf`}>
            <Button type="button" variant="outline" data-testid={`link-download-proof-${card.id}`}>Download client proof</Button>
          </a>
        )}
        {light === "amber" && !card.approved && (
          <Button type="button" variant="secondary" data-testid={`button-approve-${card.id}`} onClick={() => props.onApprove?.(card.id)}>
            Looks fine
          </Button>
        )}
        {card.approved && <span className="text-sm font-semibold self-center">Approved</span>}
      </div>
    </Card>
  );
}

export function WatcherSettings(props: {
  enabled: boolean;
  folderPath: string;
  defaultProductId: string;
  onEnabled: (value: boolean) => void;
  onFolder: (value: string) => void;
  onProduct: (value: string) => void;
  onSave: () => void;
}) {
  return (
    <details className="mt-8 rounded-xl border border-border p-4" data-testid="quick-print-watcher">
      <summary className="cursor-pointer font-semibold">Drop folder (optional, off by default)</summary>
      <p className="text-sm text-muted-foreground mt-2">
        Files placed in an Input folder are made print-ready on their own. Finished files go to Output. Amber and red files go to Needs-attention.
        This uses a normal folder on this computer. It does not need administrator rights, including on Windows.
      </p>
      <label className="mt-3 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={props.enabled}
          data-testid="watcher-enabled"
          onChange={(event) => props.onEnabled(event.target.checked)}
        />
        Watch a folder
      </label>
      <input
        aria-label="Drop folder path"
        className="mt-3 w-full rounded-md border border-border px-3 py-2 text-sm"
        placeholder="C:\\Flyerz\\PrintReady"
        value={props.folderPath}
        data-testid="watcher-folder"
        onChange={(event) => props.onFolder(event.target.value)}
      />
      <label className="block text-sm font-semibold mt-3 mb-1" htmlFor="watcher-product">Default product when the file has no trim size</label>
      <select
        id="watcher-product"
        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
        value={props.defaultProductId}
        data-testid="watcher-product"
        onChange={(event) => props.onProduct(event.target.value)}
      >
        {QUICK_PRINT_PRODUCTS.map((product) => (
          <option key={product.id} value={product.id}>{product.label}</option>
        ))}
      </select>
      <Button type="button" className="mt-3" variant="outline" data-testid="button-save-watcher" onClick={props.onSave}>
        Save drop folder
      </Button>
    </details>
  );
}
