// Default import keeps the classic JSX runtime working under `tsx` (tsconfig jsx is "preserve").
import React, { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { File, X, ZoomIn } from "lucide-react";
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

/** Share of the viewport the enlarged artwork may occupy. */
export const ARTWORK_PREVIEW_MAX_WIDTH_RATIO = 0.92;
export const ARTWORK_PREVIEW_MAX_HEIGHT_RATIO = 0.78;

/** Thumbnail frame. Larger than the previous 64px (h-16 w-16) tile so the art is visible in the file card. */
export const ARTWORK_THUMB_FRAME_CLASS = "h-24 w-24";

export function fitArtworkPreviewSize(
  naturalWidth: number,
  naturalHeight: number,
  viewportWidth: number,
  viewportHeight: number,
  maxWidthRatio = ARTWORK_PREVIEW_MAX_WIDTH_RATIO,
  maxHeightRatio = ARTWORK_PREVIEW_MAX_HEIGHT_RATIO,
): { width: number; height: number } {
  const maxW = viewportWidth * maxWidthRatio;
  const maxH = viewportHeight * maxHeightRatio;
  if (!(naturalWidth > 0) || !(naturalHeight > 0) || !(maxW > 0) || !(maxH > 0)) {
    return { width: 0, height: 0 };
  }
  const scale = Math.min(maxW / naturalWidth, maxH / naturalHeight);
  return {
    width: Math.max(1, Math.round(naturalWidth * scale)),
    height: Math.max(1, Math.round(naturalHeight * scale)),
  };
}

export function artworkPreviewLabels(
  fileName: string,
  dimensionsMm: { w: number; h: number } | null | undefined,
): { fileName: string; dimensions: string | null } {
  const dimensions =
    dimensionsMm && dimensionsMm.w > 0 && dimensionsMm.h > 0
      ? `${dimensionsMm.w} × ${dimensionsMm.h}mm @ 300 DPI`
      : null;
  return { fileName, dimensions };
}

type ArtworkThumbnailProps = {
  previewUrl: string | null;
  fileName: string;
  dimensionsMm?: { w: number; h: number } | null;
};

export function ArtworkThumbnail({ previewUrl, fileName, dimensionsMm }: ArtworkThumbnailProps) {
  const [open, setOpen] = useState(false);
  const labels = artworkPreviewLabels(fileName, dimensionsMm);

  useEffect(() => {
    if (!previewUrl) setOpen(false);
  }, [previewUrl]);

  if (!previewUrl) {
    return (
      <div
        className={cn(
          "flex shrink-0 items-center justify-center rounded-lg border border-border/40 bg-muted/30",
          ARTWORK_THUMB_FRAME_CLASS,
        )}
        data-testid="artwork-thumb-placeholder"
      >
        <File className="h-8 w-8 text-primary/60" />
      </div>
    );
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn(
          "group relative shrink-0 cursor-pointer rounded-lg border border-border/40 bg-muted/30 p-0 transition-colors",
          "hover:border-primary/50 hover:ring-2 hover:ring-primary/30",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
          ARTWORK_THUMB_FRAME_CLASS,
        )}
        aria-label={`View larger preview of ${fileName}`}
        title="View larger"
        data-testid="button-open-artwork-preview"
      >
        <span className="absolute inset-0 overflow-hidden rounded-[inherit]">
          <img
            src={previewUrl}
            alt="Artwork preview"
            draggable={false}
            className="h-full w-full object-contain transition-transform duration-200 group-hover:scale-105"
            data-testid="img-staged-preview"
          />
          <span className="pointer-events-none absolute inset-0 bg-black/0 transition-colors group-hover:bg-black/25" />
        </span>
        <span
          className="pointer-events-none absolute bottom-1 right-1 flex h-6 w-6 items-center justify-center rounded-md bg-black/60 text-white shadow-sm transition-colors group-hover:bg-primary"
          data-testid="icon-artwork-zoom"
        >
          <ZoomIn className="h-3.5 w-3.5" aria-hidden="true" />
        </span>
      </button>
      <ArtworkPreviewDialog
        open={open}
        onOpenChange={setOpen}
        previewUrl={previewUrl}
        fileName={labels.fileName}
        dimensionsLabel={labels.dimensions}
      />
    </>
  );
}

type ArtworkPreviewDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  previewUrl: string;
  fileName: string;
  dimensionsLabel: string | null;
};

function ArtworkPreviewDialog({
  open,
  onOpenChange,
  previewUrl,
  fileName,
  dimensionsLabel,
}: ArtworkPreviewDialogProps) {
  const naturalRef = useRef<{ w: number; h: number } | null>(null);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);

  const updateFit = useCallback(() => {
    const natural = naturalRef.current;
    if (!natural || typeof window === "undefined") return;
    setSize(
      fitArtworkPreviewSize(natural.w, natural.h, window.innerWidth, window.innerHeight),
    );
  }, []);

  useEffect(() => {
    naturalRef.current = null;
    setSize(null);
  }, [previewUrl]);

  useEffect(() => {
    if (!open) return;
    updateFit();
    window.addEventListener("resize", updateFit);
    return () => window.removeEventListener("resize", updateFit);
  }, [open, updateFit]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogOverlay className="bg-black/85" data-testid="artwork-preview-backdrop" />
        <DialogPrimitive.Content
          data-testid="dialog-artwork-preview"
          className="fixed left-1/2 top-1/2 z-50 flex w-max max-w-[96vw] -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-3 border-0 bg-transparent p-0 shadow-none outline-none duration-200 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95"
        >
          <ArtworkPreviewFigure
            previewUrl={previewUrl}
            fileName={fileName}
            dimensionsLabel={dimensionsLabel}
            size={size}
            onLoad={(naturalWidth, naturalHeight) => {
              naturalRef.current = { w: naturalWidth, h: naturalHeight };
              updateFit();
            }}
            title={
              <DialogTitle className="text-sm font-medium leading-snug text-white break-all">
                {fileName}
              </DialogTitle>
            }
            description={
              dimensionsLabel ? (
                <DialogDescription className="mt-1 text-xs text-white/75">
                  {dimensionsLabel}
                </DialogDescription>
              ) : (
                <DialogDescription className="sr-only">Full-size artwork preview</DialogDescription>
              )
            }
          />
          <DialogClose
            className="fixed right-4 top-4 z-[60] rounded-full border border-white/25 bg-black/70 p-2 text-white opacity-100 shadow-lg ring-offset-black transition-colors hover:bg-black/90 focus:outline-none focus:ring-2 focus:ring-white focus:ring-offset-2"
            data-testid="button-close-artwork-preview"
            aria-label="Close preview"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </DialogClose>
        </DialogPrimitive.Content>
      </DialogPortal>
    </Dialog>
  );
}

export function ArtworkPreviewFigure({
  previewUrl,
  fileName,
  dimensionsLabel,
  size,
  onLoad,
  title,
  description,
}: {
  previewUrl: string;
  fileName: string;
  dimensionsLabel: string | null;
  size: { width: number; height: number } | null;
  onLoad?: (naturalWidth: number, naturalHeight: number) => void;
  title: ReactNode;
  description: ReactNode;
}) {
  return (
    <figure
      className="flex flex-col items-center gap-3"
      data-testid="artwork-preview-figure"
      data-has-dimensions={dimensionsLabel ? "true" : "false"}
    >
      <img
        src={previewUrl}
        alt={fileName}
        draggable={false}
        data-testid="img-artwork-preview-large"
        onLoad={(event) => {
          const img = event.currentTarget;
          if (img.naturalWidth > 0 && img.naturalHeight > 0) {
            onLoad?.(img.naturalWidth, img.naturalHeight);
          }
        }}
        style={{
          maxWidth: `${ARTWORK_PREVIEW_MAX_WIDTH_RATIO * 100}vw`,
          maxHeight: `${ARTWORK_PREVIEW_MAX_HEIGHT_RATIO * 100}vh`,
          ...(size ? { width: size.width, height: size.height } : {}),
        }}
        className="h-auto w-auto object-contain"
      />
      <figcaption className="w-full max-w-[min(92vw,40rem)] px-1 text-center">
        {title}
        {description}
      </figcaption>
    </figure>
  );
}
