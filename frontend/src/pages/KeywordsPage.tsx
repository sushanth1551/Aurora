import { useEffect, useState, type FormEvent } from "react";

import { ApiError } from "../services/api";
import {
  compareCompetitors,
  getRun,
  getRunResults,
  getSerp,
  listRuns,
  listSelected,
  planRun,
  setSelection,
  startRun,
  type RunResults,
} from "../services/keywords";
import type {
  Comparison,
  Opportunity,
  PlanInput,
  Run,
  SelectedOpportunity,
  SerpSnapshot,
} from "../types/keywords";

interface KeywordsPageProps {
  onUnauthorized: () => void;
}

const FINISHED = new Set(["completed", "partially_completed", "failed"]);
const ACTION_LABEL: Record<string, string> = {
  write_new: "Write new",
  improve_existing: "Improve existing",
  monitor: "Monitor",
  low_evidence: "Low evidence",
  deprioritized: "Deprioritized",
};

function label(value: string): string {
  return value.replace(/_/g, " ");
}

export function KeywordsPage({ onUnauthorized }: KeywordsPageProps) {
  const [run, setRun] = useState<Run | null>(null);
  const [results, setResults] = useState<RunResults | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [selected, setSelected] = useState<SelectedOpportunity[]>([]);
  const [busy, setBusy] = useState<"plan" | "run" | "load" | null>(null);
  const [error, setError] = useState<string | null>(null);

  function fail(failure: unknown) {
    if (failure instanceof ApiError && failure.status === 401) {
      onUnauthorized();
      return;
    }
    setError(failure instanceof Error ? failure.message : "The request failed.");
  }

  async function refreshSide() {
    try {
      const [runs, chosen] = await Promise.all([listRuns(), listSelected()]);
      setHistory(runs);
      setSelected(chosen);
    } catch (failure) {
      fail(failure);
    }
  }

  // Load history and the selection list once when the view opens.
  useEffect(() => {
    void refreshSide();
  }, []);

  async function open(id: string) {
    setBusy("load");
    setError(null);
    try {
      const loaded = await getRun(id);
      setRun(loaded);
      setResults(FINISHED.has(loaded.status) ? await getRunResults(id) : null);
    } catch (failure) {
      fail(failure);
    } finally {
      setBusy(null);
    }
  }

  async function handlePlan(input: PlanInput) {
    setBusy("plan");
    setError(null);
    setResults(null);
    try {
      setRun(await planRun(input));
      void refreshSide();
    } catch (failure) {
      fail(failure);
    } finally {
      setBusy(null);
    }
  }

  async function handleRun(maxSearches: number) {
    if (!run) return;
    setBusy("run");
    setError(null);
    try {
      const finished = await startRun(run.id, maxSearches);
      setRun(finished);
      setResults(FINISHED.has(finished.status) ? await getRunResults(finished.id) : null);
      void refreshSide();
    } catch (failure) {
      fail(failure);
    } finally {
      setBusy(null);
    }
  }

  async function toggleSelection(opportunity: Opportunity, value: boolean) {
    setError(null);
    try {
      const updated = await setSelection(opportunity.id, value);
      setResults((current) =>
        current
          ? {
              ...current,
              opportunities: current.opportunities.map((o) =>
                o.id === updated.id ? updated : o,
              ),
            }
          : current,
      );
      setSelected(await listSelected());
    } catch (failure) {
      fail(failure);
    }
  }

  return (
    <section className="seo-view" aria-labelledby="kw-heading">
      <div className="seo-intro">
        <p className="eyebrow">KEYWORDS &amp; CONTENT OPPORTUNITIES</p>
        <h1 id="kw-heading">Find what to write next</h1>
        <p className="hero-copy">
          AURORA searches Google through SerpApi, records what it observes (results, People
          Also Ask, related searches, AI Overview) and compares competitors with your site’s
          own pages. Search volume and difficulty are not available from this provider and are
          never estimated.
        </p>
      </div>

      <div className="research-card">
        <PlanForm busy={busy !== null} onPlan={handlePlan} />
        {run?.status === "planned" && (
          <RunEstimate run={run} busy={busy === "run"} onRun={handleRun} />
        )}
        {busy === "run" && (
          <p className="notice" role="status">
            Research running. This page waits until it finishes (the server stops it at the
            approved search ceiling or its time limit).
          </p>
        )}
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
      </div>

      {run && run.status !== "planned" && <RunSummary run={run} />}
      {run && results && (
        <Results run={run} results={results} onSelect={toggleSelection} onUnauthorized={fail} />
      )}
      <SelectedList items={selected} />
      <RunHistory runs={history} activeId={run?.id} busy={busy !== null} onOpen={open} />
    </section>
  );
}

function PlanForm({ busy, onPlan }: { busy: boolean; onPlan: (input: PlanInput) => Promise<void> }) {
  const [siteUrl, setSiteUrl] = useState("");
  const [seeds, setSeeds] = useState("");
  const [country, setCountry] = useState("");
  const [language, setLanguage] = useState("");
  const [device, setDevice] = useState("");
  const [maxQueries, setMaxQueries] = useState("");
  const [expansions, setExpansions] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const list = seeds
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    const queries = Number.parseInt(maxQueries, 10);
    await onPlan({
      site_url: siteUrl.trim(),
      seeds: list,
      ...(country.trim() ? { country: country.trim() } : {}),
      ...(language.trim() ? { language: language.trim() } : {}),
      ...(device ? { device: device as PlanInput["device"] } : {}),
      ...(Number.isFinite(queries) && queries > 0 ? { max_queries: queries } : {}),
      include_expansions: expansions,
    });
  }

  return (
    <form className="research-form" onSubmit={submit}>
      <label htmlFor="kw-site">Website URL</label>
      <input
        id="kw-site"
        className="full"
        type="url"
        value={siteUrl}
        onChange={(event) => setSiteUrl(event.target.value)}
        placeholder="https://www.example.com/"
        maxLength={2048}
        required
      />
      <label htmlFor="kw-seeds" className="spaced">
        Seed keywords (one per line, optional if a technical audit exists)
      </label>
      <textarea
        id="kw-seeds"
        rows={3}
        value={seeds}
        onChange={(event) => setSeeds(event.target.value)}
        placeholder={"email marketing\ndrip campaigns"}
        aria-describedby="kw-hint"
      />
      <div className="field-row">
        <div>
          <label htmlFor="kw-country">Country</label>
          <input id="kw-country" className="narrow" value={country} maxLength={2}
            onChange={(event) => setCountry(event.target.value)} placeholder="us" />
        </div>
        <div>
          <label htmlFor="kw-language">Language</label>
          <input id="kw-language" className="narrow" value={language} maxLength={8}
            onChange={(event) => setLanguage(event.target.value)} placeholder="en" />
        </div>
        <div>
          <label htmlFor="kw-device">Device</label>
          <select id="kw-device" value={device} onChange={(event) => setDevice(event.target.value)}>
            <option value="">default</option>
            <option value="desktop">desktop</option>
            <option value="mobile">mobile</option>
            <option value="tablet">tablet</option>
          </select>
        </div>
        <div>
          <label htmlFor="kw-queries">Max searches</label>
          <input id="kw-queries" className="narrow" type="number" min={1} max={50} value={maxQueries}
            onChange={(event) => setMaxQueries(event.target.value)} placeholder="10" />
        </div>
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={expansions} onChange={(event) => setExpansions(event.target.checked)} />
        Also search template variations of each seed (“best … tools”, “how to …”, …). Uses more searches.
      </label>
      <button type="submit" className="primary" disabled={busy || !siteUrl.trim()}>
        Estimate
      </button>
      <p className="form-hint" id="kw-hint">
        Creates a plan with an upper-bound estimate; no search is made yet. Topics are also read
        from this site’s latest stored technical audit, if there is one.
      </p>
    </form>
  );
}

function RunEstimate({ run, busy, onRun }: { run: Run; busy: boolean; onRun: (n: number) => Promise<void> }) {
  const max = run.estimate.serpapi_searches_max;
  const [searches, setSearches] = useState(String(max));
  const [approved, setApproved] = useState(false);
  const value = Number.parseInt(searches, 10);
  const valid = Number.isFinite(value) && value >= 1 && value <= max;
  return (
    <div className="estimate" aria-labelledby="kw-estimate-heading">
      <h2 id="kw-estimate-heading">Estimate for {run.host}</h2>
      <dl className="facts">
        <div>
          <dt>SerpApi searches (credits)</dt>
          <dd>at most {max}</dd>
        </div>
        <div>
          <dt>Cached SERPs reusable</dt>
          <dd>
            {run.estimate.cached_snapshots_possible} (within {run.limits.cache_ttl_hours} h)
          </dd>
        </div>
        <div>
          <dt>Estimated cost</dt>
          <dd>{run.estimate.estimated_cost === null ? "not estimated" : run.estimate.estimated_cost}</dd>
        </div>
      </dl>
      <p className="form-hint">{run.estimate.cost_note}</p>
      <h3>First queries</h3>
      <ul className="notes">
        {run.params.planned_queries.slice(0, 10).map((query) => (
          <li key={query}>{query}</li>
        ))}
      </ul>
      <ul className="notes">
        {run.estimate.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
      <div className="approve-row">
        <label htmlFor="kw-approve">Approve up to</label>
        <input id="kw-approve" className="narrow" type="number" min={1} max={max} value={searches}
          onChange={(event) => setSearches(event.target.value)} />
        <span>SerpApi searches</span>
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} />
        I reviewed this estimate and approve up to {valid ? value : "…"} SerpApi searches.
      </label>
      <button type="button" className="primary" disabled={busy || !approved || !valid} onClick={() => void onRun(value)}>
        {busy ? "Running…" : "Approve and run research"}
      </button>
    </div>
  );
}

function RunSummary({ run }: { run: Run }) {
  const s = run.summary;
  return (
    <section className="panel" aria-labelledby="kw-summary-heading">
      <h2 id="kw-summary-heading">
        {run.host} <span className={`badge status-${run.status}`}>{label(run.status)}</span>
      </h2>
      {run.error_summary && <p className="notice error" role="alert">{run.error_summary}</p>}
      {run.usage.stopped_reason && (
        <p className="notice" role="status">
          Stopped: {label(run.usage.stopped_reason)}. Remaining queries were not searched.
        </p>
      )}
      <dl className="facts">
        <div><dt>SerpApi searches used</dt><dd>{run.usage.serpapi_searches ?? 0} (cached {run.usage.cache_hits ?? 0}, failed {run.usage.failures ?? 0})</dd></div>
        <div><dt>Keywords considered / searched</dt><dd>{s.candidates ?? 0} / {s.searched ?? 0}</dd></div>
        <div><dt>Clusters · competitors</dt><dd>{s.clusters ?? 0} · {s.competitors ?? 0}</dd></div>
        <div><dt>Cost</dt><dd>{run.usage.estimated_cost == null ? "not estimated" : run.usage.estimated_cost}</dd></div>
      </dl>
      {s.metrics_note && <p className="form-hint">{s.metrics_note}</p>}
    </section>
  );
}

function Results({
  run,
  results,
  onSelect,
  onUnauthorized,
}: {
  run: Run;
  results: RunResults;
  onSelect: (o: Opportunity, value: boolean) => Promise<void>;
  onUnauthorized: (failure: unknown) => void;
}) {
  return (
    <>
      <section className="panel" aria-labelledby="opps-heading">
        <h2 id="opps-heading">Content opportunities ({results.opportunities.length})</h2>
        <p className="form-hint">
          Ordered by action, impact and confidence. These are planning labels from observed SERPs,
          not traffic forecasts. Select the ones to write; no article is generated here.
        </p>
        {results.opportunities.length === 0 ? (
          <p>No keyword was searched, so there are no opportunities.</p>
        ) : (
          <ol className="rec-list">
            {results.opportunities.map((o) => (
              <OpportunityRow key={o.id} opportunity={o} onSelect={onSelect} onError={onUnauthorized} />
            ))}
          </ol>
        )}
      </section>
      <CompetitorPanel run={run} results={results} onError={onUnauthorized} />
      <GapPanel results={results} />
    </>
  );
}

function OpportunityRow({
  opportunity: o,
  onSelect,
  onError,
}: {
  opportunity: Opportunity;
  onSelect: (o: Opportunity, value: boolean) => Promise<void>;
  onError: (failure: unknown) => void;
}) {
  const [serp, setSerp] = useState<SerpSnapshot | null>(null);
  const [loading, setLoading] = useState(false);

  async function showSerp() {
    if (serp || !o.serp.snapshot_id) return;
    setLoading(true);
    try {
      setSerp(await getSerp(o.serp.snapshot_id));
    } catch (failure) {
      onError(failure);
    } finally {
      setLoading(false);
    }
  }

  return (
    <li>
      <label className="checkbox select-row">
        <input type="checkbox" checked={o.selected} onChange={(event) => void onSelect(o, event.target.checked)} />
        <span>
          <span className="rec-action">
            <span className={`badge action-${o.action}`}>{ACTION_LABEL[o.action] ?? o.action}</span> {o.keyword}
          </span>
          <span className="sr-only"> select for article generation</span>
        </span>
      </label>
      <p className="rec-meta">
        Impact {o.impact} · confidence {o.confidence} · effort {o.effort} · intent{" "}
        {o.intent ? `${o.intent} (${o.intent_basis})` : "not classified"} · volume / difficulty:{" "}
        {o.metrics.search_volume?.status === "measured" ? o.metrics.search_volume.value : "not available"}
        {o.cluster && o.cluster !== o.keyword.toLowerCase() ? ` · cluster “${o.cluster}”` : ""}
      </p>
      <ul className="notes">
        {o.rationale.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <details onToggle={(event) => event.currentTarget.open && void showSerp()}>
        <summary>
          SERP evidence ({o.serp.organic_count} results, {o.serp.paa_count} questions
          {o.serp.cache_hit ? ", cached" : ""}
          {o.serp.site_position ? `, site at #${o.serp.site_position}` : ", site not in top 10"})
        </summary>
        {loading && <p role="status">Loading…</p>}
        {serp && <SerpView snapshot={serp} />}
      </details>
    </li>
  );
}

function SerpView({ snapshot }: { snapshot: SerpSnapshot }) {
  const f = snapshot.features;
  return (
    <div className="serp">
      <p className="form-hint">
        Observed {new Date(snapshot.observed_at).toLocaleString()} · {snapshot.country ?? "default"}/
        {snapshot.language ?? "default"} · {snapshot.device ?? "default device"}. One observation, not a ranking history.
      </p>
      <ol className="evidence-list">
        {snapshot.organic.slice(0, 10).map((r) => (
          <li key={`${r.position}-${r.url}`}>
            <strong>{r.position}.</strong> {r.title || r.url}
            <br />
            <span className="url-cell">{r.url}</span>
          </li>
        ))}
      </ol>
      {f.people_also_ask.length > 0 && (
        <>
          <h4>People Also Ask</h4>
          <ul className="url-list">{f.people_also_ask.map((q) => <li key={q.question}>{q.question}</li>)}</ul>
        </>
      )}
      {f.related_searches.length > 0 && (
        <>
          <h4>Related searches</h4>
          <ul className="url-list">{f.related_searches.map((q) => <li key={q}>{q}</li>)}</ul>
        </>
      )}
      <p className="form-hint">
        Featured snippet: {f.featured_snippet ? `${f.featured_snippet.domain ?? "unknown source"}` : "none observed"} ·
        AI Overview: {label(f.ai_overview.status)}
        {f.ai_overview.status === "requires_second_request" ? " (not fetched; needs another paid request)" : ""}
        {f.ai_overview.cited_domains.length > 0 ? ` · cites ${f.ai_overview.cited_domains.join(", ")}` : ""}
      </p>
    </div>
  );
}

function CompetitorPanel({
  run,
  results,
  onError,
}: {
  run: Run;
  results: RunResults;
  onError: (failure: unknown) => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Comparison | null>(null);

  async function compare() {
    try {
      setComparison(await compareCompetitors(run.id, picked));
    } catch (failure) {
      onError(failure);
    }
  }

  function toggle(domain: string, on: boolean) {
    setComparison(null);
    setPicked((current) => (on ? [...current, domain].slice(0, 10) : current.filter((d) => d !== domain)));
  }

  return (
    <section className="panel" aria-labelledby="comp-heading">
      <h2 id="comp-heading">SERP competitors ({results.competitors.length})</h2>
      <p className="form-hint">
        Domains that appeared in the top 10 for the researched queries. They compete for the same
        searches; they are not necessarily business competitors.
      </p>
      {results.competitors.length === 0 ? (
        <p>No competitors observed.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col"><span className="sr-only">Compare</span></th>
                <th scope="col">Domain</th>
                <th scope="col">Queries</th>
                <th scope="col">Best position</th>
              </tr>
            </thead>
            <tbody>
              {results.competitors.map((c) => (
                <tr key={c.domain}>
                  <td>
                    <input type="checkbox" aria-label={`Compare ${c.domain}`} checked={picked.includes(c.domain)}
                      onChange={(event) => toggle(c.domain, event.target.checked)} />
                  </td>
                  <td className="url-cell">{c.domain}</td>
                  <td>{c.appearances}</td>
                  <td>{c.best_position}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <button type="button" className="primary" disabled={picked.length < 2} onClick={() => void compare()}>
        Compare selected ({picked.length})
      </button>
      {comparison && (
        <div className="table-scroll">
          <p className="form-hint">{comparison.basis}</p>
          <table>
            <thead>
              <tr>
                <th scope="col">Keyword</th>
                {comparison.domains.map((d) => (
                  <th scope="col" key={d}>{d}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {comparison.keywords.map((k) => (
                <tr key={k}>
                  <th scope="row">{k}{comparison.shared_keywords.includes(k) ? " (shared)" : ""}</th>
                  {comparison.domains.map((d) => (
                    <td key={d}>{comparison.matrix[d]?.[k] ?? "—"}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function GapPanel({ results }: { results: RunResults }) {
  const { gaps } = results;
  return (
    <section className="panel" aria-labelledby="gaps-heading">
      <h2 id="gaps-heading">Content gaps ({gaps.items.length})</h2>
      <p className="form-hint">{gaps.note}</p>
      {gaps.items.map((gap) => (
        <details className="finding" key={`${gap.kind}-${gap.term}`}>
          <summary>
            <span className="finding-title">
              <span className="badge">{gap.kind}</span> {gap.term}
            </span>
            <span className="finding-count">{gap.competitor_domains.length} competitor(s)</span>
          </summary>
          <p className="rec-meta">
            Basis {gap.basis} · queries: {gap.queries.join(", ")}
          </p>
          <ul className="evidence-list">
            {gap.evidence.map((e, index) => (
              <li key={`${e.domain}-${index}`}>
                <span className="url-cell">{e.domain || "unknown source"}</span>
                {e.position > 0 ? ` (#${e.position} for “${e.query}”)` : ` (question for “${e.query}”)`}
                <br />“{e.excerpt}”
              </li>
            ))}
          </ul>
        </details>
      ))}
    </section>
  );
}

function SelectedList({ items }: { items: SelectedOpportunity[] }) {
  if (items.length === 0) return null;
  return (
    <section className="panel" aria-labelledby="selected-heading">
      <h2 id="selected-heading">Selected for article generation ({items.length})</h2>
      <p className="form-hint">Article generation is a later phase; this list is what it will start from.</p>
      <ul className="history">
        {items.map((item) => (
          <li key={item.opportunity_id}>
            <strong>{item.keyword}</strong> · {item.host} · {label(item.action)}
            {item.gap_terms.length > 0 ? ` · gaps: ${item.gap_terms.slice(0, 3).join(", ")}` : ""}
          </li>
        ))}
      </ul>
    </section>
  );
}

function RunHistory({
  runs,
  activeId,
  busy,
  onOpen,
}: {
  runs: Run[];
  activeId: string | undefined;
  busy: boolean;
  onOpen: (id: string) => Promise<void>;
}) {
  if (runs.length === 0) return null;
  return (
    <section className="panel" aria-labelledby="kw-history-heading">
      <h2 id="kw-history-heading">Previous research</h2>
      <ul className="history">
        {runs.map((item) => (
          <li key={item.id}>
            <button type="button" className="link-button" disabled={busy}
              aria-current={item.id === activeId ? "true" : undefined} onClick={() => void onOpen(item.id)}>
              {item.host}
            </button>{" "}
            <span className={`badge status-${item.status}`}>{label(item.status)}</span>{" "}
            <span className="form-hint">{new Date(item.created_at).toLocaleString()}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
