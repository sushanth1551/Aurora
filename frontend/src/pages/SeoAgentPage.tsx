import { useEffect, useRef, useState } from "react";

import { ApiError } from "../services/api";
import {
  getDashboard,
  listSites,
  type InsightDashboard,
  type SiteSummary,
} from "../services/seoIntelligence";

interface SeoAgentPageProps {
  onUnauthorized: () => void;
}

const PRIORITIES = ["critical", "high", "medium", "low"] as const;

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function Empty({ what, command }: { what: string; command: string }) {
  return (
    <p className="muted-note">
      No {what} yet. Run <code>{command}</code> in the AURORA folder or ask the SEO Intelligence
      Agent.
    </p>
  );
}

export function SeoAgentPage({ onUnauthorized }: SeoAgentPageProps) {
  const [sites, setSites] = useState<SiteSummary[]>([]);
  const [siteId, setSiteId] = useState<string | null>(null);
  const [data, setData] = useState<InsightDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const unauthorized = useRef(onUnauthorized);
  unauthorized.current = onUnauthorized;

  function fail(failure: unknown) {
    if (failure instanceof ApiError && failure.status === 401) {
      unauthorized.current();
      return;
    }
    setError(failure instanceof Error ? failure.message : "The request failed.");
  }

  useEffect(() => {
    listSites()
      .then((items) => {
        setSites(items);
        setSiteId((current) => current ?? items[0]?.id ?? null);
      })
      .catch(fail);
  }, []);

  useEffect(() => {
    if (!siteId) return;
    setData(null);
    getDashboard(siteId)
      .then((value) => {
        setData(value);
        setError(null);
      })
      .catch(fail);
  }, [siteId]);

  return (
    <section className="seo-view" aria-labelledby="agent-heading">
      <div className="seo-intro">
        <p className="eyebrow">SEO INTELLIGENCE AGENT</p>
        <h1 id="agent-heading">What the agent found, and what to do next</h1>
        <p className="hero-copy">
          Every number here comes from a stored run (Search Console, the free crawl, budgeted SERP
          checks, competitor sitemaps). Nothing is estimated on this page; changes to the website
          happen only on a branch after your approval.
        </p>
        {sites.length > 1 && (
          <label>
            Website{" "}
            <select value={siteId ?? ""} onChange={(e) => setSiteId(e.target.value)}>
              {sites.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {!data && !error && <p role="status">Loading…</p>}

      {data && (
        <>
          <section className="panel" aria-labelledby="agent-findings">
            <h2 id="agent-findings">Open findings ({data.open_findings.total})</h2>
            <div className="kpi-row">
              {PRIORITIES.map((p) => (
                <div key={p} className={`kpi kpi-${p}`}>
                  <span className="kpi-value">{data.open_findings.by_priority[p] ?? 0}</span>
                  <span className="kpi-label">{p}</span>
                </div>
              ))}
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Priority</th>
                    <th scope="col">Issue</th>
                    <th scope="col">What to do</th>
                  </tr>
                </thead>
                <tbody>
                  {data.top_findings.map((f) => (
                    <tr key={f.key}>
                      <td>{f.priority}</td>
                      <td>{f.issue}</td>
                      <td>{f.recommended_action}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel" aria-labelledby="agent-alerts">
            <h2 id="agent-alerts">Alerts {data.updated.alerts && <small>({data.updated.alerts})</small>}</h2>
            {data.alerts.length === 0 ? (
              data.updated.alerts ? (
                <p className="muted-note">No alerts in the last run.</p>
              ) : (
                <Empty what="alerts run" command="python scripts/seo_insights.py alerts nextsteps" />
              )
            ) : (
              <ul>
                {data.alerts.map((a) => (
                  <li key={`${a.kind}-${a.subject}`}>
                    <strong>{a.priority}</strong> {a.kind}: {a.subject}. {a.detail}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel" aria-labelledby="agent-opps">
            <h2 id="agent-opps">
              Quick wins {data.updated.opportunities && <small>({data.updated.opportunities})</small>}
            </h2>
            {data.opportunities.length === 0 ? (
              <Empty what="opportunities run" command="python scripts/seo_insights.py opportunities nextsteps" />
            ) : (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Type</th>
                      <th scope="col">Query</th>
                      <th scope="col">Page</th>
                      <th scope="col">Position</th>
                      <th scope="col">Impressions</th>
                      <th scope="col">CTR</th>
                      <th scope="col">Extra clicks (est.)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.opportunities.map((o) => (
                      <tr key={`${o.kind}-${o.query}-${o.page}`}>
                        <td>{o.kind}</td>
                        <td>{o.query}</td>
                        <td className="url-cell">{o.page}</td>
                        <td>{o.position}</td>
                        <td>{o.impressions.toLocaleString()}</td>
                        <td>{pct(o.ctr)}</td>
                        <td>{o.extra_clicks ?? "n/a"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {data.content_trends.length > 0 && (
              <>
                <h3>Decaying and dormant pages</h3>
                <ul>
                  {data.content_trends.map((t) => (
                    <li key={t.page}>
                      {t.page}: {t.status}, {t.decision}
                      {t.merge_with ? ` into ${t.merge_with}` : ""}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>

          <section className="panel" aria-labelledby="agent-ranks">
            <h2 id="agent-ranks">
              Rankings by market {data.updated["rank-tracking"] && <small>({data.updated["rank-tracking"]})</small>}
            </h2>
            {data.market_ranks.length === 0 ? (
              <Empty what="rank tracking run" command="python scripts/seo_insights.py ranks nextsteps" />
            ) : (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Keyword</th>
                      <th scope="col">Market</th>
                      <th scope="col">Position</th>
                      <th scope="col">Previous</th>
                      <th scope="col">Move</th>
                      <th scope="col">Impressions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.market_ranks.map((r) => (
                      <tr key={`${r.keyword}-${r.market}`}>
                        <td>{r.keyword}</td>
                        <td>{r.market}</td>
                        <td>{r.position ?? "no impressions"}</td>
                        <td>{r.previous_position ?? ""}</td>
                        <td>{r.movement ?? ""}</td>
                        <td>{r.impressions.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="panel" aria-labelledby="agent-ai">
            <h2 id="agent-ai">
              AI search {data.updated["ai-visibility"] && <small>({data.updated["ai-visibility"]})</small>}
            </h2>
            {data.ai_crawlers.length === 0 ? (
              <Empty what="AI visibility run" command="python scripts/seo_insights.py ai-visibility nextsteps" />
            ) : (
              <p>
                AI crawlers blocked: {data.ai_crawlers.filter((a) => a.blocked).map((a) => a.agent).join(", ") || "none"}.
              </p>
            )}
            {data.ai_share_of_voice && Object.keys(data.ai_share_of_voice.engines).length > 0 && (
              <ul>
                {Object.entries(data.ai_share_of_voice.engines).map(([engine, v]) => (
                  <li key={engine}>
                    {engine}: cited in {v.cited} of {v.answers} recorded answers ({v.share_cited_pct}%)
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel" aria-labelledby="agent-competitors">
            <h2 id="agent-competitors">
              Competitor changes {data.updated["competitor-watch"] && <small>({data.updated["competitor-watch"]})</small>}
            </h2>
            {data.competitor_changes.length === 0 ? (
              <Empty what="competitor watch run" command="python scripts/seo_insights.py competitors nextsteps" />
            ) : (
              <ul>
                {data.competitor_changes.map((c) => (
                  <li key={c.domain}>
                    {c.domain}: {c.new_count} new, {c.updated_count} updated, {c.removed_count} removed;{" "}
                    {c.uncovered.length} new pages on topics you do not cover
                  </li>
                ))}
              </ul>
            )}
          </section>

          <p className="muted-note">
            Last site crawl: {data.updated["site-crawl"] ?? "never"}
            {data.crawl.coverage ? ` (${data.crawl.coverage.pages_audited} pages)` : ""}.
          </p>
        </>
      )}
    </section>
  );
}
