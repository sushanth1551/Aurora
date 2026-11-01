import { useState } from "react";

import { ApiError } from "../../services/api";
import { planSync, runSync } from "../../services/gsc";
import type { SyncRun } from "../../types/gsc";

interface Props {
  property: string;
  onSynced: () => void;
  onUnauthorized: () => void;
}

export function SyncPanel({ property, onSynced, onUnauthorized }: Props) {
  const [plan, setPlan] = useState<SyncRun | null>(null);
  const [result, setResult] = useState<SyncRun | null>(null);
  const [requests, setRequests] = useState("");
  const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState<"plan" | "run" | null>(null);
  const [error, setError] = useState<string | null>(null);

  function fail(failure: unknown) {
    if (failure instanceof ApiError && failure.status === 401) {
      onUnauthorized();
      return;
    }
    setError(failure instanceof Error ? failure.message : "The request failed.");
  }

  async function makePlan() {
    setBusy("plan");
    setError(null);
    setResult(null);
    setApproved(false);
    try {
      const created = await planSync();
      setPlan(created);
      setRequests(String(created.limits.max_requests));
    } catch (failure) {
      fail(failure);
    } finally {
      setBusy(null);
    }
  }

  async function run() {
    if (!plan) return;
    setBusy("run");
    setError(null);
    try {
      const done = await runSync(plan.id, Number.parseInt(requests, 10));
      setResult(done);
      setPlan(null);
      onSynced();
    } catch (failure) {
      fail(failure);
    } finally {
      setBusy(null);
    }
  }

  const max = plan?.limits.max_requests ?? 0;
  const value = Number.parseInt(requests, 10);
  const valid = Number.isFinite(value) && value >= 2 && value <= max;

  return (
    <section className="panel" aria-labelledby="gsc-sync-heading">
      <h2 id="gsc-sync-heading">Fetch data from Search Console</h2>
      <p className="form-hint">
        Property: <strong>{property}</strong>. Search Console reads are free but quota-limited, so
        each fetch is planned first and runs only with your approval of a request ceiling. Only
        final data is stored; the newest 2–3 days are not final yet.
      </p>
      <button type="button" className="secondary" disabled={busy !== null} onClick={() => void makePlan()}>
        Plan a fetch (last 28 days)
      </button>

      {plan && (
        <div className="estimate">
          <dl className="facts">
            <div>
              <dt>Date range</dt>
              <dd>
                {plan.start_date} to {plan.end_date}
              </dd>
            </div>
            <div>
              <dt>Requests (at most)</dt>
              <dd>{plan.estimate.requests_max}</dd>
            </div>
            <div>
              <dt>Rows (at most)</dt>
              <dd>{plan.estimate.rows_max.toLocaleString()}</dd>
            </div>
          </dl>
          <ul className="notes">
            {plan.estimate.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
          <div className="approve-row">
            <label htmlFor="gsc-approve">Approve up to</label>
            <input
              id="gsc-approve"
              className="narrow"
              type="number"
              min={2}
              max={max}
              value={requests}
              onChange={(event) => setRequests(event.target.value)}
            />
            <span>requests</span>
          </div>
          <label className="checkbox">
            <input type="checkbox" checked={approved} onChange={(e) => setApproved(e.target.checked)} />
            I reviewed this plan and approve up to {valid ? value : "…"} Search Console requests.
          </label>
          <button
            type="button"
            className="primary"
            disabled={busy !== null || !approved || !valid}
            onClick={() => void run()}
          >
            {busy === "run" ? "Fetching…" : "Approve and fetch"}
          </button>
        </div>
      )}

      {result && (
        <div className={result.status === "failed" ? "notice error" : "notice"} role="status">
          <p>
            Fetch {result.status.replace(/_/g, " ")}: {result.usage.requests ?? 0} request(s),{" "}
            {result.usage.rows_inserted ?? 0} new rows stored, {result.usage.rows_skipped ?? 0} already
            stored, {result.usage.daily_inserted ?? 0} new daily totals.
          </p>
          {result.usage.stopped_reason && (
            <p>Stopped at the approved request ceiling; run another fetch to continue.</p>
          )}
          {result.usage.rows_fetched === 0 && (
            <p>Google returned no rows for this range, so nothing was stored.</p>
          )}
          {result.error_summary && <p>{result.error_summary}</p>}
        </div>
      )}
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
