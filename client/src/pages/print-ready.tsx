import { useEffect, useState } from "react";
import { Layout } from "@/components/layout";
import { FileUpload } from "@/components/file-upload";
import { QuickResultCard, WatcherSettings } from "./print-ready-view";
import { PrintFlow } from "./print-flow";
import type { FlowTab } from "./print-flow-view";
import { ManualWizard } from "./manual-wizard";
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
  const [tab, setTab] = useState<FlowTab>("ready");
  const [error, setError] = useState("");
  const [cards, setCards] = useState<QuickPrintCard[]>([]);
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
      <div className="max-w-5xl mx-auto mt-6 mb-16 px-1" data-testid="page-print-ready">
        <PrintFlow
          tab={tab}
          onTab={setTab}
          manual={<ManualWizard><FileUpload /></ManualWizard>}
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
          <a className="underline" href="/manual?attention=1" data-testid="link-designer-queue">Designer queue: amber and red only</a>
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
