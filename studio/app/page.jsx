"use client";

import { useEffect, useState } from "react";

const labels = { horizontal: "Horizontal", vertical: "Vertical", radio: "Radio cut" };
const files = { horizontal: "graduation-horizontal.mp4", vertical: "graduation-vertical.mp4", radio: "radio-cut.mp4" };

export default function Studio() {
  const [job, setJob] = useState(null);
  const [preview, setPreview] = useState(false);
  const [tab, setTab] = useState("horizontal");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const running = starting || job?.state === "running";
  const kind = preview ? "preview" : "production";
  const outputs = job?.outputs?.[kind];
  const source = outputs?.[tab];

  useEffect(() => {
    let mounted = true;
    async function refresh() {
      try {
        const response = await fetch("/api/jobs", { cache: "no-store" });
        if (!response.ok) throw new Error("The render service is unavailable.");
        const snapshot = await response.json();
        if (mounted) setJob(snapshot);
      } catch {
        if (mounted) setJob((old) => ({ ...old, state: "unavailable" }));
      }
    }
    refresh();
    const interval = setInterval(refresh, 1500);
    return () => { mounted = false; clearInterval(interval); };
  }, []);

  async function launch(action) {
    setError("");
    setStarting(true);
    setTab(action === "radio" ? "radio" : "horizontal");
    try {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, preview }),
      });
      const result = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = typeof result?.detail === "string" ? result.detail : null;
        throw new Error(detail ?? "Could not reach the render service. Check that FastAPI is running.");
      }
      setJob(result);
    } catch (problem) {
      setError(problem.message || "Could not start the render.");
    } finally {
      setStarting(false);
    }
  }

  const status = running ? "Rendering" : {
    done: "Render complete", error: "Render failed", unavailable: "Service unavailable",
  }[job?.state] ?? "Idle";

  return (
    <div className="shell">
      <header className="top">
        <span className="brand"><span aria-hidden="true" className="mark">▶</span> Graduation Film Studio</span>
        <span className="status" data-state={running ? "running" : job?.state} aria-live="polite">{status}</span>
      </header>
      <main>
        <section className="intro">
          <div>
            <h1>The Hands That<br className="desktop" /> Helped Me Grow</h1>
            <p>A graduation short with Amharic narration and English translation.</p>
          </div>
          <div className="actions">
            <label className="mode"><input type="checkbox" checked={preview} disabled={running}
              onChange={(event) => setPreview(event.target.checked)} /> Offline preview</label>
          </div>
        </section>
        {preview && <p className="notice">Offline preview uses placeholder visuals and silent narration.</p>}
        {error && <p role="alert" className="notice error">{error}</p>}
        <section className="panel" aria-label="Film playback">
          <div className="panel-head">
            <div className="tabs" aria-label="Select output">
              {Object.entries(labels).map(([key, value]) =>
                <button key={key} aria-pressed={tab === key} onClick={() => setTab(key)}>{value}</button>)}
            </div>
            {source && <a href={source} download={files[tab]}>Download MP4</a>}
          </div>
          <div className={`screen ${tab === "vertical" ? "portrait" : ""}`}>
            {source ? (
              <video key={source} aria-label={labels[tab]} controls playsInline preload="metadata" src={source} />
            ) : (
              <div className="empty">
                <span aria-hidden="true" className="play">▶</span>
                <p>{running ? "Your render is in progress." : `No ${labels[tab].toLowerCase()} render yet.`}</p>
              </div>
            )}
          </div>
        </section>
        {job?.log && (
          <details className="log" open={job?.state === "error" || undefined}>
            <summary>Render log</summary>
            <pre>{job.log}</pre>
          </details>
        )}
      </main>
    </div>
  );
}
