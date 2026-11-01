import { useState } from "react";

import { ApiTokenForm } from "../components/ApiTokenForm";
import { ResearchForm } from "../components/ResearchForm";
import { ApiError, clearApiToken, getApiToken } from "../services/api";
import { submitResearch } from "../services/research";
import type { ResearchResponse } from "../types/research";
import { KeywordsPage } from "./KeywordsPage";
import { SearchConsolePage } from "./SearchConsolePage";
import { TechnicalSeoPage } from "./TechnicalSeoPage";

type View = "research" | "seo" | "keywords" | "gsc";

export function DashboardPage() {
  const [view, setView] = useState<View>("research");
  const [connected, setConnected] = useState(() => getApiToken() !== null);
  const [tokenNotice, setTokenNotice] = useState<string | null>(null);
  const [result, setResult] = useState<ResearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function disconnect(notice: string | null) {
    clearApiToken();
    setConnected(false);
    setTokenNotice(notice);
    setResult(null);
    setError(null);
  }

  async function handleResearch(topic: string) {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await submitResearch({ query: topic }));
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 401) {
        disconnect("The API token is no longer accepted. Enter it again.");
        return;
      }
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
        <nav className="view-tabs" aria-label="Workspace">
          <button
            type="button"
            aria-current={view === "research" ? "page" : undefined}
            onClick={() => setView("research")}
          >
            Research
          </button>
          <button
            type="button"
            aria-current={view === "seo" ? "page" : undefined}
            onClick={() => setView("seo")}
          >
            Technical SEO
          </button>
          <button
            type="button"
            aria-current={view === "keywords" ? "page" : undefined}
            onClick={() => setView("keywords")}
          >
            Keywords
          </button>
          <button
            type="button"
            aria-current={view === "gsc" ? "page" : undefined}
            onClick={() => setView("gsc")}
          >
            Search Console
          </button>
        </nav>
        {connected ? (
          <button
            type="button"
            className="status-pill"
            onClick={() => disconnect(null)}
            title="Remove the API token from this browser tab"
          >
            <span className="connected" aria-hidden="true" /> Connected · Forget token
          </button>
        ) : (
          <span className="status-pill">
            <span aria-hidden="true" /> Not connected
          </span>
        )}
      </header>

      {connected && view === "seo" ? (
        <TechnicalSeoPage
          onUnauthorized={() =>
            disconnect("The API token is no longer accepted. Enter it again.")
          }
        />
      ) : connected && view === "gsc" ? (
        <SearchConsolePage
          onUnauthorized={() =>
            disconnect("The API token is no longer accepted. Enter it again.")
          }
        />
      ) : connected && view === "keywords" ? (
        <KeywordsPage
          onUnauthorized={() =>
            disconnect("The API token is no longer accepted. Enter it again.")
          }
        />
      ) : (
      <>
      <section className="hero">
        <p className="eyebrow">AUTONOMOUS WEB RESEARCH &amp; SEO INTELLIGENCE</p>
        <h1>Turn the web into<br />a clearer picture.</h1>
        <p className="hero-copy">
          Explore a company, market, or idea. AURORA is designed to connect
          credible sources, search insights, and structured knowledge.
        </p>

        <div className="research-card">
          {!connected ? (
            <ApiTokenForm
              notice={tokenNotice}
              onConnected={() => {
                setConnected(true);
                setTokenNotice(null);
              }}
            />
          ) : (
            <ResearchForm onSubmit={handleResearch} busy={busy} />
          )}
          {connected && result && (
            <div className="notice" role="status">
              <p>
                Job {result.status.replace("_", " ")}: {result.successful_source_count}{" "}
                sources extracted, {result.failed_source_count} failed (
                {result.actual_result_count} search results).
              </p>
              {result.failure && <p>{result.failure.message}</p>}
              <ul>
                {result.sources.map((source) => (
                  <li key={source.id}>
                    {source.title ?? source.original_url} — {source.extraction_status}
                  </li>
                ))}
              </ul>
            </div>
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
      </>
      )}

      <footer>Evidence first. Clear recommendations. Human approval.</footer>
    </main>
  );
}
