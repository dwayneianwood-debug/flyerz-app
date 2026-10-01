import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import { Layout } from "@/components/layout";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { useToast } from "@/hooks/use-toast";
import { Crop, Download, Loader2, RotateCcw, Upload } from "lucide-react";
import {
  CROP_RATIOS,
  type CropBox,
  type RatioId,
  centeredRatioCrop,
  clampCrop,
  croppedDownloadName,
  mmToPx,
  ptToMm,
  ptToPx,
  pxToMm,
  pxToPt,
} from "@/lib/pure-crop-math";

type FileKind = "jpg" | "png" | "pdf";
type Shape = "rect" | "ellipse" | "circle";
type OutputKind = "original" | "pdf" | "jpg";
type Handle = "n" | "s" | "e" | "w" | "nw" | "ne" | "sw" | "se";

type Artwork = {
  originalFilename: string;
  fileType: FileKind;
  storedFilename: string;
  sourceW: number;
  sourceH: number;
  dpiX: number;
  dpiY: number;
  imageUrl: string;
  objectUrl: string | null;
  pageCount: number;
  page: number;
  pageWidthPt: number;
  pageHeightPt: number;
};

const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

function fileKind(name: string): FileKind | null {
  const ext = name.split(".").pop()?.toLowerCase();
  if (ext === "jpg" || ext === "jpeg") return "jpg";
  if (ext === "png") return "png";
  if (ext === "pdf") return "pdf";
  return null;
}

function ratioValue(id: RatioId, sourceW: number, sourceH: number): number | null {
  if (id === "free") return null;
  if (id === "original") return sourceW / sourceH;
  return CROP_RATIOS.find((item) => item.id === id)?.ratio ?? null;
}

export default function PureCrop() {
  const { toast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [artwork, setArtwork] = useState<Artwork | null>(null);
  const [opening, setOpening] = useState(false);
  const [crop, setCrop] = useState<CropBox>({ x: 0, y: 0, w: 1, h: 1 });
  const [ratio, setRatio] = useState<RatioId>("free");
  const [shape, setShape] = useState<Shape>("rect");
  const [zoom, setZoom] = useState(1);
  const [jpgDpi, setJpgDpi] = useState(300);
  const [saving, setSaving] = useState<OutputKind | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const resetView = useCallback((next: Artwork) => {
    setCrop({ x: 0, y: 0, w: next.sourceW, h: next.sourceH });
    setRatio("free");
    setShape("rect");
    setZoom(1);
    setJpgDpi(300);
  }, []);

  const openFile = useCallback(async (file: File) => {
    const kind = fileKind(file.name);
    if (!kind) {
      toast({ title: "Unsupported file", description: "Use a JPG, PNG, or PDF.", variant: "destructive" });
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      toast({ title: "File too large", description: "Maximum size is 50MB.", variant: "destructive" });
      return;
    }
    setOpening(true);
    const objectUrl = kind === "pdf" ? null : URL.createObjectURL(file);
    try {
      const body = new FormData();
      body.append("file", file);
      const res = await fetch("/api/pure-crop/inspect", { method: "POST", body });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || "Could not open that file");

      let next: Artwork;
      if (kind === "pdf") {
        const page = data.pages?.[0];
        next = {
          originalFilename: data.originalFilename || file.name,
          fileType: "pdf",
          storedFilename: data.storedFilename,
          sourceW: data.preview?.previewWidth || 1,
          sourceH: data.preview?.previewHeight || 1,
          dpiX: 300,
          dpiY: 300,
          imageUrl: data.previewUrl,
          objectUrl: null,
          pageCount: data.pageCount || 1,
          page: 1,
          pageWidthPt: page?.widthPt || data.preview?.widthPt || 1,
          pageHeightPt: page?.heightPt || data.preview?.heightPt || 1,
        };
      } else {
        next = {
          originalFilename: data.originalFilename || file.name,
          fileType: kind,
          storedFilename: data.storedFilename,
          sourceW: data.width,
          sourceH: data.height,
          dpiX: data.dpiX || 300,
          dpiY: data.dpiY || 300,
          imageUrl: objectUrl || "",
          objectUrl,
          pageCount: 1,
          page: 1,
          pageWidthPt: 0,
          pageHeightPt: 0,
        };
      }
      setArtwork((current) => {
        if (current?.objectUrl) URL.revokeObjectURL(current.objectUrl);
        return next;
      });
      resetView(next);
    } catch (error) {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      toast({
        title: "Could not open file",
        description: error instanceof Error ? error.message : "Try another file.",
        variant: "destructive",
      });
    } finally {
      setOpening(false);
    }
  }, [resetView, toast]);

  useEffect(() => {
    return () => {
      if (artwork?.objectUrl) URL.revokeObjectURL(artwork.objectUrl);
    };
  }, [artwork?.objectUrl]);

  const changePage = async (page: number) => {
    if (!artwork || artwork.fileType !== "pdf") return;
    setOpening(true);
    try {
      const res = await fetch("/api/pure-crop/preview-page", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ storedFilename: artwork.storedFilename, page }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || "Could not open that page");
      const pageInfo = { widthPt: data.widthPt, heightPt: data.heightPt };
      const next: Artwork = {
        ...artwork,
        page,
        sourceW: data.previewWidth,
        sourceH: data.previewHeight,
        imageUrl: data.previewUrl,
        pageWidthPt: pageInfo.widthPt,
        pageHeightPt: pageInfo.heightPt,
      };
      setArtwork(next);
      resetView(next);
    } catch (error) {
      toast({
        title: "Page preview failed",
        description: error instanceof Error ? error.message : "Try another page.",
        variant: "destructive",
      });
    } finally {
      setOpening(false);
    }
  };

  const applyRatio = (id: RatioId) => {
    if (!artwork) return;
    setRatio(id);
    const value = ratioValue(id, artwork.sourceW, artwork.sourceH);
    if (!value) return;
    setCrop(centeredRatioCrop(artwork.sourceW, artwork.sourceH, value));
  };

  const applyShape = (next: Shape) => {
    setShape(next);
    if (next === "circle") {
      applyRatio("square");
    }
  };

  const sizePx = () => {
    if (!artwork) return { w: 0, h: 0 };
    if (artwork.fileType === "pdf") {
      const wPt = (crop.w / artwork.sourceW) * artwork.pageWidthPt;
      const hPt = (crop.h / artwork.sourceH) * artwork.pageHeightPt;
      return { w: ptToPx(wPt, jpgDpi), h: ptToPx(hPt, jpgDpi) };
    }
    return { w: crop.w, h: crop.h };
  };

  const sizeMm = () => {
    if (!artwork) return { w: 0, h: 0 };
    if (artwork.fileType === "pdf") {
      const wPt = (crop.w / artwork.sourceW) * artwork.pageWidthPt;
      const hPt = (crop.h / artwork.sourceH) * artwork.pageHeightPt;
      return { w: ptToMm(wPt), h: ptToMm(hPt) };
    }
    return { w: pxToMm(crop.w, artwork.dpiX), h: pxToMm(crop.h, artwork.dpiY) };
  };

  const setSizePx = (axis: "w" | "h", value: number) => {
    if (!artwork || !(value > 0)) return;
    const locked = shape === "circle" ? 1 : ratioValue(ratio, artwork.sourceW, artwork.sourceH);
    if (artwork.fileType === "pdf") {
      const pt = pxToPt(value, jpgDpi);
      const preview = axis === "w" ? (pt / artwork.pageWidthPt) * artwork.sourceW : (pt / artwork.pageHeightPt) * artwork.sourceH;
      const next = { ...crop, [axis]: preview };
      if (locked) {
        if (axis === "w") next.h = next.w / locked;
        else next.w = next.h * locked;
      }
      setCrop(clampCrop(next, artwork.sourceW, artwork.sourceH));
      return;
    }
    const next = { ...crop, [axis]: value };
    if (locked) {
      if (axis === "w") next.h = next.w / locked;
      else next.w = next.h * locked;
    }
    setCrop(clampCrop(next, artwork.sourceW, artwork.sourceH));
  };

  const setSizeMm = (axis: "w" | "h", value: number) => {
    if (!artwork || !(value > 0)) return;
    if (artwork.fileType === "pdf") {
      const pt = (value / 25.4) * 72;
      const preview = axis === "w" ? (pt / artwork.pageWidthPt) * artwork.sourceW : (pt / artwork.pageHeightPt) * artwork.sourceH;
      const locked = shape === "circle" ? 1 : ratioValue(ratio, artwork.sourceW, artwork.sourceH);
      const next = { ...crop, [axis]: preview };
      if (locked) {
        if (axis === "w") next.h = next.w / locked;
        else next.w = next.h * locked;
      }
      setCrop(clampCrop(next, artwork.sourceW, artwork.sourceH));
      return;
    }
    const dpi = axis === "w" ? artwork.dpiX : artwork.dpiY;
    setSizePx(axis, mmToPx(value, dpi));
  };

  const save = async (output: OutputKind) => {
    if (!artwork) return;
    setSaving(output);
    try {
      const x = artwork.fileType === "pdf" ? (crop.x / artwork.sourceW) * artwork.pageWidthPt : crop.x;
      const y = artwork.fileType === "pdf" ? (crop.y / artwork.sourceH) * artwork.pageHeightPt : crop.y;
      const width = artwork.fileType === "pdf" ? (crop.w / artwork.sourceW) * artwork.pageWidthPt : crop.w;
      const height = artwork.fileType === "pdf" ? (crop.h / artwork.sourceH) * artwork.pageHeightPt : crop.h;
      const res = await fetch("/api/pure-crop/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          storedFilename: artwork.storedFilename,
          originalFilename: artwork.originalFilename,
          fileType: artwork.fileType,
          output,
          shape: shape === "rect" ? "rect" : "ellipse",
          page: artwork.page,
          x,
          y,
          width,
          height,
          jpgDpi,
        }),
      });
      if (!res.ok) {
        let message = "Crop failed";
        try {
          const err = await res.json();
          message = err.message || message;
        } catch {
          /* keep default */
        }
        throw new Error(message);
      }
      const blob = await res.blob();
      const ext = output === "original" ? (artwork.fileType === "jpg" ? "jpg" : artwork.fileType) : output === "pdf" ? "pdf" : "jpg";
      const header = res.headers.get("Content-Disposition") || "";
      const encoded = /filename\*=UTF-8''([^;]+)/.exec(header)?.[1];
      const filename = encoded ? decodeURIComponent(encoded) : croppedDownloadName(artwork.originalFilename, ext);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      toast({
        title: "Save failed",
        description: error instanceof Error ? error.message : "Could not save the crop.",
        variant: "destructive",
      });
    } finally {
      setSaving(null);
    }
  };

  const px = sizePx();
  const mm = sizeMm();
  const originalLabel = artwork?.fileType === "png" ? "PNG" : artwork?.fileType === "pdf" ? "PDF" : "JPG";

  return (
    <Layout>
      <div className="max-w-6xl mx-auto w-full px-4 py-6 space-y-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" data-testid="text-page-title">Manual Crop</h1>
          <p className="text-sm text-muted-foreground">
            Drag in artwork, crop it to any size, and save. Nothing else is changed.
          </p>
        </div>

        {!artwork && (
          <Card
            className={`p-8 border-2 border-dashed min-h-[320px] flex items-center justify-center text-center cursor-pointer ${dragOver ? "border-primary bg-primary/5" : "border-border/70"}`}
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => {
              event.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragOver(false);
              const file = event.dataTransfer.files?.[0];
              if (file) void openFile(file);
            }}
            data-testid="drop-pure-crop"
          >
            <div>
              <div className="mx-auto mb-4 w-14 h-14 rounded-full bg-primary/10 text-primary flex items-center justify-center">
                {opening ? <Loader2 className="w-6 h-6 animate-spin" /> : <Upload className="w-6 h-6" />}
              </div>
              <h2 className="text-lg font-semibold">Drop artwork here</h2>
              <p className="text-sm text-muted-foreground mt-1">JPG, PNG, or PDF — or click to browse</p>
            </div>
          </Card>
        )}

        {artwork && (
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
            <Card className="p-3 overflow-hidden" data-testid="panel-crop-stage">
              <CropStage
                imageUrl={artwork.imageUrl}
                sourceW={artwork.sourceW}
                sourceH={artwork.sourceH}
                crop={crop}
                shape={shape}
                zoom={zoom}
                ratio={shape === "circle" ? 1 : ratioValue(ratio, artwork.sourceW, artwork.sourceH)}
                onCropChange={setCrop}
              />
            </Card>

            <Card className="p-4 space-y-4 h-fit" data-testid="panel-crop-controls">
              <div className="min-w-0">
                <p className="text-sm font-semibold truncate" data-testid="text-crop-filename">{artwork.originalFilename}</p>
                <p className="text-xs text-muted-foreground">
                  {artwork.fileType === "pdf"
                    ? `Page ${artwork.page} of ${artwork.pageCount} · ${artwork.pageWidthPt.toFixed(0)} × ${artwork.pageHeightPt.toFixed(0)} pt`
                    : `${Math.round(artwork.sourceW)} × ${Math.round(artwork.sourceH)} px · ${Math.round(artwork.dpiX)} DPI`}
                </p>
              </div>

              {artwork.pageCount > 1 && (
                <label className="block text-xs text-muted-foreground">
                  Page
                  <select
                    className="mt-1 w-full h-9 rounded-md border bg-background px-2 text-sm text-foreground"
                    value={artwork.page}
                    onChange={(event) => void changePage(Number(event.target.value))}
                    data-testid="select-crop-page"
                  >
                    {Array.from({ length: artwork.pageCount }, (_, index) => (
                      <option key={index + 1} value={index + 1}>Page {index + 1}</option>
                    ))}
                  </select>
                </label>
              )}

              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">Shape</p>
                <div className="grid grid-cols-3 gap-1">
                  {(["rect", "ellipse", "circle"] as Shape[]).map((item) => (
                    <Button
                      key={item}
                      type="button"
                      size="sm"
                      variant={shape === item ? "default" : "outline"}
                      onClick={() => applyShape(item)}
                      data-testid={`button-shape-${item}`}
                    >
                      {item === "rect" ? "Rectangle" : item === "ellipse" ? "Ellipse" : "Circle"}
                    </Button>
                  ))}
                </div>
              </div>

              <label className="block text-xs text-muted-foreground">
                Proportion
                <select
                  className="mt-1 w-full h-9 rounded-md border bg-background px-2 text-sm text-foreground"
                  value={shape === "circle" ? "square" : ratio}
                  onChange={(event) => applyRatio(event.target.value as RatioId)}
                  data-testid="select-crop-ratio"
                >
                  {CROP_RATIOS.map((item) => (
                    <option key={item.id} value={item.id}>{item.label}</option>
                  ))}
                </select>
              </label>

              <div className="rounded-lg bg-muted/40 px-3 py-2" data-testid="text-crop-size">
                <p className="text-sm font-semibold">{Math.round(px.w)} × {Math.round(px.h)} px</p>
                <p className="text-xs text-muted-foreground">{mm.w.toFixed(1)} × {mm.h.toFixed(1)} mm{artwork.fileType === "pdf" ? ` at ${jpgDpi} DPI` : ""}</p>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <SizeField label="Width px" value={px.w} onCommit={(value) => setSizePx("w", value)} testId="input-crop-width-px" />
                <SizeField label="Height px" value={px.h} onCommit={(value) => setSizePx("h", value)} testId="input-crop-height-px" />
                <SizeField label="Width mm" value={mm.w} onCommit={(value) => setSizeMm("w", value)} testId="input-crop-width-mm" />
                <SizeField label="Height mm" value={mm.h} onCommit={(value) => setSizeMm("h", value)} testId="input-crop-height-mm" />
              </div>

              {artwork.fileType === "pdf" && (
                <label className="block text-xs text-muted-foreground">
                  JPG save DPI
                  <input
                    type="number"
                    min={36}
                    max={1200}
                    value={jpgDpi}
                    onChange={(event) => setJpgDpi(Math.min(1200, Math.max(36, Number(event.target.value) || 300)))}
                    className="mt-1 w-full h-9 rounded-md border bg-background px-2 text-sm text-foreground"
                    data-testid="input-jpg-dpi"
                  />
                </label>
              )}

              <div>
                <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
                  <span>Zoom</span>
                  <span>{Math.round(zoom * 100)}%</span>
                </div>
                <Slider value={[zoom]} min={0.5} max={4} step={0.05} onValueChange={(value) => setZoom(value[0] || 1)} data-testid="slider-crop-zoom" />
              </div>

              <Button
                type="button"
                variant="outline"
                className="w-full"
                onClick={() => artwork && resetView(artwork)}
                data-testid="button-crop-reset"
              >
                <RotateCcw className="w-4 h-4 mr-2" />
                Reset
              </Button>

              <div className="space-y-2 pt-1">
                <SaveButton label={`Save original format`} busy={saving === "original"} disabled={!!saving} onClick={() => void save("original")} testId="button-save-original" />
                <SaveButton label="Save as PDF" busy={saving === "pdf"} disabled={!!saving} onClick={() => void save("pdf")} testId="button-save-pdf" />
                <SaveButton label="Save as JPG" busy={saving === "jpg"} disabled={!!saving} onClick={() => void save("jpg")} testId="button-save-jpg" />
                <p className="text-[11px] text-muted-foreground">Original format for this file is {originalLabel}. Ellipse and circle crops paint the outside white on JPG and PDF, and transparent on PNG.</p>
              </div>

              <Button type="button" variant="ghost" className="w-full" onClick={() => inputRef.current?.click()} data-testid="button-crop-replace">
                <Crop className="w-4 h-4 mr-2" />
                Crop a different file
              </Button>
            </Card>
          </div>
        )}

        <input
          ref={inputRef}
          type="file"
          accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
          className="hidden"
          data-testid="input-pure-crop-file"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void openFile(file);
          }}
        />
      </div>
    </Layout>
  );
}

function SaveButton({ label, busy, disabled, onClick, testId }: { label: string; busy: boolean; disabled: boolean; onClick: () => void; testId: string }) {
  return (
    <Button type="button" className="w-full" disabled={disabled} onClick={onClick} data-testid={testId}>
      {busy ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />}
      {label}
    </Button>
  );
}

function SizeField({ label, value, onCommit, testId }: { label: string; value: number; onCommit: (value: number) => void; testId: string }) {
  const [text, setText] = useState(value.toFixed(label.endsWith("mm") ? 1 : 0));
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setText(value.toFixed(label.endsWith("mm") ? 1 : 0));
  }, [label, value]);
  return (
    <label className="block text-[11px] text-muted-foreground">
      {label}
      <input
        value={text}
        inputMode="decimal"
        className="mt-1 w-full h-9 rounded-md border bg-background px-2 text-sm text-foreground"
        data-testid={testId}
        onFocus={() => {
          focused.current = true;
        }}
        onBlur={() => {
          focused.current = false;
          const next = Number(text);
          if (next > 0) onCommit(next);
        }}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") (event.target as HTMLInputElement).blur();
        }}
      />
    </label>
  );
}

function CropStage({
  imageUrl,
  sourceW,
  sourceH,
  crop,
  shape,
  zoom,
  ratio,
  onCropChange,
}: {
  imageUrl: string;
  sourceW: number;
  sourceH: number;
  crop: CropBox;
  shape: Shape;
  zoom: number;
  ratio: number | null;
  onCropChange: (crop: CropBox) => void;
}) {
  const stageRef = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState(1);
  const cropRef = useRef(crop);
  cropRef.current = crop;

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const measure = () => {
      const sx = Math.max(1, el.clientWidth - 16) / sourceW;
      const sy = Math.max(1, el.clientHeight - 16) / sourceH;
      setFit(Math.min(sx, sy));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [sourceH, sourceW]);

  const scale = fit * zoom;
  const layoutW = sourceW * scale;
  const layoutH = sourceH * scale;
  const ellipse = shape !== "rect";

  const toSource = (event: ReactPointerEvent | PointerEvent, frame: DOMRect) => ({
    x: (event.clientX - frame.left) / scale,
    y: (event.clientY - frame.top) / scale,
  });

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    const frame = event.currentTarget.getBoundingClientRect();
    const point = toSource(event, frame);
    const handle = hitHandle(point, crop, 10 / scale);
    const inside = point.x >= crop.x && point.x <= crop.x + crop.w && point.y >= crop.y && point.y <= crop.y + crop.h;
    event.currentTarget.setPointerCapture(event.pointerId);
    const start = { ...point };
    const orig = { ...crop };
    const mode = handle ? "resize" : inside ? "move" : "create";

    const move = (ev: PointerEvent) => {
      const nextPoint = {
        x: (ev.clientX - frame.left) / scale,
        y: (ev.clientY - frame.top) / scale,
      };
      if (mode === "move") {
        onCropChange(clampCrop({
          ...orig,
          x: orig.x + (nextPoint.x - start.x),
          y: orig.y + (nextPoint.y - start.y),
        }, sourceW, sourceH));
        return;
      }
      if (mode === "create") {
        onCropChange(boxFromPoints(start, nextPoint, sourceW, sourceH, ratio));
        return;
      }
      onCropChange(resizeHandle(orig, handle as Handle, nextPoint, sourceW, sourceH, ratio));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <div ref={stageRef} className="h-[68vh] min-h-[420px] overflow-auto rounded-lg bg-muted/30" data-testid="crop-viewport">
      <div className="min-w-full min-h-full flex items-center justify-center p-2">
        <div
          className="relative touch-none select-none"
          style={{ width: layoutW, height: layoutH }}
          onPointerDown={onPointerDown}
          data-testid="crop-frame"
        >
          <img src={imageUrl} alt="Artwork to crop" draggable={false} className="absolute inset-0 w-full h-full" data-testid="img-pure-crop" />
          {!ellipse && <Shade crop={crop} scale={scale} layoutW={layoutW} layoutH={layoutH} />}
          <div
            className="absolute border-2 border-white shadow-[0_0_0_1px_rgba(0,0,0,0.45)]"
            style={{
              left: crop.x * scale,
              top: crop.y * scale,
              width: Math.max(1, crop.w * scale),
              height: Math.max(1, crop.h * scale),
              borderRadius: ellipse ? "50%" : 0,
              boxShadow: ellipse ? "0 0 0 9999px rgba(0,0,0,0.5)" : undefined,
            }}
            data-testid="crop-rect"
          />
          {HANDLES.map((handle) => (
            <span
              key={handle}
              className="absolute z-10 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-sm border border-white bg-primary"
              style={handleStyle(handle, crop, scale)}
              data-testid={`crop-handle-${handle}`}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function Shade({ crop, scale, layoutW, layoutH }: { crop: CropBox; scale: number; layoutW: number; layoutH: number }) {
  const left = crop.x * scale;
  const top = crop.y * scale;
  const width = crop.w * scale;
  const height = crop.h * scale;
  return (
    <>
      <div className="absolute left-0 top-0 bg-black/45" style={{ width: layoutW, height: top }} />
      <div className="absolute left-0 bg-black/45" style={{ top, width: left, height }} />
      <div className="absolute bg-black/45" style={{ top, left: left + width, width: Math.max(0, layoutW - left - width), height }} />
      <div className="absolute left-0 bg-black/45" style={{ top: top + height, width: layoutW, height: Math.max(0, layoutH - top - height) }} />
    </>
  );
}

function handleStyle(handle: Handle, crop: CropBox, scale: number): CSSProperties {
  const cx = handle.includes("w") ? crop.x : handle.includes("e") ? crop.x + crop.w : crop.x + crop.w / 2;
  const cy = handle.includes("n") ? crop.y : handle.includes("s") ? crop.y + crop.h : crop.y + crop.h / 2;
  return { left: cx * scale, top: cy * scale };
}

function hitHandle(point: { x: number; y: number }, crop: CropBox, slop: number): Handle | null {
  const spots: { handle: Handle; x: number; y: number }[] = [
    { handle: "nw", x: crop.x, y: crop.y },
    { handle: "ne", x: crop.x + crop.w, y: crop.y },
    { handle: "sw", x: crop.x, y: crop.y + crop.h },
    { handle: "se", x: crop.x + crop.w, y: crop.y + crop.h },
    { handle: "n", x: crop.x + crop.w / 2, y: crop.y },
    { handle: "s", x: crop.x + crop.w / 2, y: crop.y + crop.h },
    { handle: "w", x: crop.x, y: crop.y + crop.h / 2 },
    { handle: "e", x: crop.x + crop.w, y: crop.y + crop.h / 2 },
  ];
  let best: Handle | null = null;
  let bestDist = slop;
  for (const spot of spots) {
    const dist = Math.hypot(point.x - spot.x, point.y - spot.y);
    if (dist <= bestDist) {
      best = spot.handle;
      bestDist = dist;
    }
  }
  return best;
}

function boxFromPoints(
  start: { x: number; y: number },
  end: { x: number; y: number },
  boundsW: number,
  boundsH: number,
  ratio: number | null,
): CropBox {
  if (!ratio) {
    const x = Math.min(start.x, end.x);
    const y = Math.min(start.y, end.y);
    return clampCrop({ x, y, w: Math.abs(end.x - start.x), h: Math.abs(end.y - start.y) }, boundsW, boundsH);
  }
  return clampCrop(boxWithRatio(start, end, ratio), boundsW, boundsH);
}

function boxWithRatio(anchor: { x: number; y: number }, point: { x: number; y: number }, ratio: number): CropBox {
  const signX = point.x >= anchor.x ? 1 : -1;
  const signY = point.y >= anchor.y ? 1 : -1;
  let w = Math.max(1, Math.abs(point.x - anchor.x));
  let h = Math.max(1, Math.abs(point.y - anchor.y));
  if (w / h > ratio) w = h * ratio;
  else h = w / ratio;
  return {
    x: signX < 0 ? anchor.x - w : anchor.x,
    y: signY < 0 ? anchor.y - h : anchor.y,
    w,
    h,
  };
}

function resizeHandle(
  orig: CropBox,
  handle: Handle,
  point: { x: number; y: number },
  boundsW: number,
  boundsH: number,
  ratio: number | null,
): CropBox {
  const anchors: Record<Handle, { x: number; y: number }> = {
    nw: { x: orig.x + orig.w, y: orig.y + orig.h },
    ne: { x: orig.x, y: orig.y + orig.h },
    sw: { x: orig.x + orig.w, y: orig.y },
    se: { x: orig.x, y: orig.y },
    n: { x: orig.x + orig.w / 2, y: orig.y + orig.h },
    s: { x: orig.x + orig.w / 2, y: orig.y },
    w: { x: orig.x + orig.w, y: orig.y + orig.h / 2 },
    e: { x: orig.x, y: orig.y + orig.h / 2 },
  };
  if (ratio && (handle === "n" || handle === "s" || handle === "e" || handle === "w")) {
    const anchor = anchors[handle];
    const dx = point.x - anchor.x;
    const dy = point.y - anchor.y;
    let w = handle === "n" || handle === "s" ? Math.abs(dy) * ratio : Math.abs(dx);
    let h = w / ratio;
    if (handle === "n" || handle === "s") {
      h = Math.abs(dy);
      w = h * ratio;
    }
    const x = handle === "w" ? anchor.x - w : handle === "e" ? anchor.x : anchor.x - w / 2;
    const y = handle === "n" ? anchor.y - h : handle === "s" ? anchor.y : anchor.y - h / 2;
    return clampCrop({ x, y, w, h }, boundsW, boundsH);
  }
  if (ratio) {
    return clampCrop(boxWithRatio(anchors[handle], point, ratio), boundsW, boundsH);
  }
  let x1 = orig.x;
  let y1 = orig.y;
  let x2 = orig.x + orig.w;
  let y2 = orig.y + orig.h;
  if (handle.includes("w")) x1 = point.x;
  if (handle.includes("e")) x2 = point.x;
  if (handle.includes("n")) y1 = point.y;
  if (handle.includes("s")) y2 = point.y;
  return clampCrop({
    x: Math.min(x1, x2),
    y: Math.min(y1, y2),
    w: Math.abs(x2 - x1),
    h: Math.abs(y2 - y1),
  }, boundsW, boundsH);
}
