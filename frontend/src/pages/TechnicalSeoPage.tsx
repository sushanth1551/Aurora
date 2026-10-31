import { useEffect, useMemo, useState, type FormEvent } from "react";

import { ApiError } from "../services/api";
import {
  getAudit,
  getAuditResults,
  listAudits,
  planAudit,
  runAudit,
  type AuditResults,
} from "../services/seo";
import type { Audit, Evidence, Finding, Severity } from "../types/seo";

interface TechnicalSeoPageProps {
  onUnauthorized: () => void;
}

const SEVERITIES: Severity[] = ["high", "medium", "low", "info"];
const FINISHED = new Set(["completed", "partially_completed", "failed"]);

function describe(error: unknown): string {
  return error instanceof Error ? error.message : "The request failed.";
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  const text = JSON.stringify(value);
  return text.length > 240 ? `${text.slice(0, 240)}…` : text;
}

export function TechnicalSeoPage({ onUnauthorized }: TechnicalSeoPageProps) {
  const [audit, setAudit] = useState<Audit | null>(null);
  const [results, setResults] = useState<AuditResults | null>(null);
  const [history, setHistory] = useState<Audit[]>([]);
  const [busy, setBusy] = useState<"plan" | "run" | "load" | null>(null);
  const [error, setError] = useState<string | null>(null);

  function fail(failure: unknown) {
    if (failure instanceof ApiError && failure.status === 401) {
      onUnauthorized();
      return;
    }
    setError(describe(failure));
  }

  async function refreshHistory() {
    try {
      setHistory((await listAudits()).items);
    } catch (failure) {
      fail(failure);
    }
  }

  // Load the history once when the view opens.
  useEffect(() => {
    void refreshHistory();
  }, []);

  async function open(id: string) {
    setBusy("load");
    setError(null);
    try {
      const loaded = await getAudit(id);
      setAudit(loaded);
      setResults(FINISHED.has(loaded.status) ? await getAuditResults(id) : null);
    } catch (failure) {
      fail(failure);
    } finally {
      setBusy(null);
    }
  }

  async function handlePlan(url: string, maxPages: number | undefined) {
    setBusy("plan");
    setError(null);
    setResults(null);
    try {
      setAudit(await planAudit(url, maxPages));
      void refreshHistory();
    } catch (failure) {
      fail(failure);
    } finally {
      setBusy(null);
    }
  }

  async function handleRun(maxScrapes: number) {
    if (!audit) return;
    setBusy("run");
    setError(null);
    try {
      const finished = await runAudit(audit.id, maxScrapes);
      setAudit(finished);
      setResults(FINISHED.has(finished.status) ? await getAuditResults(finished.id) : null);
      void refreshHistory();
    } catch (failure) {
      fail(failure);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="seo-view" aria-labelledby="seo-heading">
      <div className="seo-intro">
        <p className="eyebrow">TECHNICAL SEO</p>
        <h1 id="seo-heading">Audit a website</h1>
        <p className="hero-copy">
          AURORA reads robots.txt and sitemaps, traces status codes and redirects,
          and parses each page’s raw HTML. Every finding links to its evidence.
          Nothing is fetched until you approve the estimate.
        </p>
      </div>

      <div className="research-card">
        <AuditPlanForm busy={busy !== null} onPlan={handlePlan} />
        {audit?.status === "planned" && (
          <EstimatePanel audit={audit} busy={busy === "run"} onRun={handleRun} />
        )}
        {busy === "run" && (
          <p className="notice" role="status">
            Audit running. This page waits until it finishes; the server stops it after at
            most {Math.ceil((audit?.limits.max_duration_seconds ?? 0) / 60)} minutes (the
            configured time limit).
          </p>
        )}
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
      </div>

      {audit && audit.status !== "planned" && (
        <AuditSummary audit={audit} />
      )}
      {audit && results && <AuditResultsView results={results} />}

      <AuditHistory audits={history} activeId={audit?.id} busy={busy !== null} onOpen={open} />
    </section>
  );
}

function AuditPlanForm({
  busy,
  onPlan,
}: {
  busy: boolean;
  onPlan: (url: string, maxPages: number | undefined) => Promise<void>;
}) {
  const [url, setUrl] = useState("");
  const [maxPages, setMaxPages] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = url.trim();
    if (!value) return;
    const pages = Number.parseInt(maxPages, 10);
    await onPlan(value, Number.isFinite(pages) && pages > 0 ? pages : undefined);
  }

  return (
    <form className="research-form" onSubmit={submit}>
      <label htmlFor="audit-url">Website URL</label>
      <div className="input-row">
        <input
          id="audit-url"
          name="url"
          type="url"
          inputMode="url"
          autoComplete="url"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://www.example.com/"
          maxLength={2048}
          required
        />
        <label className="sr-only" htmlFor="audit-pages">
          Maximum pages
        </label>
        <input
          id="audit-pages"
          name="max_pages"
          className="narrow"
          type="number"
          min={1}
          max={200}
          value={maxPages}
          onChange={(event) => setMaxPages(event.target.value)}
          placeholder="Pages (25)"
          aria-describedby="audit-hint"
        />
        <button type="submit" disabled={busy || !url.trim()}>
          Estimate
        </button>
      </div>
      <p className="form-hint" id="audit-hint">
        Creates a plan with an upper-bound estimate; no request is sent to the site yet.
        Leave pages empty to use the server default.
      </p>
    </form>
  );
}

function EstimatePanel({
  audit,
  busy,
  onRun,
}: {
  audit: Audit;
  busy: boolean;
  onRun: (maxScrapes: number) => Promise<void>;
}) {
  const max = audit.estimate.firecrawl_scrapes_max;
  const [scrapes, setScrapes] = useState(String(max));
  const [approved, setApproved] = useState(false);
  const value = Number.parseInt(scrapes, 10);
  const valid = Number.isFinite(value) && value >= 1 && value <= max;

  return (
    <div className="estimate" aria-labelledby="estimate-heading">
      <h2 id="estimate-heading">Estimate for {audit.site_url}</h2>
      <dl className="facts">
        <div>
          <dt>Firecrawl scrapes (credits)</dt>
          <dd>at most {max}</dd>
        </div>
        <div>
          <dt>Direct requests to the site</dt>
          <dd>at most {audit.estimate.direct_requests_max}</dd>
        </div>
        <div>
          <dt>Pages / link checks / sitemaps</dt>
          <dd>
            {audit.limits.max_pages} / {audit.limits.max_link_checks} / {audit.limits.max_sitemaps}
          </dd>
        </div>
      </dl>
      <ul className="notes">
        {audit.estimate.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
      <div className="approve-row">
        <label htmlFor="approve-scrapes">Approve up to</label>
        <input
          id="approve-scrapes"
          className="narrow"
          type="number"
          min={1}
          max={max}
          value={scrapes}
          onChange={(event) => setScrapes(event.target.value)}
        />
        <span>Firecrawl scrapes</span>
      </div>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={approved}
          onChange={(event) => setApproved(event.target.checked)}
        />
        I reviewed this estimate and approve spending up to {valid ? value : "…"} Firecrawl
        scrapes on this audit.
      </label>
      <button
        type="button"
        className="primary"
        disabled={busy || !approved || !valid}
        onClick={() => void onRun(value)}
      >
        {busy ? "Running…" : "Approve and run audit"}
      </button>
    </div>
  );
}

function AuditSummary({ audit }: { audit: Audit }) {
  const coverage = audit.coverage;
  const severities = audit.summary.findings_by_severity ?? {};
  return (
    <section className="panel" aria-labelledby="summary-heading">
      <h2 id="summary-heading">
        {audit.site_url} <span className={`badge status-${audit.status}`}>{audit.status.replace(/_/g, " ")}</span>
      </h2>
      {audit.error_summary && (
        <p className="notice error" role="alert">
          {audit.error_summary}
        </p>
      )}
      {coverage.stopped_reason && (
        <p className="notice" role="status">
          Stopped early ({coverage.stopped_reason.replace(/_/g, " ")}); results cover only
          what was collected.
        </p>
      )}
      <dl className="facts">
        <div>
          <dt>Pages audited</dt>
          <dd>
            {coverage.pages_audited ?? 0} of {coverage.pages_discovered ?? 0} discovered
          </dd>
        </div>
        <div>
          <dt>Firecrawl scrapes used</dt>
          <dd>
            {audit.usage.firecrawl_scrapes ?? 0} (failed {audit.usage.firecrawl_failures ?? 0})
          </dd>
        </div>
        <div>
          <dt>Internal link targets checked</dt>
          <dd>
            {coverage.link_targets_checked ?? 0} of {coverage.link_targets_found ?? 0}
          </dd>
        </div>
        <div>
          <dt>Findings</dt>
          <dd>
            {SEVERITIES.map((s) => `${severities[s] ?? 0} ${s}`).join(" · ")}
          </dd>
        </div>
      </dl>
      {coverage.not_measured && coverage.not_measured.length > 0 && (
        <div className="not-measured">
          <h3>Not measured by this audit</h3>
          <ul>
            {coverage.not_measured.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function AuditResultsView({ results }: { results: AuditResults }) {
  const findingsBySeverity = useMemo(() => {
    const groups = new Map<Severity, Finding[]>();
    for (const finding of results.findings) {
      const list = groups.get(finding.severity) ?? [];
      list.push(finding);
      groups.set(finding.severity, list);
    }
    return groups;
  }, [results.findings]);

  return (
    <>
      <section className="panel" aria-labelledby="recs-heading">
        <h2 id="recs-heading">Recommendations ({results.recommendations.length})</h2>
        <p className="form-hint">
          Ordered by tier, impact, confidence and effort. These are planning labels, not
          traffic or ranking forecasts.
        </p>
        {results.recommendations.length === 0 ? (
          <p>No rule produced a finding for the audited pages.</p>
        ) : (
          <ol className="rec-list">
            {results.recommendations.map((rec) => (
              <li key={rec.id}>
                <p className="rec-action">
                  <span className={`badge tier-${rec.tier}`}>{rec.tier.replace(/_/g, " ")}</span>{" "}
                  {rec.action}
                </p>
                <p className="rec-meta">
                  Impact {rec.impact} · confidence {rec.confidence} · effort {rec.effort} · risk{" "}
                  {rec.risk} · {rec.affected_count} affected · <code>{rec.check_id}</code>
                </p>
                <p className="rec-verify">Verify: {rec.verification}</p>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="panel" aria-labelledby="findings-heading">
        <h2 id="findings-heading">Findings ({results.findings.length})</h2>
        {SEVERITIES.filter((s) => findingsBySeverity.has(s)).map((severity) => (
          <div key={severity} className="finding-group">
            <h3>
              <span className={`badge sev-${severity}`}>{severity}</span>{" "}
              {findingsBySeverity.get(severity)?.length} finding(s)
            </h3>
            {findingsBySeverity.get(severity)?.map((finding) => (
              <FindingCard key={finding.id} finding={finding} />
            ))}
          </div>
        ))}
      </section>

      <section className="panel" aria-labelledby="pages-heading">
        <h2 id="pages-heading">
          Pages ({results.pages.length}
          {results.pagesTotal > results.pages.length ? ` of ${results.pagesTotal}` : ""})
        </h2>
        <p className="form-hint">
          “Indexable candidate” means no blocking signal was observed. It does not mean the
          page is indexed; that needs Search Console URL Inspection.
        </p>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">URL</th>
                <th scope="col">Status</th>
                <th scope="col">Fetch</th>
                <th scope="col">Indexability</th>
                <th scope="col">Title</th>
              </tr>
            </thead>
            <tbody>
              {results.pages.map((page) => (
                <tr key={page.id}>
                  <td className="url-cell">{page.url}</td>
                  <td>
                    {page.status_code ?? "—"}
                    {page.status_source === "firecrawl_reported" ? " (reported)" : ""}
                  </td>
                  <td>{page.fetch_status.replace(/_/g, " ")}</td>
                  <td>
                    {page.indexability.replace(/_/g, " ")}
                    {page.indexability_reasons.length > 0 && (
                      <span className="reasons"> ({page.indexability_reasons.join(", ")})</span>
                    )}
                  </td>
                  <td>{page.title ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function FindingCard({ finding }: { finding: Finding }) {
  return (
    <details className="finding">
      <summary>
        <span className="finding-title">{finding.title}</span>
        <span className="finding-count">{finding.affected_count} affected</span>
      </summary>
      <p>{finding.detail}</p>
      <p className="rec-meta">
        <code>{finding.check_id}</code> · evidence {finding.evidence_state} · method{" "}
        {finding.skill_refs.join(", ")}
      </p>
      <h4>Affected URLs</h4>
      <ul className="url-list">
        {finding.affected_urls.slice(0, 20).map((url) => (
          <li key={url}>{url}</li>
        ))}
        {finding.affected_count > 20 && <li>… and {finding.affected_count - 20} more</li>}
      </ul>
      <h4>Evidence</h4>
      <ul className="evidence-list">
        {finding.evidence.map((item, index) => (
          <EvidenceRow key={`${item.url}-${index}`} item={item} />
        ))}
      </ul>
    </details>
  );
}

function EvidenceRow({ item }: { item: Evidence }) {
  const entries = Object.entries(item.observed);
  return (
    <li>
      <p className="evidence-head">
        <span className="url-cell">{item.url}</span> · source <code>{item.source}</code>
        {item.observed_at && <> · {new Date(item.observed_at).toLocaleString()}</>}
      </p>
      {entries.length > 0 && (
        <dl className="observed">
          {entries.map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{formatValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

function AuditHistory({
  audits,
  activeId,
  busy,
  onOpen,
}: {
  audits: Audit[];
  activeId: string | undefined;
  busy: boolean;
  onOpen: (id: string) => Promise<void>;
}) {
  if (audits.length === 0) return null;
  return (
    <section className="panel" aria-labelledby="history-heading">
      <h2 id="history-heading">Previous audits</h2>
      <ul className="history">
        {audits.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              className="link-button"
              disabled={busy}
              aria-current={item.id === activeId ? "true" : undefined}
              onClick={() => void onOpen(item.id)}
            >
              {item.site_url}
            </button>{" "}
            <span className={`badge status-${item.status}`}>{item.status.replace(/_/g, " ")}</span>{" "}
            <span className="form-hint">{new Date(item.created_at).toLocaleString()}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
