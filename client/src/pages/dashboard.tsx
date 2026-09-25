import { Layout } from "@/components/layout";
import { FileUpload } from "@/components/file-upload";
import { HOME_WIZARD_STEPS } from "@/lib/home-wizard-steps";
import { motion } from "framer-motion";
import { Crop, Ruler, Upload } from "lucide-react";

const STEP_ICONS = [Upload, Ruler, Crop];

export default function Dashboard() {
  return (
    <Layout>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: "easeOut" }}
        className="max-w-4xl mx-auto"
      >
        <div className="text-center mb-8 mt-6">
          <p className="text-xs uppercase tracking-[0.25em] text-primary font-semibold mb-3" data-testid="text-brand-label">Artwork Intelligence</p>
          <h1 className="text-4xl sm:text-5xl font-extrabold font-display tracking-tight text-foreground mb-4" data-testid="text-hero-title">
            Flawless prints, <br className="sm:hidden" />
            <span className="gradient-text-animated">every single time.</span>
          </h1>
          <p className="text-base text-muted-foreground max-w-xl mx-auto font-medium" data-testid="text-hero-subtitle">
            Upload your artwork, choose the size, then crop and submit. Three steps to print-ready artwork.
          </p>
        </div>

        <div className="mb-8" data-testid="wizard-steps-preview">
          <div className="flex items-center justify-center gap-0 px-4 sm:px-16">
            {HOME_WIZARD_STEPS.map((step, idx) => {
              const StepIcon = STEP_ICONS[idx] || Upload;
              const isActive = step.num === 1;
              return (
                <div key={step.num} className="flex items-center flex-1">
                  <div className="flex flex-col items-center flex-1">
                    <motion.div
                      initial={{ scale: 0.8, opacity: 0 }}
                      animate={{ scale: 1, opacity: 1 }}
                      transition={{ delay: idx * 0.1, duration: 0.3 }}
                      className={`w-10 h-10 rounded-full flex items-center justify-center transition-all ${
                        isActive
                          ? 'bg-primary text-white shadow-lg shadow-primary/30 pulse-ring'
                          : 'bg-muted text-muted-foreground'
                      }`}
                    >
                      <StepIcon className="w-4 h-4" />
                    </motion.div>
                    <span className={`text-[11px] font-semibold mt-1.5 text-center ${isActive ? 'text-primary' : 'text-muted-foreground'}`}>
                      {step.label}
                    </span>
                  </div>
                  {idx < HOME_WIZARD_STEPS.length - 1 && (
                    <div className="h-0.5 flex-1 mx-1 mt-[-16px] rounded-full bg-border" />
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <div className="space-y-6">
          <FileUpload />
        </div>
      </motion.div>
    </Layout>
  );
}
