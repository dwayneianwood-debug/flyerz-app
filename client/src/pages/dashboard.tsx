import { Layout } from "@/components/layout";
import { FileUpload } from "@/components/file-upload";
import { motion } from "framer-motion";
import { Ruler, Upload, Eye, Download } from "lucide-react";

const STEPS = [
  { num: 1, label: "Set Size", icon: Ruler, color: "text-primary" },
  { num: 2, label: "Upload & Fix", icon: Upload, color: "text-primary" },
  { num: 3, label: "Review", icon: Eye, color: "text-muted-foreground" },
  { num: 4, label: "Download", icon: Download, color: "text-muted-foreground" },
];

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
            Choose your size, drop your file, and we handle the rest. Three steps to print-ready artwork.
          </p>
        </div>

        <div className="mb-8" data-testid="wizard-steps-preview">
          <div className="flex items-center justify-center gap-0 px-4 sm:px-16">
            {STEPS.map((step, idx) => {
              const StepIcon = step.icon;
              const isActive = step.num <= 2;
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
                    <span className={`text-[11px] font-semibold mt-1.5 ${isActive ? 'text-primary' : 'text-muted-foreground'}`}>
                      {step.label}
                    </span>
                  </div>
                  {idx < STEPS.length - 1 && (
                    <div className={`h-0.5 flex-1 mx-1 mt-[-16px] rounded-full ${
                      idx < 1 ? 'bg-primary/40' : 'bg-border'
                    }`} />
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
