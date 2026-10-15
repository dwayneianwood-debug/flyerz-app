import { useEffect, useState, type ReactNode } from "react";
import { useReplicateSwitch } from "@/lib/replicate-switch";
import { QUICK_PRINT_DEFAULT_PRODUCT_ID, type QuickPrintCard } from "@shared/quickPrint";
import { fixRows } from "@shared/jobProgress";
import { messageFromStartResponse } from "@/lib/upload-reply";
import { PrintFlowView, type FixRow, type FlowStage, type FlowTab, type IntakeItem } from "./print-flow-view";

const EMPTY_CARD = (id: number, filename: string): QuickPrintCard => ({
  id,
  filename,
  status: "processing",
  light: null,
  reasons: [],
  decisions: [],
  clientMessage: "",
  approved: false,
  hasPress: false,
  hasProof: false,
  productLabel: "",
  quantity: null,
  notes: "",
  fixes: fixRows("fitting"),
});

export function PrintFlow(props: { tab: FlowTab; onTab: (tab: FlowTab) => void; manual: ReactNode }) {
  const [productId, setProductId] = useState(QUICK_PRINT_DEFAULT_PRODUCT_ID);
  const [customW, setCustomW] = useState("");
  const [customH, setCustomH] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stage, setStage] = useState<FlowStage>("upload");
  const [checkId, setCheckId] = useState("");
  const [items, setItems] = useState<IntakeItem[]>([]);
  const [acknowledged, setAcknowledged] = useState<Record<string, boolean>>({});
  const replicateSwitch = useReplicateSwitch();
  const replicateOff = replicateSwitch.enabled === false;
  const [replicate, setReplicate] = useState(false);
  const [cat, setCat] = useState(false);
  const [catText, setCatText] = useState("");
  const [upscale, setUpscale] = useState(false);
  const [card, setCard] = useState<QuickPrintCard | null>(null);
  const [bleedOpen, setBleedOpen] = useState(false);
  const [bleedBusy, setBleedBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (stage !== "progress" || !card?.id) return;
    let stop = false;
    let timer = 0;
    const tick = async () => {
      try {
        const res = await fetch(`/api/quick-print/${card.id}`, { credentials: "include" });
        const next = await res.json() as QuickPrintCard;
        if (stop) return;
        setCard(next);
        if (next.status === "pending" || next.status === "processing") {
          timer = window.setTimeout(() => void tick(), 1500);
        } else {
          setStage("result");
          setBusy(false);
        }
      } catch {
        if (!stop) timer = window.setTimeout(() => void tick(), 2500);
      }
    };
    void tick();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [stage, card?.id]);

  async function runCheck() {
    setError("");
    if (!file) {
      setError("Add a file to check.");
      return;
    }
    const body = new FormData();
    body.append("file", file, file.name);
    body.append("productId", productId);
    body.append("customWidth", customW);
    body.append("customHeight", customH);
    setBusy(true);
    try {
      const res = await fetch("/api/print-flow/check", { method: "POST", body, credentials: "include" });
      const raw = await res.text();
      if (!res.ok) {
        setBusy(false);
        setError(messageFromStartResponse(raw, false));
        return;
      }
      const payload = raw ? JSON.parse(raw) : {};
      setCheckId(String(payload.checkId || ""));
      setItems(Array.isArray(payload.items) ? payload.items : []);
      setAcknowledged({});
      setStage("check");
      setBusy(false);
    } catch {
      setBusy(false);
      setError("The check could not start. Please try the file again.");
    }
  }

  async function makeReady() {
    setError("");
    const concerns = items
      .filter((item) => item.severity === "amber" || (item.severity === "red" && acknowledged[item.id]))
      .map((item) => `${item.name}: ${item.detail}`.trim());
    setBusy(true);
    setStage("progress");
    setCard(EMPTY_CARD(0, file?.name || "Artwork"));
    try {
      const res = await fetch("/api/print-flow/make", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          checkId,
          productId,
          customWidth: customW,
          customHeight: customH,
          replicateEdit: replicate && !replicateOff,
          editRequest: cat ? catText : "",
          skipUpscale: !(upscale && !replicateOff),
          concerns,
        }),
      });
      const raw = await res.text();
      if (!res.ok) {
        setBusy(false);
        setStage("check");
        setError(messageFromStartResponse(raw, false));
        return;
      }
      const payload = raw ? JSON.parse(raw) : {};
      const job = (payload.jobs || [])[0];
      if (!job?.id) {
        setBusy(false);
        setStage("check");
        setError("The press file did not start.");
        return;
      }
      setCard(EMPTY_CARD(job.id, job.filename || file?.name || "Artwork"));
    } catch {
      setBusy(false);
      setStage("check");
      setError("The press file did not start. Please try again.");
    }
  }

  async function changeBleed(strategy: string) {
    if (!card?.id) return;
    setBleedBusy(true);
    setError("");
    try {
      const res = await fetch(`/api/quick-print/${card.id}/rebleed`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy }),
      });
      const payload = await res.json();
      if (!res.ok) {
        setError(payload.message || "The bleed could not be changed.");
      } else {
        setCard(payload as QuickPrintCard);
        setBleedOpen(false);
      }
    } catch {
      setError("The bleed could not be changed.");
    } finally {
      setBleedBusy(false);
    }
  }

  async function copyMessage() {
    const message = card?.customerMessage || card?.clientMessage || "";
    try {
      await navigator.clipboard.writeText(message);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  const fixes: FixRow[] = (card?.fixes && card.fixes.length ? card.fixes : fixRows(card?.stage ? "fitting" : "checking"));

  return (
    <div>
      {props.tab === "manual" ? props.manual : (
        <PrintFlowView
          stage={stage}
          productId={productId}
          customW={customW}
          customH={customH}
          fileLabel={file?.name || ""}
          busy={busy}
          items={items}
          acknowledged={acknowledged}
          replicate={replicate}
          cat={cat}
          catText={catText}
          upscale={upscale}
          replicateOff={replicateOff}
          fixes={fixes}
          card={card}
          bleedOpen={bleedOpen}
          bleedBusy={bleedBusy}
          copied={copied}
          error={error}
          onProduct={setProductId}
          onCustomW={setCustomW}
          onCustomH={setCustomH}
          onFiles={(list) => setFile(list && list[0] ? list[0] : null)}
          onCheck={() => void runCheck()}
          onAck={(id, checked) => setAcknowledged((current) => ({ ...current, [id]: checked }))}
          onReplicate={setReplicate}
          onCat={setCat}
          onCatText={setCatText}
          onUpscale={setUpscale}
          onMake={() => void makeReady()}
          onBleedOpen={() => setBleedOpen((open) => !open)}
          onBleed={(id) => void changeBleed(id)}
          onCopy={() => void copyMessage()}
        />
      )}
    </div>
  );
}
