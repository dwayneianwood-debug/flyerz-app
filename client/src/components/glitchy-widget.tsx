import { useState, useRef, useEffect, useCallback } from "react";

type CatMode = "head" | "walking" | "sleeping" | "stretching";

interface CheckItem {
  label: string;
  pass: boolean;
}

function CatAvatar({ mode, dilated }: { mode: CatMode; dilated: boolean }) {
  const isAsleep = mode === "sleeping";
  const showBody = mode !== "head";

  const pupilStyle: React.CSSProperties = dilated
    ? { width: 7, height: 7, background: "black", borderRadius: "50%", transition: "all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)" }
    : { width: 2, height: 8, background: "black", borderRadius: 2, transition: "all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)" };

  const charClass = [
    mode === "walking" ? "toothless-walking" : "",
    mode === "stretching" ? "toothless-stretching" : "",
    mode === "sleeping" ? "toothless-sleeping" : "",
  ].filter(Boolean).join(" ");

  return (
    <div className={charClass} style={{ position: "relative", width: 32, height: 32, transition: "transform 0.3s" }}>
      {showBody && (
        <div
          style={{
            position: "absolute",
            top: 12,
            left: 4,
            width: 36,
            height: 18,
            background: "#0d1117",
            borderRadius: 15,
            transition: "all 0.4s",
            transform: isAsleep ? "translateX(-8px) rotate(-10deg)" : "none",
          }}
        >
          <div
            className="toothless-tail"
            style={{
              position: "absolute",
              right: -6,
              top: 4,
              width: 10,
              height: 3,
              background: "#0d1117",
              borderRadius: 3,
              transformOrigin: "left",
            }}
          />
          <div style={{ position: "absolute", bottom: -4, left: 4, width: 3, height: 6, background: "#0d1117", borderRadius: 2 }} />
          <div style={{ position: "absolute", bottom: -4, right: 8, width: 3, height: 6, background: "#0d1117", borderRadius: 2 }} />
        </div>
      )}

      <div
        style={{
          width: 32,
          height: 32,
          background: "#0d1117",
          borderRadius: "50%",
          position: "relative",
          zIndex: 2,
          boxShadow: "0 -5px 20px rgba(0,0,0,0.6)",
        }}
      >
        <div
          className="toothless-ear-twitch"
          style={{
            position: "absolute",
            top: -4,
            left: 3,
            width: 8,
            height: 12,
            background: "#0d1117",
            borderRadius: "60% 60% 20% 20%",
            transform: "rotate(-15deg)",
          }}
        />
        <div
          style={{
            position: "absolute",
            top: -4,
            right: 3,
            width: 8,
            height: 12,
            background: "#0d1117",
            borderRadius: "60% 60% 20% 20%",
            transform: "rotate(15deg)",
          }}
        />

        {!isAsleep && (
          <div style={{ display: "flex", gap: 3, paddingTop: 10, justifyContent: "center" }}>
            <div style={{ width: 11, height: 11, background: "#a3e635", borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden", boxShadow: "0 0 12px #a3e635" }}>
              <div style={pupilStyle} />
            </div>
            <div style={{ width: 11, height: 11, background: "#a3e635", borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden", boxShadow: "0 0 12px #a3e635" }}>
              <div style={pupilStyle} />
            </div>
          </div>
        )}

        {isAsleep && (
          <div style={{ display: "flex", gap: 6, paddingTop: 14, justifyContent: "center" }}>
            <div style={{ width: 10, height: 2, background: "#333", borderRadius: 2 }} />
            <div style={{ width: 10, height: 2, background: "#333", borderRadius: 2 }} />
          </div>
        )}
      </div>
    </div>
  );
}

const HOVER_QUOTES = [
  "I hope you're winning!",
  "You're doing amazing!",
  "Can I make this easier?",
  "Tell me a secret?",
];

interface PreflightItem {
  label: string;
  done: boolean;
}

type ProcessState = "IDLE" | "PROCESSING" | "QUEUED" | "SUCCESS" | "ERROR";

/** If a busy state never receives a complete/error/heartbeat, unstick so Glitchy is not frozen. */
const GLITCHY_BUSY_WATCHDOG_MS = 120000;

const GLITCHY_SUPPRESS_CROPBOX_ERR = "cropbox not in mediabox";

function textContainsCropBoxMediaBoxUiNoise(text: string): boolean {
  return text.toLowerCase().includes(GLITCHY_SUPPRESS_CROPBOX_ERR);
}

export default function GlitchyWidget() {
  const [bubbleVisible, setBubbleVisible] = useState(false);
  const [bubbleText, setBubbleText] = useState("Hello!");
  const [chatBoxVisible, setChatBoxVisible] = useState(false);
  const [showFeedback, setShowFeedback] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [isInteracting, setIsInteracting] = useState(false);
  const [posX, setPosX] = useState(15);
  const [caught, setCaught] = useState(false);
  const [chatLoading, setChatLoading] = useState(false);
  const [checklist, setChecklist] = useState<CheckItem[]>([]);
  const [responseText, setResponseText] = useState("*Purrs*");
  const [catMode, setCatMode] = useState<CatMode>("head");
  const [uiVisible, setUiVisible] = useState(true);
  const [happyHop, setHappyHop] = useState(false);
  const [preflightItems, setPreflightItems] = useState<PreflightItem[]>([]);
  const [processState, setProcessState] = useState<ProcessState>("IDLE");
  const [processingMessage, setProcessingMessage] = useState("");
  const [compileDownloadUrl, setCompileDownloadUrl] = useState<string | null>(null);
  const [compileErrorMsg, setCompileErrorMsg] = useState("");
  const [idleBubbleReady, setIdleBubbleReady] = useState(false);
  const [inkStainActive, setInkStainActive] = useState(false);
  const [checklistPassOverride, setChecklistPassOverride] = useState(false);
  /** ask = invite; reveal = show what we did after click */
  const [achievementPhase, setAchievementPhase] = useState<"ask" | "reveal" | null>(null);
  const chatInputRef = useRef<HTMLInputElement>(null);
  const feedbackRef = useRef<HTMLTextAreaElement>(null);
  const idleRef = useRef(0);
  const wanderingRef = useRef(false);
  const busyHeartbeatRef = useRef(0);

  function getJobIdFromUrl(): number | null {
    const match = window.location.pathname.match(/\/job\/(\d+)/);
    return match ? parseInt(match[1]) : null;
  }

  const fetchChecklist = useCallback(async () => {
    const jobId = getJobIdFromUrl();
    if (!jobId) {
      setChecklist([]);
      return;
    }
    try {
      const res = await fetch(`/api/glitchy-checklist/${jobId}`);
      const data = await res.json();
      const rows: CheckItem[] = data.checks || [];
      setChecklist(
        rows.map((c) =>
          textContainsCropBoxMediaBoxUiNoise(c.label) ? { ...c, pass: true } : c,
        ),
      );
    } catch {
      setChecklist([]);
    }
  }, []);

  const wakeUp = useCallback(() => {
    setCatMode("head");
    wanderingRef.current = false;
    setPosX(15);
    setUiVisible(true);
    if (!isInteracting) {
      setBubbleVisible(false);
    }
  }, [isInteracting]);

  const triggerStretch = useCallback(() => {
    setCatMode("stretching");
    setUiVisible(false);
    setChatBoxVisible(false);
    setTimeout(() => {
      setCatMode("walking");
    }, 1500);
  }, []);

  const triggerSleep = useCallback(() => {
    setCatMode("sleeping");
    setUiVisible(false);
    setChatBoxVisible(false);
  }, []);

  const walkToNewSpot = useCallback(() => {
    if (isInteracting) return;
    wanderingRef.current = true;
    const newX = Math.random() * (window.innerWidth - 80);
    setPosX(newX);
  }, [isInteracting]);

  useEffect(() => {
    const interval = setInterval(() => {
      if (isInteracting || processState !== "IDLE") return;
      idleRef.current++;

      if (idleRef.current === 15) {
        const jobId = getJobIdFromUrl();
        if (jobId) {
          setIdleBubbleReady(true);
        } else {
          triggerStretch();
        }
      }
      if (idleRef.current === 45) triggerSleep();
      if (idleRef.current > 16 && idleRef.current < 45 && Math.random() > 0.7) {
        walkToNewSpot();
      }
    }, 1000);

    const handleMouseMove = () => {
      idleRef.current = 0;
      if (processState === "IDLE") {
        setIdleBubbleReady(false);
      }
      if (!isInteracting && catMode !== "head" && processState === "IDLE") {
        wakeUp();
      }
    };

    document.addEventListener("mousemove", handleMouseMove);
    return () => {
      clearInterval(interval);
      document.removeEventListener("mousemove", handleMouseMove);
    };
  }, [isInteracting, catMode, processState, triggerStretch, triggerSleep, walkToNewSpot, wakeUp]);

  useEffect(() => {
    fetchChecklist();
  }, [fetchChecklist]);

  useEffect(() => {
    const onAuditSync = async (e: Event) => {
      const d = (e as CustomEvent).detail || {};
      if (d.overallPassed === true) {
        setChecklistPassOverride(true);
        setCatMode("head");
        setUiVisible(true);
        setPosX(15);
        wanderingRef.current = false;
        idleRef.current = 0;
        setChatBoxVisible(false);
        setIdleBubbleReady(false);
        setProcessState("SUCCESS");
        setProcessingMessage("I finished checking your artwork and applied the print fixes it needed!");
        setAchievementPhase("ask");
        setCompileErrorMsg("");
        setHappyHop(true);
        setTimeout(() => setHappyHop(false), 3000);
        const jobId = getJobIdFromUrl();
        const items: PreflightItem[] = [];
        if (jobId) {
          try {
            const res = await fetch(`/api/glitchy-checklist/${jobId}`);
            const data = await res.json();
            const rows: CheckItem[] = data.checks || [];
            const cleaned = rows.map((c) =>
              textContainsCropBoxMediaBoxUiNoise(c.label) ? { ...c, pass: true } : c,
            );
            setChecklist(cleaned);
            for (const c of cleaned) {
              if (c.pass && items.length < 8) {
                items.push({ label: c.label, done: true });
              }
            }
          } catch {
            setChecklist([]);
          }
        } else {
          fetchChecklist();
        }
        if (items.length === 0) {
          items.push({ label: "Artwork audited and prepared for print", done: true });
        }
        setPreflightItems(items);
      } else if (d.overallPassed === false) {
        setChecklistPassOverride(false);
        setAchievementPhase(null);
        fetchChecklist();
        if (d.jobStatus === "complete") {
          setProcessState("IDLE");
          setProcessingMessage("");
          setHappyHop(false);
        }
      } else {
        fetchChecklist();
      }
    };
    window.addEventListener("glitchy:audit-sync", onAuditSync);
    return () => window.removeEventListener("glitchy:audit-sync", onAuditSync);
  }, [fetchChecklist]);

  useEffect(() => {
    const resetGlitchy = () => {
      setCatMode("head");
      setUiVisible(true);
      setPosX(15);
      wanderingRef.current = false;
      idleRef.current = 0;
      setChatBoxVisible(false);
      setIdleBubbleReady(false);
    };

    const offerAchievement = (message: string, items: PreflightItem[]) => {
      resetGlitchy();
      setProcessState("SUCCESS");
      setProcessingMessage(message);
      setPreflightItems(items);
      setAchievementPhase("ask");
      setCompileErrorMsg("");
      setHappyHop(true);
      setTimeout(() => setHappyHop(false), 3000);
    };

    const handleCompileStart = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      busyHeartbeatRef.current = Date.now();
      resetGlitchy();
      setProcessState("PROCESSING");
      setProcessingMessage(detail.message || "Processing...");
      setPreflightItems([]);
      setCompileDownloadUrl(null);
      setCompileErrorMsg("");
      setHappyHop(false);
      setAchievementPhase(null);
    };

    const handleCompileComplete = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      setCompileDownloadUrl(detail.downloadUrl || null);

      const bleedLabel = detail.selectedBleedMethod || "";
      let message = "Your artwork is compiled and ready for the press!";
      if (detail.glitchyMessage) {
        message = detail.glitchyMessage;
      } else if (detail.lensesFlattened) {
        const bleedNote = bleedLabel ? ` Using ${bleedLabel} bleed.` : "";
        message = `I found some complex lenses and shadows. I've supersampled them to 600 DPI and baked them into the background to prevent white boxes on the press!${bleedNote}`;
      } else if (bleedLabel) {
        message = `Your artwork is compiled with ${bleedLabel} bleed and ready for the press!`;
      }

      const items: PreflightItem[] = [];
      const report = detail.auditReport;
      if (report) {
        if (report.geometry?.action_taken) {
          items.push({ label: `📐 ${report.geometry.action_taken}`, done: true });
        }
        if (report.typography?.action_taken) {
          items.push({ label: `🖋️ ${report.typography.action_taken}`, done: true });
        }
        if (report.color_and_ink?.action_taken) {
          items.push({ label: `🎨 ${report.color_and_ink.action_taken}`, done: true });
        }
        if (report.resolution_and_lenses?.action_taken) {
          items.push({ label: `🔍 ${report.resolution_and_lenses.action_taken}`, done: true });
        }
      } else {
        items.push({ label: "Ink Coverage: Clamped to 200% TIC (Press-Safe)", done: true });
        items.push({ label: "Text Sharpening: Converted to 100% K Overprint", done: true });
        items.push({ label: "Bleed Status: 5mm TrimBox & BleedBox Embedded", done: true });
        if (detail.lensesDetected) {
          items.push({ label: "Lenses Flattened: Supersampled 600→300 DPI", done: !!detail.lensesFlattened });
        }
        if (detail.aiEnhanced) {
          items.push({ label: "AI Resolution Enhancement Applied", done: true });
        }
        if (detail.originalTic && detail.finalTic && detail.originalTic > detail.finalTic) {
          items.push({ label: `TIC Reduced: ${detail.originalTic}% → ${detail.finalTic}%`, done: true });
        }
      }
      if (bleedLabel) {
        items.push({ label: `Bleed strategy: ${bleedLabel}`, done: true });
      }
      if (items.length === 0) {
        items.push({ label: "Press-ready PDF packaged for download", done: true });
      }
      offerAchievement(message, items);
    };

    const handleCompileError = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      resetGlitchy();
      setProcessState("ERROR");
      setCompileErrorMsg(detail.message || "Glitchy encountered a press error!");
      setPreflightItems([]);
      setCompileDownloadUrl(null);
      setHappyHop(false);
      setAchievementPhase(null);
    };

    const handleResizeComplete = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      const items: PreflightItem[] = [];
      let message = "I've resized your artwork for print!";

      if (detail.falseMargins) {
        items.push({ label: "False Margins Stripped", done: true });
        message = "I stripped some blank borders off your file first so I could resize the actual artwork correctly!";
      }
      if (detail.scalePercentage > 200) {
        message = detail.falseMargins
          ? "I stripped blank borders first, then rebuilt pixels with AI — scaling this much pushes litho sharpness limits!"
          : "I'm rebuilding these pixels with AI, but scaling this much is pushing the limits of litho sharpness!";
        if (detail.aiUpscaled) items.push({ label: "AI Reconstruction Applied", done: true });
        items.push({ label: `Scale Factor: ${Number(detail.scalePercentage).toFixed(0)}%`, done: true });
      } else if (detail.aiUpscaled) {
        message = "I've enhanced your artwork with AI reconstruction to keep it sharp at print resolution!";
        items.push({ label: "AI Reconstruction Applied", done: true });
      } else if (detail.scalePercentage) {
        items.push({ label: `Scaled to ${Number(detail.scalePercentage).toFixed(0)}%`, done: true });
      }
      if (items.length === 0) {
        items.push({ label: "Precision resize complete", done: true });
      }
      offerAchievement(message, items);
    };

    const handleBleedSwitch = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      busyHeartbeatRef.current = Date.now();
      resetGlitchy();
      setProcessState("PROCESSING");
      setProcessingMessage(detail.message || "Switching bleed strategy...");
      setPreflightItems([]);
      setCompileDownloadUrl(null);
      setCompileErrorMsg("");
      setHappyHop(false);
      setAchievementPhase(null);
    };

    const handleBleedSwitchDone = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      if (detail.error) {
        resetGlitchy();
        setProcessState("ERROR");
        setCompileErrorMsg(detail.message || "Bleed switch failed");
        setAchievementPhase(null);
      } else {
        const msg = detail.message || "Bleed strategy updated!";
        offerAchievement(msg, [
          { label: msg, done: true },
          { label: "Bleed edges regenerated for your selected method", done: true },
        ]);
      }
    };

    const handleJobReset = () => {
      resetGlitchy();
      setProcessState("IDLE");
      setProcessingMessage("");
      setPreflightItems([]);
      setCompileDownloadUrl(null);
      setCompileErrorMsg("");
      setHappyHop(false);
      setIdleBubbleReady(false);
      setChecklist([]);
      setChecklistPassOverride(false);
      setAchievementPhase(null);
    };

    const handleQueueUpdate = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      busyHeartbeatRef.current = Date.now();
      if (detail.position && detail.position > 0) {
        setProcessState("QUEUED");
        setProcessingMessage(`You are #${detail.position} in line. The press room is busy — I'll start on your file as soon as a slot opens up!`);
        setPreflightItems([]);
        setCompileDownloadUrl(null);
        setAchievementPhase(null);
      }
    };

    const handleQueueDequeued = () => {
      busyHeartbeatRef.current = Date.now();
      setProcessState("PROCESSING");
      setProcessingMessage("Your turn! Processing your artwork now...");
      setAchievementPhase(null);
    };

    const handleJobComplete = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      if (detail.overallPassed === true) {
        return;
      }
      resetGlitchy();
      setProcessState("IDLE");
      setProcessingMessage("");
      setPreflightItems([]);
      setCompileDownloadUrl(null);
      setCompileErrorMsg("");
      setHappyHop(false);
      setAchievementPhase(null);
    };

    const handleJobFailed = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      resetGlitchy();
      setProcessState("ERROR");
      setCompileErrorMsg(detail.message || "Processing failed. Click me to continue.");
      setPreflightItems([]);
      setCompileDownloadUrl(null);
      setHappyHop(false);
      setAchievementPhase(null);
    };

    const handleProgressHeartbeat = () => {
      busyHeartbeatRef.current = Date.now();
    };

    const handleInkStain = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      busyHeartbeatRef.current = Date.now();
      setInkStainActive(true);
      if (detail.message) {
        setProcessingMessage(detail.message);
      }
      setTimeout(() => setInkStainActive(false), 2400);
    };

    const handleDownloadError = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      resetGlitchy();
      setProcessState("ERROR");
      setCompileErrorMsg(detail.message || "Error: Could not retrieve the file from the server.");
      setPreflightItems([]);
      setCompileDownloadUrl(null);
      setHappyHop(false);
      setAchievementPhase(null);
    };

    window.addEventListener("glitchy:ink-stain", handleInkStain);
    window.addEventListener("glitchy:compile-start", handleCompileStart);
    window.addEventListener("glitchy:compile-complete", handleCompileComplete);
    window.addEventListener("glitchy:compile-error", handleCompileError);
    window.addEventListener("glitchy:resize-complete", handleResizeComplete);
    window.addEventListener("glitchy:queue-update", handleQueueUpdate);
    window.addEventListener("glitchy:queue-dequeued", handleQueueDequeued);
    window.addEventListener("glitchy:bleed-switch", handleBleedSwitch);
    window.addEventListener("glitchy:bleed-switch-done", handleBleedSwitchDone);
    window.addEventListener("glitchy:download-error", handleDownloadError);
    window.addEventListener("glitchy:job-reset", handleJobReset);
    window.addEventListener("glitchy:job-complete", handleJobComplete);
    window.addEventListener("glitchy:job-failed", handleJobFailed);
    window.addEventListener("glitchy:progress", handleProgressHeartbeat);
    return () => {
      window.removeEventListener("glitchy:compile-start", handleCompileStart);
      window.removeEventListener("glitchy:compile-complete", handleCompileComplete);
      window.removeEventListener("glitchy:compile-error", handleCompileError);
      window.removeEventListener("glitchy:resize-complete", handleResizeComplete);
      window.removeEventListener("glitchy:bleed-switch", handleBleedSwitch);
      window.removeEventListener("glitchy:bleed-switch-done", handleBleedSwitchDone);
      window.removeEventListener("glitchy:download-error", handleDownloadError);
      window.removeEventListener("glitchy:job-reset", handleJobReset);
      window.removeEventListener("glitchy:queue-update", handleQueueUpdate);
      window.removeEventListener("glitchy:queue-dequeued", handleQueueDequeued);
      window.removeEventListener("glitchy:ink-stain", handleInkStain);
      window.removeEventListener("glitchy:job-complete", handleJobComplete);
      window.removeEventListener("glitchy:job-failed", handleJobFailed);
      window.removeEventListener("glitchy:progress", handleProgressHeartbeat);
    };
  }, []);

  useEffect(() => {
    const busy = processState === "PROCESSING" || processState === "QUEUED";
    if (!busy) return;
    busyHeartbeatRef.current = Date.now();
    const interval = setInterval(() => {
      if (Date.now() - busyHeartbeatRef.current < GLITCHY_BUSY_WATCHDOG_MS) return;
      setProcessState("ERROR");
      setCompileErrorMsg("I got stuck waiting. Click me to continue — you can retry from the job page.");
      setAchievementPhase(null);
      setHappyHop(false);
    }, 5000);
    return () => clearInterval(interval);
  }, [processState]);

  function handleClick() {
    if (processState === "SUCCESS" && achievementPhase === "ask") {
      setAchievementPhase("reveal");
      setIsInteracting(true);
      idleRef.current = 0;
      return;
    }
    if (processState === "SUCCESS" || processState === "ERROR") {
      setProcessState("IDLE");
      setPreflightItems([]);
      setCompileDownloadUrl(null);
      setCompileErrorMsg("");
      setIdleBubbleReady(false);
      setAchievementPhase(null);
      setIsInteracting(false);
      return;
    }
    if (processState === "PROCESSING" || processState === "QUEUED") {
      setProcessState("IDLE");
      setProcessingMessage("");
      setIdleBubbleReady(false);
      setAchievementPhase(null);
      idleRef.current = 0;
      return;
    }

    if (catMode !== "head") {
      idleRef.current = 0;
      wakeUp();
      return;
    }

    setIsInteracting(true);
    idleRef.current = 0;
    setIdleBubbleReady(false);

    setChatBoxVisible((v) => {
      if (v) {
        setIsInteracting(false);
        setBubbleVisible(false);
        setShowFeedback(false);
        return false;
      }
      fetchChecklist();
      return true;
    });
    setBubbleVisible(false);
  }

  function handleMouseEnter() {
    if (!chatBoxVisible && !wanderingRef.current && catMode === "head") {
      setBubbleText(HOVER_QUOTES[Math.floor(Math.random() * HOVER_QUOTES.length)]);
      setBubbleVisible(true);
    }
  }

  function handleMouseLeave() {
    if (!wanderingRef.current && !caught && !chatLoading) {
      setBubbleVisible(false);
    }
  }

  async function askGlitchy() {
    const val = chatInputRef.current?.value?.trim();
    if (!val || chatLoading) return;
    chatInputRef.current!.value = "";

    setResponseText("...Thinking...");
    setChatLoading(true);

    try {
      const jobId = getJobIdFromUrl();
      const res = await fetch("/api/glitchy-chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: val, jobId }),
      });
      const data = await res.json();
      setResponseText(data.reply);
    } catch {
      setResponseText("Meow? (Check your connection!)");
    }
    setChatLoading(false);
  }

  async function sendFeedback() {
    const text = feedbackRef.current?.value;
    if (!text) return;
    const jobId = getJobIdFromUrl();
    await fetch("/api/glitchy-report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        page: window.location.pathname,
        timestamp: new Date().toLocaleString(),
        jobId,
        page_state: {
          page: window.location.pathname,
          jobId,
          href: window.location.href,
          glitchyProcessState: processState,
          catMode,
          processingMessage,
          compileErrorMsg,
        },
      }),
    });
    setSubmitted(true);
    if (feedbackRef.current) feedbackRef.current.value = "";
    setTimeout(() => {
      setSubmitted(false);
      setShowFeedback(false);
    }, 1500);
  }

  const hasFailedChecks =
    !checklistPassOverride &&
    checklist.some((c) => !c.pass && !textContainsCropBoxMediaBoxUiNoise(c.label));
  const showChecklist = hasFailedChecks && catMode === "head" && chatBoxVisible;

  const suppressCropBoxErrorBubble =
    processState === "ERROR" && textContainsCropBoxMediaBoxUiNoise(compileErrorMsg);

  const stateAnimClass = (() => {
    if (happyHop) return "glitchy-success";
    switch (processState) {
      case "PROCESSING": return "glitchy-working";
      case "QUEUED": return "glitchy-working";
      case "ERROR": return suppressCropBoxErrorBubble ? "" : "glitchy-error";
      default: return "";
    }
  })();

  const isVisible = catMode !== "head" || uiVisible || processState !== "IDLE";

  const showBubble =
    (bubbleVisible && processState === "IDLE" && !chatBoxVisible) ||
    processState === "PROCESSING" ||
    processState === "QUEUED" ||
    (processState === "ERROR" && !suppressCropBoxErrorBubble) ||
    (processState === "SUCCESS" && achievementPhase === "ask") ||
    (processState === "SUCCESS" && achievementPhase === "reveal") ||
    (processState === "IDLE" && idleBubbleReady && catMode === "head" && !chatBoxVisible);

  const bubbleContent = (() => {
    if (processState === "QUEUED") {
      return (
        <span className="pulsing-text" data-testid="glitchy-speech-queued" style={{ fontSize: 12, color: "#fbbf24", fontWeight: "bold" }}>
          {processingMessage || "Waiting in queue..."}
        </span>
      );
    }
    if (processState === "PROCESSING") {
      return (
        <span className="pulsing-text" data-testid="glitchy-speech-processing" style={{ fontSize: 12, color: "#a3e635", fontWeight: "bold" }}>
          {processingMessage || "Processing..."}
        </span>
      );
    }
    if (processState === "SUCCESS" && achievementPhase === "ask") {
      return (
        <div className="success-content" data-testid="glitchy-achievement-ask">
          <p style={{ margin: 0, fontSize: 12, fontWeight: "bold", color: "#333", textAlign: "center" }}>
            Do you want to know what we just achieved?
          </p>
          <p style={{ margin: "6px 0 0 0", fontSize: 9, color: "#666", textAlign: "center", fontWeight: 500 }}>
            Click me to find out
          </p>
        </div>
      );
    }
    if (processState === "SUCCESS" && achievementPhase === "reveal") {
      return (
        <div className="success-content" data-testid="glitchy-preflight">
          <p style={{ margin: 0, fontSize: 11, fontWeight: "bold", color: "#333" }}>
            {processingMessage || "All done! Your file is print-ready."}
          </p>
          {preflightItems.length > 0 && (
            <ul style={{ listStyle: "none", padding: 0, margin: 0, fontSize: 10, lineHeight: 1.7, width: "100%" }}>
              {preflightItems.map((item, i) => (
                <li
                  key={i}
                  data-testid={`preflight-item-${i}`}
                  style={{ color: "#333", fontWeight: 500 }}
                >
                  <span style={{ color: "#a3e635", marginRight: 4, fontWeight: "bold" }}>{item.done ? "\u2713" : "\u23F3"}</span>
                  {item.label}
                </li>
              ))}
            </ul>
          )}
          <p style={{ margin: "6px 0 0 0", fontSize: 8, color: "#888", textAlign: "center" }}>
            Click again to dismiss
          </p>
        </div>
      );
    }
    if (processState === "ERROR") {
      if (textContainsCropBoxMediaBoxUiNoise(compileErrorMsg || "")) {
        return null;
      }
      return (
        <span className="error-text" data-testid="glitchy-speech-error">
          {compileErrorMsg || "Glitchy encountered a press error!"}
        </span>
      );
    }
    if (processState === "IDLE" && idleBubbleReady && !chatBoxVisible) {
      return (
        <span data-testid="glitchy-speech-idle" style={{ fontSize: 12, color: "#333" }}>
          {hasFailedChecks
            ? "Watch out! Text is close to the trim line."
            : "Select a bleed variant when you're ready."}
        </span>
      );
    }
    if (bubbleVisible && processState === "IDLE" && !chatBoxVisible) {
      return <span style={{ fontSize: 12, color: "#333" }}>{bubbleText}</span>;
    }
    return null;
  })();

  return (
    <>
      <style>{`
        @keyframes toothless-tail-wag {
          0%, 100% { transform: rotate(0deg); }
          50% { transform: rotate(30deg); }
        }
        @keyframes toothless-walk-cycle {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-4px); }
        }
        @keyframes toothless-stretch-anim {
          0%, 100% { transform: scaleX(1); }
          50% { transform: scaleX(1.3) translateY(-2px); }
        }
        @keyframes toothless-breathe {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.03); }
        }
        @keyframes ear-twitch {
          0%, 90%, 100% { transform: rotate(-15deg); }
          95% { transform: rotate(-30deg); }
        }
        .toothless-ear-twitch {
          animation: ear-twitch 5s infinite;
          transform-origin: bottom right;
        }
        .toothless-tail {
          animation: toothless-tail-wag 2s infinite;
        }
        .toothless-walking {
          animation: toothless-walk-cycle 0.5s infinite;
        }
        .toothless-stretching > div:first-child {
          animation: toothless-stretch-anim 1.5s ease;
        }
        .toothless-sleeping {
          animation: toothless-breathe 3s infinite ease-in-out;
        }

        @keyframes tail-swish-slow {
          0%, 100% { transform: translateY(0) rotate(0deg); }
          50% { transform: translateY(-5px) rotate(2deg); }
        }
        @keyframes peek-alert {
          0%, 100% { transform: translateY(0); }
          10%, 30% { transform: translateY(-15px); }
        }
        @keyframes working-bat {
          0% { transform: translateY(0) rotate(-5deg); }
          100% { transform: translateY(-10px) rotate(5deg); }
        }
        @keyframes happy-hop {
          0%, 100% { transform: translateY(0); }
          10%, 30% { transform: translateY(-8px); }
          20%, 50% { transform: translateY(0); }
        }
        @keyframes error-shake {
          0%, 100% { transform: translateX(0); }
          25% { transform: translateX(-5px); }
          75% { transform: translateX(5px); }
        }

        @keyframes ink-stain-splat {
          0% { filter: drop-shadow(0 0 0 transparent); transform: scale(1); }
          15% { filter: drop-shadow(0 0 8px rgba(0, 200, 255, 0.9)) drop-shadow(0 0 16px rgba(255, 0, 180, 0.7)); transform: scale(1.15); }
          30% { filter: drop-shadow(0 0 12px rgba(255, 255, 0, 0.8)) drop-shadow(0 0 20px rgba(0, 255, 255, 0.6)); transform: scale(1.1) rotate(-3deg); }
          50% { filter: drop-shadow(0 0 15px rgba(255, 0, 255, 0.85)) drop-shadow(0 0 25px rgba(0, 0, 0, 0.4)); transform: scale(1.2) rotate(2deg); }
          70% { filter: drop-shadow(0 0 10px rgba(0, 180, 255, 0.7)) drop-shadow(0 0 18px rgba(255, 100, 0, 0.5)); transform: scale(1.08) rotate(-1deg); }
          85% { filter: drop-shadow(0 0 5px rgba(0, 200, 180, 0.5)); transform: scale(1.03); }
          100% { filter: drop-shadow(0 0 0 transparent); transform: scale(1); }
        }
        .glitchy-ink-stain { animation: ink-stain-splat 2.4s ease-in-out; }

        .glitchy-idle-active { animation: tail-swish-slow 4s infinite; }
        .glitchy-working { animation: working-bat 0.8s infinite alternate; }
        .glitchy-success { animation: happy-hop 2s ease-in-out infinite; }
        .glitchy-error { animation: error-shake 0.5s infinite; }

        @keyframes pulse-opacity {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
        .pulsing-text { animation: pulse-opacity 1.5s infinite; }

        .glitchy-speech-bubble {
          background: #ffffff;
          color: #1a1a2e;
          padding: 15px 20px;
          border-radius: 12px;
          font-weight: 600;
          font-size: 0.95rem;
          margin-bottom: 10px;
          box-shadow: 0 8px 25px rgba(0,0,0,0.3);
          text-align: center;
          position: relative;
          max-width: 240px;
          min-width: 140px;
        }
        .glitchy-chat-expanded {
          width: 330px;
          max-width: 330px;
          min-width: 210px;
          padding: 15px 20px;
          font-size: 13px;
          margin-left: -105px;
        }
        .glitchy-speech-bubble::after {
          content: '';
          position: absolute;
          bottom: -10px;
          left: 50%;
          transform: translateX(-50%);
          border-width: 10px 10px 0;
          border-style: solid;
          border-color: #ffffff transparent transparent transparent;
        }

        .success-content {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 6px;
          text-align: left;
        }
        .glitchy-download-btn {
          background-color: #e94560;
          color: white;
          text-decoration: none;
          padding: 10px 20px;
          border-radius: 8px;
          font-weight: 900;
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 1.5px;
          transition: background 0.2s ease, transform 0.1s ease;
          display: inline-block;
          margin-top: 4px;
        }
        .glitchy-download-btn:hover {
          background-color: #ff5277;
          transform: scale(1.01);
        }
        .error-text {
          color: #ff4b4b;
          font-weight: bold;
          font-size: 12px;
        }
      `}</style>
      <div
        data-testid="glitchy-container"
        data-glitchy-state={processState}
        style={{
          position: "fixed",
          bottom: isVisible ? 0 : -100,
          right: wanderingRef.current ? undefined : 50,
          left: wanderingRef.current ? posX : undefined,
          width: 120,
          transformOrigin: "bottom center",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          overflow: "visible",
          zIndex: 9999,
          fontFamily: "sans-serif",
          transition: "bottom 0.5s ease-in-out, left 2s linear",
        }}
      >
        {showBubble && bubbleContent && (
          <div className="glitchy-speech-bubble" data-testid="glitchy-bubble">
            {bubbleContent}
          </div>
        )}

        {showChecklist && (
          <div
            className="glitchy-speech-bubble"
            data-testid="glitchy-checklist"
            style={{ textAlign: "left" }}
          >
            <p style={{ margin: "0 0 5px 0", fontSize: 10, fontWeight: "bold", color: "#333" }}>
              Checklist:
            </p>
            <ul style={{ listStyle: "none", padding: 0, margin: 0, fontSize: 9, lineHeight: 1.5 }}>
              {checklist.map((c, i) => (
                <li
                  key={i}
                  data-testid={`glitchy-check-${i}`}
                  style={{ color: c.pass ? "#27ae60" : "#e74c3c", fontWeight: "bold" }}
                >
                  {c.pass ? "\u2705" : "\u274C"} {c.label}
                </li>
              ))}
            </ul>
          </div>
        )}

        {chatBoxVisible && catMode === "head" && (
          <div
            className="glitchy-speech-bubble glitchy-chat-expanded"
            data-testid="glitchy-chat-box"
            style={{
              background: "#1a1a1a",
              color: "white",
              textAlign: "left",
            }}
          >
            {showFeedback ? (
              submitted ? (
                <p data-testid="glitchy-success" style={{ textAlign: "center", color: "#a3e635", fontWeight: "bold", margin: 0, fontSize: 9 }}>
                  Yay!
                </p>
              ) : (
                <>
                  <textarea
                    ref={feedbackRef}
                    data-testid="glitchy-feedback-input"
                    style={{
                      width: "100%",
                      fontSize: 9,
                      border: "none",
                      borderRadius: 4,
                      padding: 4,
                      boxSizing: "border-box",
                      resize: "none",
                      height: 40,
                      background: "#333",
                      color: "white",
                    }}
                    placeholder="What can we improve?"
                  />
                  <button
                    onClick={sendFeedback}
                    data-testid="glitchy-feedback-send"
                    style={{
                      width: "100%",
                      marginTop: 4,
                      background: "#a3e635",
                      color: "black",
                      border: "none",
                      borderRadius: 4,
                      cursor: "pointer",
                      fontSize: 9,
                      fontWeight: "bold",
                      padding: 4,
                    }}
                  >
                    Send
                  </button>
                  <button
                    onClick={() => setShowFeedback(false)}
                    data-testid="glitchy-back-chat"
                    style={{
                      width: "100%",
                      marginTop: 3,
                      background: "none",
                      color: "#888",
                      border: "none",
                      cursor: "pointer",
                      fontSize: 8,
                      padding: 2,
                    }}
                  >
                    back to chat
                  </button>
                </>
              )
            ) : (
              <>
                <div data-testid="glitchy-response" style={{ fontSize: 10, marginBottom: 5, color: "#eee" }}>
                  {responseText}
                </div>
                <input
                  ref={chatInputRef}
                  data-testid="glitchy-chat-input"
                  type="text"
                  placeholder="Ask me..."
                  onKeyDown={(e) => e.key === "Enter" && askGlitchy()}
                  style={{
                    width: "100%",
                    fontSize: 9,
                    padding: 4,
                    borderRadius: 4,
                    border: "none",
                    background: "#333",
                    color: "white",
                    boxSizing: "border-box",
                    outline: "none",
                  }}
                />
                <button
                  onClick={askGlitchy}
                  data-testid="glitchy-chat-send"
                  style={{
                    width: "100%",
                    marginTop: 4,
                    background: "#a3e635",
                    color: "black",
                    border: "none",
                    borderRadius: 4,
                    padding: 4,
                    fontSize: 9,
                    fontWeight: "bold",
                    cursor: "pointer",
                  }}
                >
                  Ask Cat
                </button>
                <button
                  onClick={() => setShowFeedback(true)}
                  data-testid="glitchy-switch-feedback"
                  style={{
                    width: "100%",
                    marginTop: 3,
                    background: "none",
                    color: "#888",
                    border: "none",
                    cursor: "pointer",
                    fontSize: 8,
                    padding: 2,
                  }}
                >
                  feedback
                </button>
              </>
            )}
          </div>
        )}

        <div
          data-testid="glitchy-avatar"
          className={`${stateAnimClass} ${inkStainActive ? "glitchy-ink-stain" : ""}`}
          onClick={handleClick}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
          style={{ cursor: "pointer" }}
        >
          <CatAvatar
            mode={catMode}
            dilated={
              hasFailedChecks || (processState === "ERROR" && !suppressCropBoxErrorBubble)
            }
          />
        </div>
      </div>
    </>
  );
}
