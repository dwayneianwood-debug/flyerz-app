import { useEffect, useState } from "react";

export interface CoverCropNoticeData {
  success?: boolean;
  cropped?: boolean;
  leftMm?: number;
  rightMm?: number;
  topMm?: number;
  bottomMm?: number;
  contentInTrim?: boolean;
  contentOutsideSafe?: boolean;
  summary?: string;
  warning?: string;
  previewUrl?: string;
  pageFit?: string;
}

export function CoverCropNotice({
  jobId,
  trimWidthMm,
  trimHeightMm,
  onFitWhole,
}: {
  jobId: number;
  trimWidthMm: number;
  trimHeightMm: number;
  onFitWhole?: () => void;
}) {
  const [notice, setNotice] = useState<CoverCropNoticeData | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const url = `/api/jobs/${jobId}/cover-crop-notice?trimW=${encodeURIComponent(String(trimWidthMm))}&trimH=${encodeURIComponent(String(trimHeightMm))}`;
    fetch(url)
      .then((res) => res.json())
      .then((data) => {
        if (!cancelled) setNotice(data);
      })
      .catch(() => {
        if (!cancelled) setNotice(null);
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, trimWidthMm, trimHeightMm, notice?.pageFit]);

  if (!notice?.cropped) return null;

  const chooseWhole = async () => {
    setBusy(true);
    try {
      const res = await fetch(`/api/jobs/${jobId}/page-fit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: notice.pageFit === "whole" ? "cover" : "whole", trimW: trimWidthMm, trimH: trimHeightMm }),
      });
      const data = await res.json();
      if (res.ok) {
        setNotice((prev) => ({ ...(prev || {}), pageFit: data.mode }));
        onFitWhole?.();
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-amber-500/40 bg-amber-50/40 dark:bg-amber-500/5 p-4" data-testid="section-cover-crop-notice">
      <h4 className="text-sm font-bold text-foreground">What gets trimmed to fill the page</h4>
      <p className="mt-1 text-sm text-foreground" data-testid="text-cover-crop-summary">{notice.summary}</p>
      {notice.warning ? (
        <p className="mt-2 text-sm font-semibold text-red-700 dark:text-red-400" data-testid="text-cover-crop-warning">
          {notice.warning}
        </p>
      ) : null}
      {notice.previewUrl ? (
        <img
          src={notice.previewUrl}
          alt="Shaded area that will be trimmed off"
          className="mt-3 max-h-72 w-full rounded-lg object-contain bg-neutral-900"
          data-testid="img-cover-crop-notice"
        />
      ) : null}
      <p className="mt-2 text-[11px] text-muted-foreground">
        The shaded area is cut away so the picture fills the page. The red line is the cut. The green line is the safe zone.
      </p>
      <button
        type="button"
        disabled={busy}
        onClick={() => void chooseWhole()}
        data-testid="button-fit-whole-artwork"
        className="mt-3 rounded-md border border-sky-600 bg-sky-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-60"
      >
        {notice.pageFit === "whole" ? "Fill the page instead" : "Fit whole artwork"}
      </button>
      {notice.pageFit === "whole" ? (
        <p className="mt-2 text-sm text-foreground" data-testid="text-fit-whole-artwork">
          The whole artwork will be kept. The gaps are filled by extending the background or a colour border, so there is no white frame.
        </p>
      ) : null}
    </div>
  );
}
