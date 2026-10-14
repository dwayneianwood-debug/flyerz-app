import { useState } from "react";
import { Layout } from "@/components/layout";
import { FileUpload } from "@/components/file-upload";
import { JobList } from "@/components/job-list";
import { ManualWizard } from "./manual-wizard";
import { FlowTabs, type FlowTab } from "./print-flow-view";
import { motion } from "framer-motion";
import { useLocation } from "wouter";

export default function Dashboard() {
  const [, setLocation] = useLocation();
  const [tab, setTab] = useState<FlowTab>("manual");
  return (
    <Layout>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: "easeOut" }}
        className="max-w-4xl mx-auto"
      >
        <div className="mt-6">
          <FlowTabs
            active={tab}
            onReady={() => setLocation("/print-ready")}
            onManual={() => setTab("manual")}
          />
        </div>
        <div className="text-center mb-8">
          <p className="text-xs uppercase tracking-[0.25em] text-primary font-semibold mb-3" data-testid="text-brand-label">Artwork Intelligence</p>
          <h1 className="text-4xl sm:text-5xl font-extrabold font-display tracking-tight text-foreground mb-4" data-testid="text-hero-title">
            Flawless prints, <br className="sm:hidden" />
            <span className="gradient-text-animated">every single time.</span>
          </h1>
          <p className="text-base text-muted-foreground max-w-xl mx-auto font-medium" data-testid="text-hero-subtitle">
            Upload your artwork, choose the size, then crop and submit. Three steps to print-ready artwork.
          </p>
        </div>

        <div className="space-y-6">
          <ManualWizard>
            <FileUpload />
          </ManualWizard>
          <JobList />
        </div>
      </motion.div>
    </Layout>
  );
}
