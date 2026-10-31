import { useState } from "react";

import { ResearchForm } from "../components/ResearchForm";
import { submitResearch } from "../services/research";
import type { ResearchAcknowledgement } from "../types/research";

export function DashboardPage() {
  const [result, setResult] = useState<ResearchAcknowledgement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleResearch(topic: string) {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await submitResearch({ topic }));
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Unable to submit the research request.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="dashboard-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Aurora home">
          <span className="brand-mark">A</span>
          <span>AURORA</span>
        </a>
        <span className="workspace-label">Research intelligence workspace</span>
        <span className="status-pill"><span /> Prototype</span>
      </header>

      <section className="hero">
        <p className="eyebrow">AUTONOMOUS WEB RESEARCH &amp; SEO INTELLIGENCE</p>
        <h1>Turn the web into<br />a clearer picture.</h1>
        <p className="hero-copy">
          Explore a company, market, or idea. AURORA is designed to connect
          credible sources, search insights, and structured knowledge.
        </p>

        <div className="research-card">
          <ResearchForm onSubmit={handleResearch} busy={busy} />
          {result && (
            <p className="notice" role="status">
              {result.message}
            </p>
          )}
          {error && <p className="notice error" role="alert">{error}</p>}
        </div>
      </section>

      <section className="feature-grid" aria-label="Planned capabilities">
        <article className="feature-card">
          <span className="feature-icon">01</span>
          <h2>Discover</h2>
          <p>Collect and organize useful web sources with provenance.</p>
        </article>
        <article className="feature-card">
          <span className="feature-icon">02</span>
          <h2>Understand</h2>
          <p>Connect entities, search signals, and evidence into context.</p>
        </article>
        <article className="feature-card">
          <span className="feature-icon">03</span>
          <h2>Plan</h2>
          <p>Shape research into responsible SEO strategies and briefs.</p>
        </article>
      </section>

      <footer>Evidence first. Clear recommendations. Human approval.</footer>
    </main>
  );
}
