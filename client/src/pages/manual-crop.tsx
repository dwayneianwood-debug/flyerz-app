import { Layout } from "@/components/layout";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { useToast } from "@/hooks/use-toast";
import {
  Upload, Crop, Download, Loader2, RotateCcw,
  ZoomOut, ArrowLeft, CheckCircle2, Scissors, Eye,
  Maximize, Move, Type, Plus, Minus, Hand
} from "lucide-react";
import { useState, useRef, useCallback, useEffect } from "react";
import { Link } from "wouter";
import { motion, AnimatePresence } from "framer-motion";

interface PreviewData {
  pages: Array<{
    page: number;
    width_pt?: number;
    height_pt?: number;
    width_mm?: number;
    height_mm?: number;
    width_px?: number;
    height_px?: number;
  }>;
  pageCount: number;
  previewWidth: number;
  previewHeight: number;
  sourceWidth: number;
  sourceHeight: number;
  scale: number;
  originalFilename: string;
  storedFilename: string;
  previewFilename: string;
  fileType: string;
}

interface CropRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

type Step = "upload" | "crop" | "result";
type CropMode = "draw" | "input";

export interface CropCoordinates {
  cropX: number;
  cropY: number;
  cropWidth: number;
  cropHeight: number;
}

export function ManualCropEmbedded({ onCropApply, sourceImageUrl, aspectRatio }: { onCropApply?: (coords: CropCoordinates) => void; sourceImageUrl?: string; aspectRatio?: number } = {}) {
  return <ManualCrop embedded onCropApply={onCropApply} sourceImageUrl={sourceImageUrl} aspectRatio={aspectRatio} />;
}

export default function ManualCrop({ embedded = false, onCropApply, sourceImageUrl, aspectRatio }: { embedded?: boolean; onCropApply?: (coords: CropCoordinates) => void; sourceImageUrl?: string; aspectRatio?: number }) {
  const { toast } = useToast();
  const [step, setStep] = useState<Step>("upload");
  const [uploading, setUploading] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [previewData, setPreviewData] = useState<PreviewData | null>(null);
  const [cropRect, setCropRect] = useState<CropRect>({ x: 0, y: 0, width: 0, height: 0 });
  const [scalePercent, setScalePercent] = useState(100);
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  type DragMode = "create" | "move" | "edge-top" | "edge-bottom" | "edge-left" | "edge-right" | "corner-tl" | "corner-tr" | "corner-bl" | "corner-br" | null;
  const [dragMode, setDragMode] = useState<DragMode>(null);
  const [moveOffset, setMoveOffset] = useState({ x: 0, y: 0 });
  const cropRectRef = useRef(cropRect);
  cropRectRef.current = cropRect;
  const [resultData, setResultData] = useState<any>(null);
  const [cropMode, setCropMode] = useState<CropMode>("draw");
  const [inputCropMm, setInputCropMm] = useState({ width: "", height: "", offsetX: "", offsetY: "" });
  const [viewZoom, setViewZoom] = useState(1.0);
  const [isPanning, setIsPanning] = useState(false);
  const [panDragging, setPanDragging] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });
  const resizeIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sourceImageLoadedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!sourceImageUrl) return;
    if (step === "crop" && imageRef.current && previewData) return;

    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      imageRef.current = img;
      const syntheticPreview: PreviewData = {
        pages: [{ page: 1, width_px: img.naturalWidth, height_px: img.naturalHeight }],
        pageCount: 1,
        previewWidth: img.naturalWidth,
        previewHeight: img.naturalHeight,
        sourceWidth: img.naturalWidth,
        sourceHeight: img.naturalHeight,
        scale: 1,
        originalFilename: "artwork",
        storedFilename: "",
        previewFilename: "",
        fileType: "image",
      };
      setPreviewData(syntheticPreview);
      setCropRect({ x: 0, y: 0, width: 0, height: 0 });
      setStep("crop");
      const wMm = (img.naturalWidth * 25.4 / 300).toFixed(1);
      const hMm = (img.naturalHeight * 25.4 / 300).toFixed(1);
      setInputCropMm({
        width: wMm,
        height: hMm,
        offsetX: "0",
        offsetY: "0",
      });
    };
    img.onerror = () => {
      toast({ title: "Preview Error", description: "Could not load artwork for crop tool", variant: "destructive" });
    };
    img.src = sourceImageUrl;
  }, [sourceImageUrl, toast, step, previewData]);

  const isPdf = previewData?.fileType === "pdf";

  const IMAGE_DPI = 300;

  const sourceWidthMm = previewData
    ? isPdf
      ? previewData.sourceWidth * 25.4 / 72
      : previewData.sourceWidth * 25.4 / IMAGE_DPI
    : 0;

  const sourceHeightMm = previewData
    ? isPdf
      ? previewData.sourceHeight * 25.4 / 72
      : previewData.sourceHeight * 25.4 / IMAGE_DPI
    : 0;

  const mmToPreviewPx = useCallback((mm: number, axis: "x" | "y") => {
    if (!previewData) return 0;
    if (isPdf) {
      const pt = mm * 72 / 25.4;
      return pt * previewData.scale;
    }
    return mm * IMAGE_DPI / 25.4;
  }, [previewData, isPdf]);

  const previewPxToMm = useCallback((px: number, axis: "x" | "y") => {
    if (!previewData) return 0;
    if (isPdf) {
      const pt = px / previewData.scale;
      return pt * 25.4 / 72;
    }
    return px * 25.4 / IMAGE_DPI;
  }, [previewData, isPdf]);

  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imageRef.current;
    if (!canvas || !img || !previewData) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;

    ctx.drawImage(img, 0, 0);

    if (cropRect.width > 2 && cropRect.height > 2) {
      ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
      ctx.fillRect(0, 0, canvas.width, cropRect.y);
      ctx.fillRect(0, cropRect.y, cropRect.x, cropRect.height);
      ctx.fillRect(cropRect.x + cropRect.width, cropRect.y, canvas.width - cropRect.x - cropRect.width, cropRect.height);
      ctx.fillRect(0, cropRect.y + cropRect.height, canvas.width, canvas.height - cropRect.y - cropRect.height);

      ctx.strokeStyle = "#ef4444";
      ctx.lineWidth = 3;
      ctx.setLineDash([]);
      ctx.strokeRect(cropRect.x, cropRect.y, cropRect.width, cropRect.height);

      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1;
      ctx.setLineDash([8, 4]);
      ctx.strokeRect(cropRect.x, cropRect.y, cropRect.width, cropRect.height);
      ctx.setLineDash([]);

      const cornerHandleSize = 16;
      const edgeHandleSize = 14;
      ctx.fillStyle = "#ef4444";
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      const corners = [
        [cropRect.x, cropRect.y],
        [cropRect.x + cropRect.width, cropRect.y],
        [cropRect.x, cropRect.y + cropRect.height],
        [cropRect.x + cropRect.width, cropRect.y + cropRect.height],
      ];
      corners.forEach(([cx, cy]) => {
        ctx.fillRect(cx - cornerHandleSize / 2, cy - cornerHandleSize / 2, cornerHandleSize, cornerHandleSize);
        ctx.strokeRect(cx - cornerHandleSize / 2, cy - cornerHandleSize / 2, cornerHandleSize, cornerHandleSize);
      });

      const midHandles = [
        [cropRect.x + cropRect.width / 2, cropRect.y],
        [cropRect.x + cropRect.width / 2, cropRect.y + cropRect.height],
        [cropRect.x, cropRect.y + cropRect.height / 2],
        [cropRect.x + cropRect.width, cropRect.y + cropRect.height / 2],
      ];
      midHandles.forEach(([cx, cy]) => {
        ctx.fillRect(cx - edgeHandleSize / 2, cy - edgeHandleSize / 2, edgeHandleSize, edgeHandleSize);
        ctx.strokeRect(cx - edgeHandleSize / 2, cy - edgeHandleSize / 2, edgeHandleSize, edgeHandleSize);
      });

      const labelW = previewPxToMm(cropRect.width, "x");
      const labelH = previewPxToMm(cropRect.height, "y");
      const labelText = `${labelW.toFixed(1)} × ${labelH.toFixed(1)} mm`;

      ctx.font = "bold 13px monospace";
      const textMetrics = ctx.measureText(labelText);
      const labelPadX = 8;
      const labelPadY = 4;
      const labelBgW = textMetrics.width + labelPadX * 2;
      const labelBgH = 20;
      let labelX = cropRect.x + cropRect.width / 2 - labelBgW / 2;
      let labelY = cropRect.y - labelBgH - 6;
      if (labelY < 0) labelY = cropRect.y + 6;

      ctx.fillStyle = "rgba(239, 68, 68, 0.9)";
      ctx.beginPath();
      ctx.roundRect(labelX, labelY, labelBgW, labelBgH, 4);
      ctx.fill();

      ctx.fillStyle = "#ffffff";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(labelText, labelX + labelBgW / 2, labelY + labelBgH / 2);

      const thirdW = cropRect.width / 3;
      const thirdH = cropRect.height / 3;
      ctx.strokeStyle = "rgba(255, 255, 255, 0.25)";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(cropRect.x + thirdW, cropRect.y);
      ctx.lineTo(cropRect.x + thirdW, cropRect.y + cropRect.height);
      ctx.moveTo(cropRect.x + thirdW * 2, cropRect.y);
      ctx.lineTo(cropRect.x + thirdW * 2, cropRect.y + cropRect.height);
      ctx.moveTo(cropRect.x, cropRect.y + thirdH);
      ctx.lineTo(cropRect.x + cropRect.width, cropRect.y + thirdH);
      ctx.moveTo(cropRect.x, cropRect.y + thirdH * 2);
      ctx.lineTo(cropRect.x + cropRect.width, cropRect.y + thirdH * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }, [cropRect, previewData, previewPxToMm, isPdf]);

  useEffect(() => {
    drawCanvas();
  }, [drawCanvas]);

  const getCanvasCoords = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    return {
      x: (e.clientX - rect.left) * scaleX,
      y: (e.clientY - rect.top) * scaleY,
    };
  };

  const EDGE_THRESHOLD = 20;
  const CORNER_THRESHOLD = 28;

  const detectHitZone = (px: number, py: number): DragMode => {
    const r = cropRectRef.current;
    if (r.width <= 2 || r.height <= 2) return "create";

    const nearCornerTL = Math.abs(px - r.x) <= CORNER_THRESHOLD && Math.abs(py - r.y) <= CORNER_THRESHOLD;
    const nearCornerTR = Math.abs(px - (r.x + r.width)) <= CORNER_THRESHOLD && Math.abs(py - r.y) <= CORNER_THRESHOLD;
    const nearCornerBL = Math.abs(px - r.x) <= CORNER_THRESHOLD && Math.abs(py - (r.y + r.height)) <= CORNER_THRESHOLD;
    const nearCornerBR = Math.abs(px - (r.x + r.width)) <= CORNER_THRESHOLD && Math.abs(py - (r.y + r.height)) <= CORNER_THRESHOLD;

    if (nearCornerTL) return "corner-tl";
    if (nearCornerTR) return "corner-tr";
    if (nearCornerBL) return "corner-bl";
    if (nearCornerBR) return "corner-br";

    const nearLeft = Math.abs(px - r.x) <= EDGE_THRESHOLD && py > r.y + CORNER_THRESHOLD && py < r.y + r.height - CORNER_THRESHOLD;
    const nearRight = Math.abs(px - (r.x + r.width)) <= EDGE_THRESHOLD && py > r.y + CORNER_THRESHOLD && py < r.y + r.height - CORNER_THRESHOLD;
    const nearTop = Math.abs(py - r.y) <= EDGE_THRESHOLD && px > r.x + CORNER_THRESHOLD && px < r.x + r.width - CORNER_THRESHOLD;
    const nearBottom = Math.abs(py - (r.y + r.height)) <= EDGE_THRESHOLD && px > r.x + CORNER_THRESHOLD && px < r.x + r.width - CORNER_THRESHOLD;

    if (nearTop) return "edge-top";
    if (nearBottom) return "edge-bottom";
    if (nearLeft) return "edge-left";
    if (nearRight) return "edge-right";

    if (
      px >= r.x + EDGE_THRESHOLD &&
      px <= r.x + r.width - EDGE_THRESHOLD &&
      py >= r.y + EDGE_THRESHOLD &&
      py <= r.y + r.height - EDGE_THRESHOLD
    ) {
      return "move";
    }

    return "create";
  };

  const dragAnchorRef = useRef<CropRect>({ x: 0, y: 0, width: 0, height: 0 });

  const startDrag = (px: number, py: number) => {
    const mode = detectHitZone(px, py);
    const r = cropRectRef.current;
    dragAnchorRef.current = { ...r };
    setIsDragging(true);
    setDragStart({ x: px, y: py });
    setDragMode(mode);

    if (mode === "move") {
      setMoveOffset({ x: px - r.x, y: py - r.y });
    } else if (mode === "create") {
      setCropRect({ x: px, y: py, width: 0, height: 0 });
    }
  };

  const enforceAspectRatio = (rect: CropRect, cw: number, ch: number, ar: number): CropRect => {
    let { x, y, width, height } = rect;
    const desiredH = width / ar;
    if (desiredH <= ch - y) {
      height = desiredH;
    } else {
      height = ch - y;
      width = height * ar;
    }
    if (x + width > cw) {
      width = cw - x;
      height = width / ar;
    }
    if (y + height > ch) {
      height = ch - y;
      width = height * ar;
    }
    return { x, y, width: Math.max(3, width), height: Math.max(3, height) };
  };

  const processDrag = (px: number, py: number, cw: number, ch: number) => {
    const anchor = dragAnchorRef.current;
    const ar = aspectRatio;

    if (dragMode === "create") {
      const nx = Math.max(0, Math.min(dragStart.x, px));
      const ny = Math.max(0, Math.min(dragStart.y, py));
      let nw = Math.min(Math.abs(px - dragStart.x), cw - nx);
      let nh = Math.min(Math.abs(py - dragStart.y), ch - ny);
      if (ar && ar > 0) {
        const desiredH = nw / ar;
        if (desiredH <= ch - ny) {
          nh = desiredH;
        } else {
          nh = ch - ny;
          nw = nh * ar;
        }
        if (nx + nw > cw) { nw = cw - nx; nh = nw / ar; }
      }
      setCropRect({ x: nx, y: ny, width: nw, height: nh });
    } else if (dragMode === "move") {
      let nx = px - moveOffset.x;
      let ny = py - moveOffset.y;
      nx = Math.max(0, Math.min(nx, cw - anchor.width));
      ny = Math.max(0, Math.min(ny, ch - anchor.height));
      setCropRect((prev) => ({ ...prev, x: nx, y: ny }));
    } else if (ar && ar > 0) {
      if (dragMode === "edge-right" || dragMode === "edge-bottom" || dragMode === "corner-br") {
        let nw = Math.max(3, Math.min(px - anchor.x, cw - anchor.x));
        let nh = nw / ar;
        if (anchor.y + nh > ch) { nh = ch - anchor.y; nw = nh * ar; }
        setCropRect({ x: anchor.x, y: anchor.y, width: nw, height: nh });
      } else if (dragMode === "corner-tl") {
        const right = anchor.x + anchor.width;
        const bottom = anchor.y + anchor.height;
        let nw = Math.max(3, right - Math.max(0, px));
        let nh = nw / ar;
        let newY = bottom - nh;
        if (newY < 0) { newY = 0; nh = bottom; nw = nh * ar; }
        setCropRect({ x: right - nw, y: newY, width: nw, height: nh });
      } else if (dragMode === "corner-tr") {
        const bottom = anchor.y + anchor.height;
        let nw = Math.max(3, Math.min(px - anchor.x, cw - anchor.x));
        let nh = nw / ar;
        let newY = bottom - nh;
        if (newY < 0) { newY = 0; nh = bottom; nw = nh * ar; }
        setCropRect({ x: anchor.x, y: newY, width: nw, height: nh });
      } else if (dragMode === "corner-bl") {
        const right = anchor.x + anchor.width;
        let nw = Math.max(3, right - Math.max(0, px));
        let nh = nw / ar;
        if (anchor.y + nh > ch) { nh = ch - anchor.y; nw = nh * ar; }
        setCropRect({ x: right - nw, y: anchor.y, width: nw, height: nh });
      } else if (dragMode === "edge-left") {
        const right = anchor.x + anchor.width;
        let nw = Math.max(3, right - Math.max(0, px));
        let nh = nw / ar;
        if (anchor.y + nh > ch) { nh = ch - anchor.y; nw = nh * ar; }
        setCropRect({ x: right - nw, y: anchor.y, width: nw, height: nh });
      } else if (dragMode === "edge-top") {
        const bottom = anchor.y + anchor.height;
        let nh = Math.max(3, bottom - Math.max(0, py));
        let nw = nh * ar;
        if (anchor.x + nw > cw) { nw = cw - anchor.x; nh = nw / ar; }
        setCropRect({ x: anchor.x, y: bottom - nh, width: nw, height: nh });
      }
    } else {
      if (dragMode === "edge-top") {
        const bottom = anchor.y + anchor.height;
        let newY = Math.max(0, Math.min(py, bottom - 3));
        setCropRect((prev) => ({ ...prev, y: newY, height: bottom - newY }));
      } else if (dragMode === "edge-bottom") {
        let newBottom = Math.max(anchor.y + 3, Math.min(py, ch));
        setCropRect((prev) => ({ ...prev, height: newBottom - prev.y }));
      } else if (dragMode === "edge-left") {
        const right = anchor.x + anchor.width;
        let newX = Math.max(0, Math.min(px, right - 3));
        setCropRect((prev) => ({ ...prev, x: newX, width: right - newX }));
      } else if (dragMode === "edge-right") {
        let newRight = Math.max(anchor.x + 3, Math.min(px, cw));
        setCropRect((prev) => ({ ...prev, width: newRight - prev.x }));
      } else if (dragMode === "corner-tl") {
        const right = anchor.x + anchor.width;
        const bottom = anchor.y + anchor.height;
        let newX = Math.max(0, Math.min(px, right - 3));
        let newY = Math.max(0, Math.min(py, bottom - 3));
        setCropRect({ x: newX, y: newY, width: right - newX, height: bottom - newY });
      } else if (dragMode === "corner-tr") {
        const bottom = anchor.y + anchor.height;
        let newRight = Math.max(anchor.x + 3, Math.min(px, cw));
        let newY = Math.max(0, Math.min(py, bottom - 3));
        setCropRect((prev) => ({ ...prev, y: newY, width: newRight - prev.x, height: bottom - newY }));
      } else if (dragMode === "corner-bl") {
        const right = anchor.x + anchor.width;
        let newX = Math.max(0, Math.min(px, right - 3));
        let newBottom = Math.max(anchor.y + 3, Math.min(py, ch));
        setCropRect((prev) => ({ ...prev, x: newX, width: right - newX, height: newBottom - prev.y }));
      } else if (dragMode === "corner-br") {
        let newRight = Math.max(anchor.x + 3, Math.min(px, cw));
        let newBottom = Math.max(anchor.y + 3, Math.min(py, ch));
        setCropRect((prev) => ({ ...prev, width: newRight - prev.x, height: newBottom - prev.y }));
      }
    }
  };

  const endDrag = () => {
    setIsDragging(false);
    setDragMode(null);
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (isPanning) {
      setPanDragging(true);
      setPanStart({ x: e.clientX, y: e.clientY });
      return;
    }
    const { x, y } = getCanvasCoords(e);
    startDrag(x, y);
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (isPanning && panDragging) {
      const sc = scrollContainerRef.current;
      if (!sc) return;
      sc.scrollLeft -= (e.clientX - panStart.x);
      sc.scrollTop -= (e.clientY - panStart.y);
      setPanStart({ x: e.clientX, y: e.clientY });
      return;
    }
    if (!isDragging || !canvasRef.current) return;
    const { x, y } = getCanvasCoords(e);
    processDrag(x, y, canvasRef.current.width, canvasRef.current.height);
  };

  const handleMouseUp = () => {
    if (isPanning) { setPanDragging(false); return; }
    endDrag();
  };

  const handleTouchStart = (e: React.TouchEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const touch = e.touches[0];
    if (isPanning) {
      setPanDragging(true);
      setPanStart({ x: touch.clientX, y: touch.clientY });
      return;
    }
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const x = (touch.clientX - rect.left) * scaleX;
    const y = (touch.clientY - rect.top) * scaleY;
    startDrag(x, y);
  };

  const handleTouchMove = (e: React.TouchEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    if (isPanning && panDragging) {
      const touch = e.touches[0];
      const sc = scrollContainerRef.current;
      if (!sc) return;
      sc.scrollLeft -= (touch.clientX - panStart.x);
      sc.scrollTop -= (touch.clientY - panStart.y);
      setPanStart({ x: touch.clientX, y: touch.clientY });
      return;
    }
    if (!isDragging || !canvasRef.current) return;
    const touch = e.touches[0];
    const canvas = canvasRef.current;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const x = (touch.clientX - rect.left) * scaleX;
    const y = (touch.clientY - rect.top) * scaleY;
    processDrag(x, y, canvas.width, canvas.height);
  };

  const handleTouchEnd = () => {
    if (isPanning) { setPanDragging(false); return; }
    endDrag();
  };

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch("/api/manual-crop/preview", {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.message || "Upload failed");
      }

      const data = await res.json();
      setPreviewData(data);

      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => {
        imageRef.current = img;
        setCropRect({ x: 0, y: 0, width: 0, height: 0 });
        setStep("crop");

        if (data.fileType === "pdf" && data.pages?.[0]) {
          setInputCropMm({
            width: String(data.pages[0].width_mm || ""),
            height: String(data.pages[0].height_mm || ""),
            offsetX: "0",
            offsetY: "0",
          });
        } else {
          const wMm = ((data.sourceWidth || 0) * 25.4 / 300).toFixed(1);
          const hMm = ((data.sourceHeight || 0) * 25.4 / 300).toFixed(1);
          setInputCropMm({
            width: wMm,
            height: hMm,
            offsetX: "0",
            offsetY: "0",
          });
        }
      };
      img.onerror = () => {
        toast({ title: "Preview Error", description: "Could not load preview image", variant: "destructive" });
      };
      img.src = `/api/manual-crop/preview-image/${data.previewFilename}`;
    } catch (err: any) {
      toast({ title: "Upload Failed", description: err.message, variant: "destructive" });
    } finally {
      setUploading(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) handleUpload(file);
  };

  const selectFullCanvas = () => {
    const canvas = canvasRef.current;
    if (!canvas || !canvas.width || !canvas.height) return;
    setCropRect({ x: 0, y: 0, width: canvas.width, height: canvas.height });
  };

  const applyCropFromMmInputs = () => {
    if (!previewData || !imageRef.current) return;

    const w = parseFloat(inputCropMm.width) || 0;
    const h = parseFloat(inputCropMm.height) || 0;
    const ox = parseFloat(inputCropMm.offsetX) || 0;
    const oy = parseFloat(inputCropMm.offsetY) || 0;

    if (w <= 0 || h <= 0) {
      toast({ title: "Invalid Dimensions", description: "Width and height must be greater than 0.", variant: "destructive" });
      return;
    }

    const maxW = sourceWidthMm;
    const maxH = sourceHeightMm;

    if (ox + w > maxW + 0.5 || oy + h > maxH + 0.5) {
      toast({ title: "Crop exceeds artwork", description: `Max area: ${maxW.toFixed(1)} × ${maxH.toFixed(1)} mm`, variant: "destructive" });
      return;
    }

    const previewX = mmToPreviewPx(ox, "x");
    const previewY = mmToPreviewPx(oy, "y");
    const previewW = mmToPreviewPx(w, "x");
    const previewH = mmToPreviewPx(h, "y");

    setCropRect({
      x: Math.max(0, previewX),
      y: Math.max(0, previewY),
      width: Math.min(previewW, imageRef.current.naturalWidth - previewX),
      height: Math.min(previewH, imageRef.current.naturalHeight - previewY),
    });
  };

  const centerCropOnArtwork = () => {
    if (!previewData || !imageRef.current) return;

    const cropMm = getCropDimensionsMm();
    const w = cropMm ? cropMm.width : (parseFloat(inputCropMm.width) || 0);
    const h = cropMm ? cropMm.height : (parseFloat(inputCropMm.height) || 0);

    if (w <= 0 || h <= 0) return;

    const maxW = sourceWidthMm;
    const maxH = sourceHeightMm;

    const ox = Math.max(0, (maxW - w) / 2);
    const oy = Math.max(0, (maxH - h) / 2);

    setInputCropMm(prev => ({ ...prev, width: w.toFixed(1), height: h.toFixed(1), offsetX: ox.toFixed(1), offsetY: oy.toFixed(1) }));

    const previewX = mmToPreviewPx(ox, "x");
    const previewY = mmToPreviewPx(oy, "y");
    const previewW = mmToPreviewPx(w, "x");
    const previewH = mmToPreviewPx(h, "y");

    setCropRect({
      x: Math.max(0, previewX),
      y: Math.max(0, previewY),
      width: Math.min(previewW, imageRef.current.naturalWidth - previewX),
      height: Math.min(previewH, imageRef.current.naturalHeight - previewY),
    });
  };

  const handleSymmetricalResize = (direction: "expand" | "shrink") => {
    if (!imageRef.current) return;
    const current = cropRectRef.current;
    if (current.width < 1 || current.height < 1) return;

    const cw = imageRef.current.naturalWidth;
    const ch = imageRef.current.naturalHeight;
    const stepMm = 0.75;
    const stepPx = mmToPreviewPx(stepMm, "x");
    const delta = direction === "expand" ? stepPx : -stepPx;
    const half = delta / 2;

    let newW = current.width + delta;
    let newH = current.height + delta;

    if (direction === "shrink" && (newW < 3 || newH < 3)) return;

    let newX = current.x - half;
    let newY = current.y - half;

    newX = Math.max(0, newX);
    newY = Math.max(0, newY);
    newW = Math.min(newW, cw - newX);
    newH = Math.min(newH, ch - newY);

    const next = { x: newX, y: newY, width: newW, height: newH };
    setCropRect(next);

    const wMm = previewPxToMm(newW, "x");
    const hMm = previewPxToMm(newH, "y");
    const oxMm = previewPxToMm(newX, "x");
    const oyMm = previewPxToMm(newY, "y");
    setInputCropMm({ width: wMm.toFixed(1), height: hMm.toFixed(1), offsetX: oxMm.toFixed(1), offsetY: oyMm.toFixed(1) });

    requestAnimationFrame(() => {
      const canvas = canvasRef.current;
      const img = imageRef.current;
      if (!canvas || !img || !previewData) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      ctx.drawImage(img, 0, 0);

      if (next.width > 2 && next.height > 2) {
        ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
        ctx.fillRect(0, 0, canvas.width, next.y);
        ctx.fillRect(0, next.y, next.x, next.height);
        ctx.fillRect(next.x + next.width, next.y, canvas.width - next.x - next.width, next.height);
        ctx.fillRect(0, next.y + next.height, canvas.width, canvas.height - next.y - next.height);

        ctx.strokeStyle = "#ef4444";
        ctx.lineWidth = 3;
        ctx.setLineDash([]);
        ctx.strokeRect(next.x, next.y, next.width, next.height);

        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1;
        ctx.setLineDash([8, 4]);
        ctx.strokeRect(next.x, next.y, next.width, next.height);
        ctx.setLineDash([]);

        const handleSize = 10;
        ctx.fillStyle = "#ef4444";
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        const corners = [
          [next.x, next.y],
          [next.x + next.width, next.y],
          [next.x, next.y + next.height],
          [next.x + next.width, next.y + next.height],
        ];
        corners.forEach(([cx, cy]) => {
          ctx.fillRect(cx - handleSize / 2, cy - handleSize / 2, handleSize, handleSize);
          ctx.strokeRect(cx - handleSize / 2, cy - handleSize / 2, handleSize, handleSize);
        });

        const midHandles = [
          [next.x + next.width / 2, next.y],
          [next.x + next.width / 2, next.y + next.height],
          [next.x, next.y + next.height / 2],
          [next.x + next.width, next.y + next.height / 2],
        ];
        midHandles.forEach(([cx, cy]) => {
          ctx.fillRect(cx - handleSize / 2, cy - handleSize / 2, handleSize, handleSize);
          ctx.strokeRect(cx - handleSize / 2, cy - handleSize / 2, handleSize, handleSize);
        });
      }
    });
  };

  const handleSymmetricalResizeRef = useRef(handleSymmetricalResize);
  handleSymmetricalResizeRef.current = handleSymmetricalResize;

  const stopContinuousResize = useCallback(() => {
    if (resizeIntervalRef.current) {
      clearInterval(resizeIntervalRef.current);
      resizeIntervalRef.current = null;
    }
  }, []);

  const startContinuousResize = useCallback((direction: "expand" | "shrink") => {
    stopContinuousResize();
    handleSymmetricalResizeRef.current(direction);
    resizeIntervalRef.current = setInterval(() => {
      handleSymmetricalResizeRef.current(direction);
    }, 80);
  }, [stopContinuousResize]);

  useEffect(() => {
    return () => { stopContinuousResize(); };
  }, [stopContinuousResize]);

  const getCropInSourceCoords = (): CropRect => {
    if (!previewData) return cropRect;
    const invScale = 1 / previewData.scale;
    return {
      x: cropRect.x * invScale,
      y: cropRect.y * invScale,
      width: cropRect.width * invScale,
      height: cropRect.height * invScale,
    };
  };

  const getCropAsPercentages = (): CropRect => {
    if (!previewData || !imageRef.current) return { x: 0, y: 0, width: 0, height: 0 };
    const srcCoords = getCropInSourceCoords();
    const imgW = previewData.sourceWidth || imageRef.current.naturalWidth;
    const imgH = previewData.sourceHeight || imageRef.current.naturalHeight;
    return {
      x: srcCoords.x / imgW,
      y: srcCoords.y / imgH,
      width: srcCoords.width / imgW,
      height: srcCoords.height / imgH,
    };
  };

  const getCropDimensionsMm = () => {
    if (!previewData || cropRect.width <= 0) return null;
    const w = previewPxToMm(cropRect.width, "x");
    const h = previewPxToMm(cropRect.height, "y");
    const ox = previewPxToMm(cropRect.x, "x");
    const oy = previewPxToMm(cropRect.y, "y");
    return { width: w, height: h, offsetX: ox, offsetY: oy };
  };

  const handleExecuteCrop = async () => {
    if (!previewData) return;
    if (cropRect.width <= 2 || cropRect.height <= 2) {
      toast({ title: "No Crop Selected", description: "Draw a crop area on the preview or enter exact dimensions.", variant: "destructive" });
      return;
    }

    const srcCoords = getCropInSourceCoords();

    if (embedded && sourceImageUrl && onCropApply) {
      const pct = getCropAsPercentages();
      console.log(`[CROP] Sending percentage-based crop: x=${pct.x.toFixed(4)}, y=${pct.y.toFixed(4)}, w=${pct.width.toFixed(4)}, h=${pct.height.toFixed(4)}`);
      onCropApply({
        cropX: pct.x,
        cropY: pct.y,
        cropWidth: pct.width,
        cropHeight: pct.height,
      });
      return;
    }

    setProcessing(true);
    try {
      const pctStandalone = getCropAsPercentages();
      const res = await fetch("/api/manual-crop/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          storedFilename: previewData.storedFilename,
          fileType: previewData.fileType,
          cropX: pctStandalone.x,
          cropY: pctStandalone.y,
          cropWidth: pctStandalone.width,
          cropHeight: pctStandalone.height,
          scalePercent,
        }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.message || "Crop failed");
      }

      const data = await res.json();
      setResultData(data);
      setStep("result");
      if (onCropApply) {
        onCropApply({
          cropX: pctStandalone.x,
          cropY: pctStandalone.y,
          cropWidth: pctStandalone.width,
          cropHeight: pctStandalone.height,
        });
      }
      toast({ title: "Crop Complete", description: "Your artwork has been cropped and is ready for download." });
    } catch (err: any) {
      toast({ title: "Crop Failed", description: err.message, variant: "destructive" });
    } finally {
      setProcessing(false);
    }
  };

  const handleReset = () => {
    setCropRect({ x: 0, y: 0, width: 0, height: 0 });
    setScalePercent(100);
    setResultData(null);
    setCropMode("draw");
    setInputCropMm({ width: "", height: "", offsetX: "", offsetY: "" });
    setViewZoom(1.0);
    setIsPanning(false);
    setPanDragging(false);

    if (embedded && sourceImageUrl) {
      sourceImageLoadedRef.current = null;
      setPreviewData(null);
      imageRef.current = null;
      setStep("upload");
    } else {
      setStep("upload");
      setPreviewData(null);
      imageRef.current = null;
    }
  };

  const currentDims = getCropDimensionsMm();

  const scaledDims = currentDims
    ? {
        width: Math.round(currentDims.width * scalePercent / 100 * 10) / 10,
        height: Math.round(currentDims.height * scalePercent / 100 * 10) / 10,
      }
    : null;

  const toolContent = (
    <>
      {!embedded && (
        <div className="flex items-center gap-2 mb-4" data-testid="step-indicator">
          {(["upload", "crop", "result"] as Step[]).map((s, i) => (
            <div key={s} className="flex items-center gap-2">
              <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold transition-colors ${
                step === s ? "bg-primary text-white" :
                (["upload", "crop", "result"].indexOf(step) > i) ? "bg-green-500 text-white" :
                "bg-muted text-muted-foreground"
              }`}>
                {["upload", "crop", "result"].indexOf(step) > i ? (
                  <CheckCircle2 className="w-3 h-3" />
                ) : (
                  i + 1
                )}
              </div>
              <span className={`text-xs font-medium hidden sm:inline ${step === s ? "text-foreground" : "text-muted-foreground"}`}>
                {s === "upload" ? "Upload" : s === "crop" ? "Crop & Scale" : "Download"}
              </span>
              {i < 2 && <div className="w-6 h-px bg-border" />}
            </div>
          ))}
        </div>
      )}

      <AnimatePresence mode="wait">
          {step === "upload" && sourceImageUrl && (
            <motion.div key="loading-source" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -20 }}>
              <Card className="p-8 text-center border-2 border-dashed border-primary/20 bg-primary/5" data-testid="crop-loading">
                <div className="flex flex-col items-center gap-3">
                  <Loader2 className="w-8 h-8 text-primary animate-spin" />
                  <p className="text-sm font-medium text-muted-foreground">Loading artwork into crop tool...</p>
                </div>
              </Card>
            </motion.div>
          )}
          {step === "upload" && !sourceImageUrl && (
            embedded ? (
              <motion.div key="upload-prompt" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -20 }}>
                <Card className="p-8 text-center border-2 border-dashed border-muted-foreground/20 bg-muted/10" data-testid="crop-no-artwork">
                  <div className="flex flex-col items-center gap-3">
                    <Crop className="w-8 h-8 text-muted-foreground/50" />
                    <p className="text-sm font-medium text-muted-foreground">Upload artwork above first, then open this tool to crop it</p>
                  </div>
                </Card>
              </motion.div>
            ) : (
              <motion.div key="upload" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -20 }}>
                <Card
                  className="p-12 text-center border-2 border-dashed border-primary/30 hover:border-primary/50 transition-colors cursor-pointer bg-primary/5"
                  onClick={() => fileInputRef.current?.click()}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={handleDrop}
                  data-testid="dropzone-crop-upload"
                >
                  {uploading ? (
                    <div className="flex flex-col items-center gap-4">
                      <Loader2 className="w-12 h-12 animate-spin text-primary" />
                      <p className="text-lg font-semibold text-foreground">Generating preview...</p>
                      <p className="text-sm text-muted-foreground">Analyzing artwork dimensions</p>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center gap-4">
                      <div className="bg-primary/10 p-4 rounded-2xl">
                        <Upload className="w-10 h-10 text-primary" />
                      </div>
                      <div>
                        <p className="text-lg font-semibold text-foreground mb-1">Drop your file here or click to browse</p>
                        <p className="text-sm text-muted-foreground">PDF, JPG, PNG — up to 50MB</p>
                      </div>
                    </div>
                  )}

                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf,.jpg,.jpeg,.png"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) handleUpload(file);
                      e.target.value = "";
                    }}
                    data-testid="input-crop-file"
                  />
                </Card>
              </motion.div>
            )
          )}

          {step === "crop" && previewData && (
            <motion.div key="crop" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -20 }}>
              <div className="grid grid-cols-1 lg:grid-cols-[1fr_340px] gap-6">
                <div className="space-y-4">
                  <Card className="p-4 overflow-visible">
                    <div className="flex items-center justify-between mb-3 sticky top-0 z-20 bg-background/95 backdrop-blur-sm py-2 -mt-2 -mx-1 px-1 rounded-md">
                      <div className="flex items-center gap-2">
                        <Eye className="w-4 h-4 text-primary" />
                        <span className="text-sm font-bold text-foreground truncate max-w-[200px]">{previewData.originalFilename}</span>
                        <span className="text-xs text-muted-foreground font-mono">
                          {`${sourceWidthMm.toFixed(1)} × ${sourceHeightMm.toFixed(1)} mm`}
                        </span>
                      </div>
                      <div className="flex gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onMouseDown={() => startContinuousResize("shrink")}
                          onMouseUp={stopContinuousResize}
                          onMouseLeave={stopContinuousResize}
                          onTouchStart={() => startContinuousResize("shrink")}
                          onTouchEnd={stopContinuousResize}
                          disabled={cropRect.width <= 2}
                          className="text-xs h-7 w-7 p-0"
                          title="Shrink crop by 1.5mm (hold for continuous)"
                          data-testid="button-crop-shrink"
                        >
                          <Minus className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onMouseDown={() => startContinuousResize("expand")}
                          onMouseUp={stopContinuousResize}
                          onMouseLeave={stopContinuousResize}
                          onTouchStart={() => startContinuousResize("expand")}
                          onTouchEnd={stopContinuousResize}
                          disabled={cropRect.width <= 2}
                          className="text-xs h-7 w-7 p-0"
                          title="Expand crop by 1.5mm (hold for continuous)"
                          data-testid="button-crop-expand"
                        >
                          <Plus className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={centerCropOnArtwork}
                          disabled={cropRect.width <= 2}
                          className="text-xs h-7 px-2"
                          title="Center crop on artwork"
                          data-testid="button-center-crop-top"
                        >
                          <Move className="w-3 h-3 mr-1" />
                          Center
                        </Button>
                        <Button variant="ghost" size="sm" onClick={selectFullCanvas} className="text-xs" data-testid="button-select-all">
                          <Maximize className="w-3 h-3 mr-1" />
                          Select All
                        </Button>
                      </div>
                    </div>

                    <div className="flex items-center justify-between mb-2 sticky top-0 z-10 bg-background/90 backdrop-blur-sm py-1 px-1 rounded-md">
                      <div className="flex items-center gap-1">
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 w-7 p-0"
                          onClick={() => setViewZoom(z => Math.max(0.5, +(z - 0.01).toFixed(2)))}
                          disabled={viewZoom <= 0.5}
                          title="Zoom out 1%"
                          data-testid="button-view-zoom-out"
                        >
                          <Minus className="w-3 h-3" />
                        </Button>
                        <span className="text-xs font-mono text-muted-foreground min-w-[48px] text-center" data-testid="text-view-zoom">
                          {Math.round(viewZoom * 100)}%
                        </span>
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 w-7 p-0"
                          onClick={() => setViewZoom(z => Math.min(3.0, +(z + 0.01).toFixed(2)))}
                          disabled={viewZoom >= 3.0}
                          title="Zoom in 1%"
                          data-testid="button-view-zoom-in"
                        >
                          <Plus className="w-3 h-3" />
                        </Button>
                        {viewZoom !== 1.0 && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 text-xs px-2"
                            onClick={() => setViewZoom(1.0)}
                            data-testid="button-view-zoom-reset"
                          >
                            Reset
                          </Button>
                        )}
                        <div className="w-px h-5 bg-border mx-1" />
                        <Button
                          variant={isPanning ? "secondary" : "ghost"}
                          size="sm"
                          className="h-7 px-2 gap-1 text-xs"
                          onClick={() => { setIsPanning(p => !p); setPanDragging(false); }}
                          title={isPanning ? "Switch to crop mode" : "Switch to pan mode (drag to scroll)"}
                          data-testid="button-toggle-pan"
                        >
                          <Hand className="w-3.5 h-3.5" />
                          {isPanning ? "Pan" : "Pan"}
                        </Button>
                      </div>
                      {currentDims && currentDims.width > 0 && (
                        <span className="text-xs font-mono text-foreground bg-muted px-2 py-0.5 rounded" data-testid="text-live-dimensions">
                          {isPdf
                            ? `${currentDims.width.toFixed(1)} × ${currentDims.height.toFixed(1)} mm`
                            : `${Math.round(currentDims.width)} × ${Math.round(currentDims.height)} px`}
                        </span>
                      )}
                      <p className="text-xs text-muted-foreground hidden sm:block">
                        <span className="text-red-500 font-bold">Red line</span> = crop boundary
                      </p>
                    </div>

                    <div
                      ref={scrollContainerRef}
                      className="relative bg-[repeating-conic-gradient(#d1d5db_0%_25%,#f3f4f6_0%_50%)] dark:bg-[repeating-conic-gradient(#374151_0%_25%,#1f2937_0%_50%)] bg-[length:16px_16px] rounded-lg overflow-auto border-2 border-red-500/30"
                      style={{ maxHeight: "60vh" }}
                    >
                      <div style={{ width: `${viewZoom * 100}%`, minHeight: viewZoom > 1 ? `${viewZoom * 100}%` : undefined }}>
                        <div
                          ref={containerRef}
                          style={{ transform: `scale(${viewZoom})`, transformOrigin: "top left", width: `${100 / viewZoom}%` }}
                        >
                          <canvas
                            ref={canvasRef}
                            className="w-full h-auto block select-none"
                            style={{ cursor: isPanning ? (panDragging ? "grabbing" : "grab") : (isDragging ? (dragMode === "move" ? "grabbing" : dragMode?.startsWith("edge") ? (dragMode === "edge-top" || dragMode === "edge-bottom" ? "ns-resize" : "ew-resize") : dragMode?.startsWith("corner") ? ((dragMode === "corner-tl" || dragMode === "corner-br") ? "nwse-resize" : "nesw-resize") : "crosshair") : "crosshair") }}
                            onMouseDown={handleMouseDown}
                            onMouseMove={handleMouseMove}
                            onMouseUp={handleMouseUp}
                            onMouseLeave={handleMouseUp}
                            onTouchStart={handleTouchStart}
                            onTouchMove={handleTouchMove}
                            onTouchEnd={handleTouchEnd}
                            data-testid="canvas-crop"
                          />
                        </div>
                      </div>
                    </div>
                  </Card>
                </div>

                <div className="space-y-4">
                  <Card className="p-5">
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-sm font-bold text-foreground flex items-center gap-2">
                        <Crop className="w-4 h-4 text-primary" />
                        Crop Area
                      </h3>
                      <div className="flex gap-1">
                        <Button
                          variant={cropMode === "draw" ? "secondary" : "ghost"}
                          size="sm"
                          className="text-xs h-7 px-2"
                          onClick={() => setCropMode("draw")}
                          data-testid="button-mode-draw"
                        >
                          <Move className="w-3 h-3 mr-1" />
                          Draw
                        </Button>
                        <Button
                          variant={cropMode === "input" ? "secondary" : "ghost"}
                          size="sm"
                          className="text-xs h-7 px-2"
                          onClick={() => setCropMode("input")}
                          data-testid="button-mode-input"
                        >
                          <Type className="w-3 h-3 mr-1" />
                          Exact mm
                        </Button>
                      </div>
                    </div>

                    {cropMode === "input" ? (
                      <div className="space-y-3">
                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <Label className="text-xs text-muted-foreground mb-1 block">Width (mm)</Label>
                            <Input
                              type="number"
                              step="0.1"
                              min="1"
                              value={inputCropMm.width}
                              onChange={(e) => setInputCropMm(prev => ({ ...prev, width: e.target.value }))}
                              className="h-9 text-sm font-mono"
                              data-testid="input-crop-width-mm"
                            />
                          </div>
                          <div>
                            <Label className="text-xs text-muted-foreground mb-1 block">Height (mm)</Label>
                            <Input
                              type="number"
                              step="0.1"
                              min="1"
                              value={inputCropMm.height}
                              onChange={(e) => setInputCropMm(prev => ({ ...prev, height: e.target.value }))}
                              className="h-9 text-sm font-mono"
                              data-testid="input-crop-height-mm"
                            />
                          </div>
                        </div>
                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <Label className="text-xs text-muted-foreground mb-1 block">Offset X (mm)</Label>
                            <Input
                              type="number"
                              step="0.1"
                              min="0"
                              value={inputCropMm.offsetX}
                              onChange={(e) => setInputCropMm(prev => ({ ...prev, offsetX: e.target.value }))}
                              className="h-9 text-sm font-mono"
                              data-testid="input-crop-offset-x"
                            />
                          </div>
                          <div>
                            <Label className="text-xs text-muted-foreground mb-1 block">Offset Y (mm)</Label>
                            <Input
                              type="number"
                              step="0.1"
                              min="0"
                              value={inputCropMm.offsetY}
                              onChange={(e) => setInputCropMm(prev => ({ ...prev, offsetY: e.target.value }))}
                              className="h-9 text-sm font-mono"
                              data-testid="input-crop-offset-y"
                            />
                          </div>
                        </div>
                        <div className="flex gap-2">
                          <Button
                            onClick={applyCropFromMmInputs}
                            size="sm"
                            className="flex-1 text-xs"
                            data-testid="button-apply-exact"
                          >
                            Apply Crop
                          </Button>
                        </div>
                        <p className="text-xs text-muted-foreground">
                          Artwork: {`${sourceWidthMm.toFixed(1)} × ${sourceHeightMm.toFixed(1)} mm`}
                        </p>
                      </div>
                    ) : (
                      <div className="space-y-2">
                        {currentDims && currentDims.width > 0 ? (
                          <div className="space-y-2 text-sm">
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">Width</span>
                              <span className="font-mono font-medium" data-testid="text-crop-width">
                                {`${currentDims.width.toFixed(1)} mm`}
                              </span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">Height</span>
                              <span className="font-mono font-medium" data-testid="text-crop-height">
                                {`${currentDims.height.toFixed(1)} mm`}
                              </span>
                            </div>
                            <div className="flex justify-between text-xs">
                              <span className="text-muted-foreground">Position</span>
                              <span className="font-mono">
                                ({currentDims.offsetX.toFixed(1)}, {currentDims.offsetY.toFixed(1)})
                              </span>
                            </div>
                          </div>
                        ) : (
                          <p className="text-sm text-muted-foreground text-center py-3">
                            Draw a crop rectangle on the preview above
                          </p>
                        )}
                      </div>
                    )}
                  </Card>

                  <Card className="p-5">
                    <h3 className="text-sm font-bold text-foreground mb-3 flex items-center gap-2">
                      <ZoomOut className="w-4 h-4 text-primary" />
                      Downscale
                    </h3>

                    <div className="space-y-3">
                      <div className="flex items-center gap-3">
                        <Slider
                          value={[scalePercent]}
                          onValueChange={([v]) => setScalePercent(v)}
                          min={10}
                          max={100}
                          step={1}
                          className="flex-1"
                          data-testid="slider-scale"
                        />
                        <div className="flex items-center gap-1 min-w-[72px]">
                          <Input
                            type="number"
                            value={scalePercent}
                            onChange={(e) => {
                              const v = Math.max(10, Math.min(100, parseInt(e.target.value) || 10));
                              setScalePercent(v);
                            }}
                            className="w-16 h-8 text-center text-sm font-mono"
                            data-testid="input-scale-percent"
                          />
                          <span className="text-sm text-muted-foreground">%</span>
                        </div>
                      </div>

                      {scalePercent < 100 && scaledDims && (
                        <div className="bg-primary/5 border border-primary/20 rounded-lg p-3 text-xs space-y-1">
                          <div className="flex justify-between">
                            <span className="text-muted-foreground">Final width</span>
                            <span className="font-mono font-medium">{scaledDims.width} mm</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-muted-foreground">Final height</span>
                            <span className="font-mono font-medium">{scaledDims.height} mm</span>
                          </div>
                        </div>
                      )}

                      <p className="text-xs text-muted-foreground">
                        {scalePercent === 100 ? "No downscaling — original quality preserved" : `Proportional downscale to ${scalePercent}%`}
                      </p>
                    </div>
                  </Card>

                  <Card className="p-5 bg-muted/30">
                    <h3 className="text-xs font-bold text-muted-foreground mb-2 uppercase tracking-wider">Source Info</h3>
                    <div className="space-y-1 text-xs">
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Type</span>
                        <span className="font-mono uppercase">{previewData.fileType}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Pages</span>
                        <span className="font-mono">{previewData.pageCount}</span>
                      </div>
                      {previewData.pages[0]?.width_mm && (
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">Page size</span>
                          <span className="font-mono">{previewData.pages[0].width_mm} × {previewData.pages[0].height_mm} mm</span>
                        </div>
                      )}
                      {previewData.pages[0]?.width_px && (
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">Image size</span>
                          <span className="font-mono">{previewData.pages[0].width_px} × {previewData.pages[0].height_px} px</span>
                        </div>
                      )}
                    </div>
                  </Card>

                  <div className="space-y-2">
                    <Button
                      onClick={handleExecuteCrop}
                      disabled={processing || cropRect.width <= 2}
                      className="w-full hover-elevate bg-gradient-to-r from-primary to-primary/90 text-white shadow-lg shadow-primary/20"
                      data-testid="button-execute-crop"
                    >
                      {processing ? (
                        <>
                          <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                          Processing...
                        </>
                      ) : (
                        <>
                          <Scissors className="w-4 h-4 mr-2" />
                          {embedded && sourceImageUrl ? "Apply Crop" : "Crop & Download"}
                        </>
                      )}
                    </Button>
                    <Button variant="outline" onClick={handleReset} className="w-full" data-testid="button-reset-crop">
                      <RotateCcw className="w-4 h-4 mr-2" />
                      Start Over
                    </Button>
                  </div>
                </div>
              </div>
            </motion.div>
          )}

          {step === "result" && resultData && (
            <motion.div key="result" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -20 }}>
              <Card className="p-8 text-center border-2 border-green-500/30 bg-green-50/30 dark:bg-green-500/5">
                <div className="flex flex-col items-center gap-4">
                  <div className="bg-green-500/15 p-4 rounded-2xl">
                    <CheckCircle2 className="w-10 h-10 text-green-600 dark:text-green-400" />
                  </div>
                  <h2 className="text-2xl font-bold font-display text-foreground">Crop Complete</h2>
                  <p className="text-muted-foreground max-w-md">
                    Your artwork has been cropped{scalePercent < 100 ? ` and scaled to ${scalePercent}%` : ""} with full quality preserved.
                  </p>

                  {(resultData.pages || resultData.finalSize) && (
                    <div className="bg-background/80 border border-border/60 rounded-lg p-4 text-sm space-y-1 w-full max-w-sm">
                      {resultData.pages?.map((p: any) => (
                        <div key={p.page} className="flex justify-between text-xs">
                          <span className="text-muted-foreground">Page {p.page}</span>
                          <span className="font-mono">
                            {p.final_mm ? `${p.final_mm[0]} × ${p.final_mm[1]} mm` : `${p.finalSize?.[0]} × ${p.finalSize?.[1]} px`}
                          </span>
                        </div>
                      ))}
                      {resultData.finalSize && (
                        <div className="flex justify-between text-xs">
                          <span className="text-muted-foreground">Output</span>
                          <span className="font-mono">{resultData.finalSize[0]} × {resultData.finalSize[1]} px</span>
                        </div>
                      )}
                      <div className="flex justify-between text-xs pt-1 border-t border-border/50">
                        <span className="text-muted-foreground">Scale</span>
                        <span className="font-mono">{resultData.scalePercent}%</span>
                      </div>
                    </div>
                  )}

                  <div className="flex gap-3 mt-2">
                    <a href={resultData.downloadUrl} download={resultData.downloadFilename}>
                      <Button className="hover-elevate bg-gradient-to-r from-primary to-primary/90 text-white shadow-lg" data-testid="button-download-cropped">
                        <Download className="w-4 h-4 mr-2" />
                        Download
                      </Button>
                    </a>
                    <Button variant="outline" onClick={handleReset} data-testid="button-crop-another">
                      <RotateCcw className="w-4 h-4 mr-2" />
                      Crop Another
                    </Button>
                  </div>
                </div>
              </Card>
            </motion.div>
          )}
      </AnimatePresence>
    </> 
  );

  if (embedded) {
    return <div data-testid="embedded-crop-tool">{toolContent}</div>;
  }

  return (
    <Layout>
      <div className="max-w-6xl mx-auto">
        <Link href="/" className="inline-flex items-center text-sm font-medium text-muted-foreground hover:text-foreground transition-colors mb-6">
          <ArrowLeft className="w-4 h-4 mr-1" />
          Back to Dashboard
        </Link>

        <div className="flex items-center gap-3 mb-8">
          <div className="bg-primary/10 p-3 rounded-2xl text-primary">
            <Scissors className="w-7 h-7" />
          </div>
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold font-display text-foreground">Manual Crop & Downscale</h1>
            <p className="text-sm text-muted-foreground font-medium">High-quality crop and scale — no automatic modifications</p>
          </div>
        </div>

        {toolContent}
      </div>
    </Layout>
  );
}
