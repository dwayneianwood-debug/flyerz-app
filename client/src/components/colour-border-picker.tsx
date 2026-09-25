import React, { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import {
  COLOUR_BORDER_PRESETS,
  type ColourBorderChoice,
  cmykToRgb,
  customFromCmyk,
  customFromRgb,
  presetChoice,
  rgbHex,
} from "@/lib/colour-border";

function Swatch({ r, g, b }: { r: number; g: number; b: number }) {
  return (
    <span
      className="inline-block h-4 w-4 shrink-0 rounded-sm border border-black/20"
      style={{ backgroundColor: rgbHex(r, g, b) }}
      aria-hidden
    />
  );
}

export function ColourBorderPicker({
  value,
  onChange,
  disabled,
}: {
  value: ColourBorderChoice;
  onChange: (next: ColourBorderChoice) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDoc = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const choose = (next: ColourBorderChoice) => {
    onChange(next);
    if (next.source !== "custom") setOpen(false);
  };

  return (
    <div className="space-y-2" ref={rootRef} data-testid="colour-border-picker">
      <p className="text-xs font-medium text-muted-foreground">Border colour</p>
      <div className="relative">
        <button
          type="button"
          disabled={disabled}
          onClick={() => setOpen((current) => !current)}
          className="flex w-full items-center gap-2 rounded-lg border-2 border-border/60 bg-white px-3 py-2.5 text-left text-sm font-medium text-foreground disabled:cursor-not-allowed disabled:opacity-50 dark:bg-background"
          data-testid="button-colour-border-menu"
        >
          <Swatch r={value.r} g={value.g} b={value.b} />
          <span className="flex-1 truncate" data-testid="text-colour-border-label">{value.label}</span>
          <ChevronDown className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
        {open && (
          <div className="absolute z-30 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-border/70 bg-background shadow-xl" data-testid="menu-colour-border">
            {COLOUR_BORDER_PRESETS.map((preset) => (
              <button
                key={preset.id}
                type="button"
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted/60"
                onClick={() => choose(presetChoice(preset.id))}
                data-testid={`option-colour-${preset.id}`}
              >
                <Swatch r={preset.r} g={preset.g} b={preset.b} />
                <span>{preset.label}</span>
              </button>
            ))}
            <button
              type="button"
              className="flex w-full items-center gap-2 border-t border-border/50 px-3 py-2 text-left text-sm hover:bg-muted/60"
              onClick={() => choose({ ...value, source: "edge", presetId: "edge", label: "Match artwork edge" })}
              data-testid="option-colour-edge"
            >
              <Swatch r={value.source === "edge" ? value.r : 180} g={value.source === "edge" ? value.g : 180} b={value.source === "edge" ? value.b : 180} />
              <span>Match artwork edge</span>
            </button>
            <button
              type="button"
              className="flex w-full items-center gap-2 border-t border-border/50 px-3 py-2 text-left text-sm hover:bg-muted/60"
              onClick={() => {
                onChange(value.source === "custom" ? value : customFromRgb(value.r, value.g, value.b));
                setOpen(false);
              }}
              data-testid="option-colour-custom"
            >
              <Swatch r={value.r} g={value.g} b={value.b} />
              <span>Custom...</span>
            </button>
          </div>
        )}
      </div>
      <p className="text-[11px] text-muted-foreground" data-testid="text-colour-border-cmyk">
        C{Math.round(value.c)} M{Math.round(value.m)} Y{Math.round(value.y)} K{Math.round(value.k)}
      </p>
      {value.source === "custom" && (
        <div className="space-y-2 rounded-lg border border-border/50 p-2" data-testid="panel-colour-custom">
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Pick
            <input
              type="color"
              value={rgbHex(value.r, value.g, value.b)}
              onChange={(event) => {
                const hex = event.target.value;
                const r = parseInt(hex.slice(1, 3), 16);
                const g = parseInt(hex.slice(3, 5), 16);
                const b = parseInt(hex.slice(5, 7), 16);
                onChange(customFromRgb(r, g, b));
              }}
              className="h-8 w-12 cursor-pointer rounded border bg-transparent"
              data-testid="input-colour-picker"
            />
          </label>
          <div className="grid grid-cols-4 gap-1">
            {(["c", "m", "y", "k"] as const).map((channel) => (
              <label key={channel} className="text-[10px] uppercase text-muted-foreground">
                {channel}
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={value[channel]}
                  onChange={(event) => {
                    const next = customFromCmyk(value.c, value.m, value.y, value.k);
                    next[channel] = Number(event.target.value);
                    const rgb = cmykToRgb(next.c, next.m, next.y, next.k);
                    onChange({ ...next, ...rgb, source: "custom", label: "Custom" });
                  }}
                  className="mt-0.5 h-8 w-full rounded border bg-background px-1 text-xs text-foreground"
                  data-testid={`input-colour-${channel}`}
                />
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
