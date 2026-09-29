import { useEffect, useState } from "react";
import { Layout } from "@/components/layout";
import { QuickPrintForm, QuickResultCard, WatcherSettings } from "./print-ready-view";
import type { QuickPrintCard } from "@shared/quickPrint";
import { QUICK_PRINT_DEFAULT_PRODUCT_ID } from "@shared/quickPrint";
import { useQueryClient } from "@tanstack/react-query";

function mergeCards(current: QuickPrintCard[], incoming: QuickPrintCard[]): QuickPrintCard[] {
  const map = new Map(current.map((card) => [card.id, card]));
  for (const card of incoming) map.set(card.id, card);
  return Array.from(map.values()).sort((a, b) => b.id - a.id);
}

export default function PrintReady() {
  const queryClient = useQueryClient();
  const [productId, setProductId] = useState(QUICK_PRINT_DEFAULT_PRODUCT_ID);
  const [customW, setCustomW] = useState("");
  const [customH, setCustomH] = useState("");
  const [quantity, setQuantity] = useState("");
  const [notes, setNotes] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [cards, setCards] = useState<QuickPrintCard[]>([]);
  const [activeIds, setActiveIds] = useState<number[]>([]);
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [watcherEnabled, setWatcherEnabled] = useState(false);
  const [folderPath, setFolderPath] = useState("");
  const [watcherProduct, setWatcherProduct] = useState(QUICK_PRINT_DEFAULT_PRODUCT_ID);

  useEffect(() => {
    let stop = false;
    fetch("/api/quick-print/recent", { credentials: "include" })
      .then((res) => res.json())
      .then((body) => {
        if (!stop && Array.isArray(body.jobs)) setCards((current) => mergeCards(current, body.jobs));
      })
      .catch(() => undefined);
    fetch("/api/quick-print/settings", { credentials: "include" })
      .then((res) => res.json())
      .then((body) => {
        if (stop || !body) return;
        setWatcherEnabled(body.enabled === true);
        setFolderPath(typeof body.folderPath === "string" ? body.folderPath : "");
        if (typeof body.defaultProductId === "string") setWatcherProduct(body.defaultProductId);
      })
      .catch(() => undefined);
    return () => {
      stop = true;
    };
  }, []);

  useEffect(() => {
    if (!activeIds.length) return;
    let stop = false;
    let timer = 0;
    const tick = async () => {
      try {
        const incoming = await Promise.all(activeIds.map(async (id) => {
          const res = await fetch(`/api/quick-print/${id}`, { credentials: "include" });
          return res.json() as Promise<QuickPrintCard>;
        }));
        if (stop) return;
        setCards((current) => mergeCards(current, incoming));
        const stillWorking = incoming.some((card) => card.status === "pending" || card.status === "processing");
        if (stillWorking) timer = window.setTimeout(() => void tick(), 2000);
        else setBusy(false);
      } catch {
        if (!stop) timer = window.setTimeout(() => void tick(), 3000);
      }
    };
    void tick();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [activeIds]);

  const fileLabel = files.length ? files.map((file) => file.name).join(", ") : "";

  async function submit() {
    setError("");
    if (!files.length) {
      setError("Add at least one file.");
      return;
    }
    const body = new FormData();
    for (const file of files) body.append("files", file, file.name);
    body.append("productId", productId);
    body.append("customWidth", customW);
    body.append("customHeight", customH);
    body.append("quantity", quantity);
    body.append("notes", notes);
    setBusy(true);
    try {
      const res = await fetch("/api/quick-print", { method: "POST", body, credentials: "include" });
      const payload = await res.json();
      if (!res.ok) {
        setBusy(false);
        setError(payload.message || "Could not start.");
        return;
      }
      const ids = (payload.jobs || []).map((job: { id: number }) => job.id);
      setActiveIds(ids);
      setCards((current) => mergeCards(current, (payload.jobs || []).map((job: { id: number; filename: string }) => ({
        id: job.id,
        filename: job.filename,
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
      }))));
      setFiles([]);
    } catch {
      setBusy(false);
      setError("Could not start. Try the file again.");
    }
  }

  async function approve(id: number) {
    const res = await fetch(`/api/quick-print/${id}/approve`, { method: "POST", credentials: "include" });
    if (!res.ok) return;
    const card = await res.json() as QuickPrintCard;
    setCards((current) => mergeCards(current, [card]));
    void queryClient.invalidateQueries({ queryKey: ["jobs"] });
  }

  async function copyMessage(id: number, message: string) {
    try {
      await navigator.clipboard.writeText(message);
      setCopiedId(id);
    } catch {
      setCopiedId(null);
    }
  }

  async function saveWatcher() {
    setError("");
    const res = await fetch("/api/quick-print/settings", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: watcherEnabled,
        folderPath,
        defaultProductId: watcherProduct,
      }),
    });
    const payload = await res.json();
    if (!res.ok) {
      setError(payload.message || "Could not save the drop folder.");
      return;
    }
    setWatcherEnabled(payload.enabled === true);
    setFolderPath(payload.folderPath || "");
  }

  return (
    <Layout>
      <div className="max-w-3xl mx-auto mt-6 mb-16" data-testid="page-print-ready">
        <QuickPrintForm
          productId={productId}
          customW={customW}
          customH={customH}
          quantity={quantity}
          notes={notes}
          fileLabel={fileLabel}
          busy={busy}
          onProduct={setProductId}
          onCustomW={setCustomW}
          onCustomH={setCustomH}
          onQuantity={setQuantity}
          onNotes={setNotes}
          onFiles={(list) => setFiles(list ? Array.from(list) : [])}
          onSubmit={() => void submit()}
        />
        {error && <p className="mt-3 text-sm text-red-700" data-testid="text-quick-error">{error}</p>}
        <div className="mt-6 space-y-4">
          {cards.map((card) => (
            <QuickResultCard
              key={card.id}
              card={card}
              copied={copiedId === card.id}
              onApprove={(id) => void approve(id)}
              onCopy={(id, message) => void copyMessage(id, message)}
            />
          ))}
        </div>
        <p className="mt-8 text-sm">
          <a className="underline" href="/?attention=1" data-testid="link-designer-queue">Designer queue: amber and red only</a>
        </p>
        <WatcherSettings
          enabled={watcherEnabled}
          folderPath={folderPath}
          defaultProductId={watcherProduct}
          onEnabled={setWatcherEnabled}
          onFolder={setFolderPath}
          onProduct={setWatcherProduct}
          onSave={() => void saveWatcher()}
        />
      </div>
    </Layout>
  );
}
