import { useState, useEffect } from "react";
import { useLocation } from "wouter";
import { ArrowLeft } from "lucide-react";

interface GlitchyReport {
  timestamp: string;
  message: string;
  page: string;
}

export default function GlitchyAdmin() {
  const [reports, setReports] = useState<GlitchyReport[]>([]);
  const [, navigate] = useLocation();

  async function load() {
    const res = await fetch("/api/glitchy-admin-data");
    const data = await res.json();
    setReports(data);
  }

  async function fix(ts: string) {
    await fetch("/api/glitchy-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timestamp: ts }),
    });
    load();
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div style={{ fontFamily: "sans-serif", background: "#f4f7f6", minHeight: "100vh", padding: 40 }}>
      <div
        data-testid="glitchy-admin-card"
        style={{
          background: "white",
          padding: 25,
          borderRadius: 15,
          boxShadow: "0 4px 10px rgba(0,0,0,0.05)",
          maxWidth: 900,
          margin: "auto",
        }}
      >
        <button
          data-testid="button-back-dashboard"
          onClick={() => navigate("/")}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            background: "none",
            border: "none",
            color: "#6c5ce7",
            cursor: "pointer",
            fontSize: 14,
            marginBottom: 10,
            padding: 0,
          }}
        >
          <ArrowLeft size={16} /> Back to Dashboard
        </button>
        <h1
          data-testid="text-glitchy-title"
          style={{ color: "#6c5ce7", borderBottom: "2px solid #eee", paddingBottom: 10 }}
        >
          👨‍🔬 Glitchy's Lab Report
        </h1>
        <p>Analyze these reports to make your app better!</p>
        <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 20 }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", padding: 15, color: "#666" }}>Time</th>
              <th style={{ textAlign: "left", padding: 15, color: "#666" }}>Feedback</th>
              <th style={{ textAlign: "left", padding: 15, color: "#666" }}>Location</th>
              <th style={{ textAlign: "left", padding: 15, color: "#666" }}>Action</th>
            </tr>
          </thead>
          <tbody data-testid="glitchy-report-list">
            {reports.length === 0 && (
              <tr>
                <td colSpan={4} style={{ padding: 15, textAlign: "center", color: "#999" }}>
                  No reports yet — Glitchy is happy! 🎉
                </td>
              </tr>
            )}
            {reports.map((r, i) => (
              <tr key={r.timestamp + i} data-testid={`glitchy-report-row-${i}`}>
                <td style={{ padding: 15, borderBottom: "1px solid #eee", fontSize: 12, color: "#999" }}>
                  {r.timestamp}
                </td>
                <td style={{ padding: 15, borderBottom: "1px solid #eee" }}>
                  <strong>"{r.message}"</strong>
                </td>
                <td style={{ padding: 15, borderBottom: "1px solid #eee" }}>
                  <code style={{ background: "#eee", padding: 3 }}>{r.page}</code>
                </td>
                <td style={{ padding: 15, borderBottom: "1px solid #eee" }}>
                  <button
                    data-testid={`button-eat-bug-${i}`}
                    onClick={() => fix(r.timestamp)}
                    style={{
                      background: "#55efc4",
                      color: "#00b894",
                      border: "none",
                      padding: "10px 15px",
                      borderRadius: 8,
                      cursor: "pointer",
                      fontWeight: "bold",
                      transition: "0.2s",
                    }}
                    onMouseEnter={(e) => {
                      (e.target as HTMLButtonElement).style.background = "#00b894";
                      (e.target as HTMLButtonElement).style.color = "white";
                    }}
                    onMouseLeave={(e) => {
                      (e.target as HTMLButtonElement).style.background = "#55efc4";
                      (e.target as HTMLButtonElement).style.color = "#00b894";
                    }}
                  >
                    Eat Bug 🐜
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
