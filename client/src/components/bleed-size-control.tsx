import { BLEED_PRESETS_MM, normalizeBleedMm } from "@shared/bleed-size";

export function BleedSizeControl({
  value,
  disabled,
  onChange,
}: {
  value: number;
  disabled?: boolean;
  onChange: (mm: number) => void;
}) {
  const current = normalizeBleedMm(value);
  const preset = (BLEED_PRESETS_MM as readonly number[]).includes(current);

  return (
    <div className="mt-3" data-testid="bleed-size-control">
      <p className="text-xs font-semibold text-foreground mb-2">Bleed size</p>
      <div className="flex flex-wrap items-center gap-2">
        {BLEED_PRESETS_MM.map((mm) => (
          <button
            key={mm}
            type="button"
            disabled={disabled}
            aria-pressed={current === mm}
            data-testid={`button-bleed-${mm}`}
            onClick={() => onChange(mm)}
            className={`rounded-md px-3 py-1.5 text-sm font-semibold border ${
              current === mm
                ? "bg-primary text-white border-primary"
                : "bg-white/80 dark:bg-background/40 border-border text-foreground"
            }`}
          >
            {mm} mm{mm === 5 ? " (usual)" : ""}
          </button>
        ))}
        <label className="flex items-center gap-2 text-sm text-foreground">
          Custom
          <input
            type="number"
            min={1}
            max={25}
            step={0.5}
            disabled={disabled}
            value={preset ? "" : current}
            placeholder="mm"
            data-testid="input-bleed-custom"
            onChange={(event) => {
              const next = normalizeBleedMm(event.target.value);
              if (event.target.value !== "") onChange(next);
            }}
            className="w-20 rounded-md border border-border bg-white dark:bg-background px-2 py-1.5 text-sm"
          />
        </label>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground" data-testid="text-bleed-size">
        {current} mm of bleed on every side. This is used for the cut-line preview, the press file, and the health report.
      </p>
    </div>
  );
}
