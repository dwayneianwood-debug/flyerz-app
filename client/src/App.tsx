import { Switch, Route } from "wouter";
import { queryClient } from "./lib/queryClient";
import { QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import Dashboard from "@/pages/dashboard";
import JobDetails from "@/pages/job-details";
import ManualCrop from "@/pages/manual-crop";
import SafeMarginShrink from "@/pages/safe-margin-shrink";
import GlitchyAdmin from "@/pages/glitchy-admin";
import ArProof from "@/pages/ar-proof";
import DashboardRules from "@/pages/dashboard-rules";
import GlitchyWidget from "@/components/glitchy-widget";
import { useMemo } from "react";
import { BetaContext, isBetaMode } from "@/lib/beta-flag";

function Router() {
  return (
    <Switch>
      <Route path="/dashboard/rules" component={DashboardRules}/>
      <Route path="/" component={Dashboard}/>
      <Route path="/job/:id" component={JobDetails}/>
      <Route path="/crop" component={ManualCrop}/>
      <Route path="/shrink" component={SafeMarginShrink}/>
      <Route path="/glitchy-admin" component={GlitchyAdmin}/>
      <Route path="/ar-proof/:jobId" component={ArProof}/>
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  const beta = useMemo(() => isBetaMode(), []);
  return (
    <BetaContext.Provider value={beta}>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <Toaster />
          <Router />
          <GlitchyWidget />
        </TooltipProvider>
      </QueryClientProvider>
    </BetaContext.Provider>
  );
}

export default App;
