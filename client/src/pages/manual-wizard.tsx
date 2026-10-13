import React, { type ReactNode } from "react";
import { HOME_WIZARD_STEPS } from "@/lib/home-wizard-steps";
import { Crop, Ruler, Upload } from "lucide-react";

const STEP_ICONS = [Upload, Ruler, Crop];

/** The existing upload, size, and crop steps, shown from the manual tab. */
export function ManualWizard(props: { children: ReactNode }) {
  return (
    <div data-testid="panel-manual-artwork">
      <h1 className="text-3xl font-extrabold font-display tracking-tight mb-2">Fix my artwork manually</h1>
      <p className="text-sm text-muted-foreground mb-6">Upload your artwork, choose the size, then crop and submit.</p>
      <div className="mb-8" data-testid="wizard-steps-preview">
        <div className="flex items-center justify-center gap-0 px-2 sm:px-8">
          {HOME_WIZARD_STEPS.map((step, idx) => {
            const StepIcon = STEP_ICONS[idx] || Upload;
            const isActive = step.num === 1;
            return (
              <div key={step.num} className="flex items-center flex-1">
                <div className="flex flex-col items-center flex-1">
                  <div className={`w-10 h-10 rounded-full flex items-center justify-center ${isActive ? "bg-primary text-white" : "bg-muted text-muted-foreground"}`}>
                    <StepIcon className="w-4 h-4" />
                  </div>
                  <span className={`text-[11px] font-semibold mt-1.5 text-center ${isActive ? "text-primary" : "text-muted-foreground"}`}>
                    {step.label}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {props.children}
    </div>
  );
}
