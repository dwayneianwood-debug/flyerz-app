import React, { useEffect, useRef, useState } from "react";
import { FileText, UploadCloud } from "lucide-react";

const ACCEPT = ".pdf,.jpg,.jpeg,.png,.ai,.eps,.docx,.pptx";

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** Drag-and-drop artwork picker for the Quick check card. UI only: it hands the
 * chosen FileList to onFiles exactly like the old plain file input did. */
export function FlowDropzone(props: { onFiles: (files: FileList | null) => void; fileLabel?: string }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
  const [preview, setPreview] = useState<string>("");
  const [previewState, setPreviewState] = useState<"idle" | "loading" | "ready" | "none">("idle");

  useEffect(() => {
    setPreview("");
    if (!file) { setPreviewState("idle"); return; }
    const name = file.name.toLowerCase();
    if (/\.(jpe?g|png)$/.test(name) || file.type === "image/jpeg" || file.type === "image/png") {
      const url = URL.createObjectURL(file);
      setPreview(url);
      setPreviewState("ready");
      return () => URL.revokeObjectURL(url);
    }
    if (/\.(pdf|ai|eps)$/.test(name)) {
      let stop = false;
      setPreviewState("loading");
      const form = new FormData();
      form.append("file", file);
      form.append("page", "0");
      fetch("/api/preview-pdf-page", { method: "POST", body: form, credentials: "include" })
        .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
        .then((body) => {
          if (stop) return;
          if (body && typeof body.previewUrl === "string") { setPreview(body.previewUrl); setPreviewState("ready"); }
          else setPreviewState("none");
        })
        .catch(() => { if (!stop) setPreviewState("none"); });
      return () => { stop = true; };
    }
    setPreviewState("none");
  }, [file]);

  function take(list: FileList | null) {
    setFile(list && list[0] ? list[0] : null);
    props.onFiles(list && list.length ? list : null);
  }

  function clear() {
    if (inputRef.current) inputRef.current.value = "";
    take(null);
  }

  return (
    <div className="mb-4">
      <input
        ref={inputRef}
        id="flow-file"
        type="file"
        accept={ACCEPT}
        className="sr-only"
        data-testid="input-flow-file"
        onChange={(event) => take(event.target.files)}
      />
      {!file ? (
        <div
          role="button"
          tabIndex={0}
          data-testid="dropzone-flow-file"
          data-over={over ? "true" : "false"}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); inputRef.current?.click(); } }}
          onDragEnter={(event) => { event.preventDefault(); setOver(true); }}
          onDragOver={(event) => { event.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(event) => { event.preventDefault(); setOver(false); take(event.dataTransfer.files); }}
          className={`cursor-pointer rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${over ? "border-primary bg-primary/10" : "border-border hover:border-primary/50 hover:bg-muted/40"}`}
        >
          <div className={`mx-auto mb-3 w-14 h-14 rounded-full flex items-center justify-center ${over ? "bg-primary/20" : "bg-primary/10"}`}>
            <UploadCloud className="w-7 h-7 text-primary" />
          </div>
          <p className="font-bold text-lg">Drag your artwork here or click to choose</p>
          <p className="text-xs text-muted-foreground mt-2">PDF, JPG, PNG, AI, EPS, DOCX, PPTX</p>
          {props.fileLabel ? <p className="text-sm mt-3 font-semibold" data-testid="text-flow-file">Selected: {props.fileLabel}</p> : null}
        </div>
      ) : (
        <div className="rounded-xl border border-border p-4 flex flex-col sm:flex-row gap-4 items-start" data-testid="panel-flow-file">
          <div className="w-full sm:w-48 shrink-0 rounded-lg border border-border bg-white flex items-center justify-center min-h-32 overflow-hidden">
            {previewState === "ready" && preview ? (
              <img src={preview} alt={`Preview of ${file.name}`} className="max-h-64 w-auto object-contain" data-testid="img-flow-preview" />
            ) : previewState === "loading" ? (
              <p className="text-xs text-muted-foreground p-4" data-testid="text-flow-preview-loading">Loading preview…</p>
            ) : (
              <FileText className="w-10 h-10 text-muted-foreground m-6" data-testid="icon-flow-file" />
            )}
          </div>
          <div className="min-w-0">
            <p className="font-semibold break-all" data-testid="text-flow-file">{file.name}</p>
            <p className="text-sm text-muted-foreground" data-testid="text-flow-file-size">{formatSize(file.size)}</p>
            <button type="button" className="mt-2 text-sm underline text-primary" onClick={clear} data-testid="button-flow-file-change">Remove / change</button>
          </div>
        </div>
      )}
    </div>
  );
}
