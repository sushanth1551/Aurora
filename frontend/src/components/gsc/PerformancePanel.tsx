import { useState } from "react";

import { getOverview, getPage, getTop } from "../../services/gsc";
import type { TopRow } from "../../types/gsc";
import { LineChart } from "./LineChart";
import { useLoad } from "./useLoad";

interface Props {
  refreshKey: number;
  onUnauthorized: () => void;
}

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
const pos = (v: number | null) => (v === null ? "—" : v.toFixed(1));

export function PerformancePanel({ refreshKey, onUnauthorized }: Props) {
  const [pageUrl, setPageUrl] = useState<string | null>(null);
  const overview = useLoad(() => getOverview(), refreshKey, onUnauthorized);
  const pages = useLoad(() => getTop("pages"), refreshKey, onUnauthorized);
  const queries = useLoad(() => getTop("queries"), refreshKey, onUnauthorized);
  const detail = useLoad(
    () => getPage(pageUrl ?? ""),
    pageUrl,
    onUnauthorized,
    pageUrl !== null,
  );

  const o = overview.data;
  return (
    <section className="panel" aria-labelledby="gsc-perf-heading">
      <h2 id="gsc-perf-heading">Performance (stored data)</h2>
      <div className="notice" role="note">
        <strong>About “average position”.</strong>{" "}
        {o?.position_note ??
          "It is not exact rank tracking: it averages the topmost position of your site across impressions and moves with the query mix, location and device."}
      </div>
      {overview.error && <p className="notice error" role="alert">{overview.error}</p>}
      {overview.loading && !o && <p role="status">Loading…</p>}

      {o && !o.has_data && (
        <p className="notice" role="status">
          {o.note} Nothing is estimated or filled in while there is no data.
        </p>
      )}

      {o?.has_data && o.totals && (
        <>
          <dl className="facts">
            <div>
              <dt>Clicks</dt>
              <dd>{o.totals.clicks.toLocaleString()}</dd>
            </div>
            <div>
              <dt>Impressions</dt>
              <dd>{o.totals.impressions.toLocaleString()}</dd>
            </div>
            <div>
              <dt>CTR</dt>
              <dd>{pct(o.totals.ctr)}</dd>
            </div>
            <div>
              <dt>Average position</dt>
              <dd>{pos(o.totals.position)}</dd>
            </div>
          </dl>
          <p className="form-hint">
            {o.start} to {o.end} · {o.days_with_data} of {o.days_in_range} days stored · {o.note}
          </p>
          <div className="charts">
            <LineChart
              title="Clicks per day"
              unit="clicks"
              points={o.series.map((d) => ({ label: d.date, value: d.clicks }))}
            />
            <LineChart
              title="Impressions per day"
              unit="impressions"
              points={o.series.map((d) => ({ label: d.date, value: d.impressions }))}
            />
            <LineChart
              title="Average position per day (lower is better; not exact rank)"
              unit="average position"
              invert
              digits={1}
              points={o.series.map((d) => ({ label: d.date, value: d.position }))}
            />
          </div>
        </>
      )}

      {pages.data?.has_data && (
        <RowsTable title="Top pages" rows={pages.data.items} first="page" onPick={setPageUrl} />
      )}
      {queries.data?.has_data && <RowsTable title="Top queries" rows={queries.data.items} first="query" />}

      {pageUrl && (
        <div className="page-detail" aria-live="polite">
          <h3>
            Page detail <button type="button" className="link-button" onClick={() => setPageUrl(null)}>close</button>
          </h3>
          <p className="url-cell">{pageUrl}</p>
          {detail.loading && <p role="status">Loading…</p>}
          {detail.error && <p className="notice error" role="alert">{detail.error}</p>}
          {detail.data && !detail.data.has_data && (
            <p className="notice" role="status">
              {detail.data.state.note ?? "No stored rows for this page."}
            </p>
          )}
          {detail.data?.has_data && (
            <>
              <LineChart
                title="Clicks per day for this page"
                unit="clicks"
                points={detail.data.series.map((d) => ({ label: d.date, value: d.clicks }))}
              />
              <RowsTable
                title="Queries for this page"
                rows={detail.data.queries.map((q) => ({ page: null, ...q }))}
                first="query"
              />
            </>
          )}
        </div>
      )}
    </section>
  );
}

function RowsTable({
  title,
  rows,
  first,
  onPick,
}: {
  title: string;
  rows: TopRow[];
  first: "page" | "query";
  onPick?: (url: string) => void;
}) {
  return (
    <div className="table-scroll">
      <h3>{title}</h3>
      <table>
        <thead>
          <tr>
            <th scope="col">{first === "page" ? "Page" : "Query"}</th>
            <th scope="col">Clicks</th>
            <th scope="col">Impressions</th>
            <th scope="col">CTR</th>
            <th scope="col">Avg position</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const label = (first === "page" ? r.page : r.query) ?? "";
            return (
              <tr key={label}>
                <td className="url-cell">
                  {onPick ? (
                    <button type="button" className="link-button" onClick={() => onPick(label)}>
                      {label}
                    </button>
                  ) : (
                    label
                  )}
                </td>
                <td>{r.clicks.toLocaleString()}</td>
                <td>{r.impressions.toLocaleString()}</td>
                <td>{pct(r.ctr)}</td>
                <td>{pos(r.position)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
