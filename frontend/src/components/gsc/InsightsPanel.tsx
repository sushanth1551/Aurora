import { useState } from "react";

import { ApiError } from "../../services/api";
import {
  analyze,
  createRefreshTask,
  getInsights,
  listAnalyses,
  listTasks,
  setInsightStatus,
  updateTask,
} from "../../services/gsc";
import type { Analysis, Insight, RefreshTask } from "../../types/gsc";
import { useLoad } from "./useLoad";

interface Props {
  refreshKey: number;
  onUnauthorized: () => void;
}

const KIND_LABEL: Record<string, string> = {
  low_ctr: "High impressions, low CTR",
  declining_clicks: "Declining clicks",
  declining_position: "Declining average position",
  new_query: "New query",
  cannibalization: "Potential cannibalization",
  growing_page: "Growing page",
  no_data_yet: "No data yet",
};
const NEXT_TASK_STATUS: Record<string, { to: string; label: string }[]> = {
  proposed: [{ to: "accepted", label: "Accept" }],
  accepted: [{ to: "draft_requested", label: "Mark draft requested" }],
  draft_requested: [{ to: "draft_ready", label: "Mark draft ready" }],
  draft_ready: [],
  dismissed: [],
};

function show(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  const text = JSON.stringify(value);
  return text.length > 300 ? `${text.slice(0, 300)}…` : text;
}

export function InsightsPanel({ refreshKey, onUnauthorized }: Props) {
  const [latest, setLatest] = useState<Analysis | null>(null);
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const analyses = useLoad(() => listAnalyses(), `${refreshKey}:${version}`, onUnauthorized);
  const current = latest ?? analyses.data?.[0] ?? null;
  const insights = useLoad(
    () => getInsights(current?.id ?? ""),
    `${current?.id}:${version}`,
    onUnauthorized,
    current !== null,
  );
  const tasks = useLoad(() => listTasks(), `${refreshKey}:${version}`, onUnauthorized);

  async function act(name: string, action: () => Promise<unknown>) {
    setBusy(name);
    setError(null);
    try {
      await action();
      setVersion((v) => v + 1);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) {
        onUnauthorized();
        return;
      }
      setError(failure instanceof Error ? failure.message : "The request failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="panel" aria-labelledby="gsc-insights-heading">
      <h2 id="gsc-insights-heading">Findings and recommendations</h2>
      <p className="form-hint">
        Analysis reads stored data only (no Google request). It never changes your site: content
        actions become a brief for a new <strong>draft</strong>, and any update needs a new approval
        before it can be published.
      </p>
      <button
        type="button"
        className="primary"
        disabled={busy !== null}
        onClick={() => void act("analyze", async () => setLatest(await analyze()))}
      >
        {busy === "analyze" ? "Analyzing…" : "Analyze stored data"}
      </button>
      {error && <p className="notice error" role="alert">{error}</p>}
      {(analyses.error || insights.error) && (
        <p className="notice error" role="alert">{analyses.error ?? insights.error}</p>
      )}

      {current && (
        <div className="notes-block">
          <p className="form-hint">
            Analysis from {new Date(current.created_at).toLocaleString()}
            {current.window.current_start
              ? ` · current ${current.window.current_start} to ${current.window.current_end}`
              : ""}
            {current.window.previous_start
              ? ` · compared with ${current.window.previous_start} to ${current.window.previous_end}`
              : ""}
          </p>
          {current.notes.length > 0 && (
            <ul className="notes">
              {current.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {!current && !analyses.loading && <p>No analysis yet. Fetch data, then analyze.</p>}

      {insights.data && insights.data.length === 0 && current && (
        <p>No finding met the thresholds for this data.</p>
      )}
      <ol className="rec-list">
        {insights.data?.map((i) => (
          <InsightItem
            key={i.id}
            insight={i}
            disabled={busy !== null}
            onStatus={(status) => void act("status", () => setInsightStatus(i.id, status))}
            onTask={() => void act("task", () => createRefreshTask(i.id))}
          />
        ))}
      </ol>

      <h3>Refresh briefs ({tasks.data?.length ?? 0})</h3>
      {tasks.data?.length === 0 && <p className="form-hint">None yet.</p>}
      <ul className="history">
        {tasks.data?.map((t) => (
          <TaskItem
            key={t.id}
            task={t}
            disabled={busy !== null}
            onMove={(to) => void act("task-move", () => updateTask(t.id, to))}
          />
        ))}
      </ul>
    </section>
  );
}

function InsightItem({
  insight: i,
  disabled,
  onStatus,
  onTask,
}: {
  insight: Insight;
  disabled: boolean;
  onStatus: (s: "open" | "accepted" | "dismissed") => void;
  onTask: () => void;
}) {
  return (
    <li className={i.status === "dismissed" ? "dismissed" : undefined}>
      <p className="rec-action">
        <span className={`badge tier-${i.tier}`}>{i.tier.replace(/_/g, " ")}</span>{" "}
        <span className="badge">{KIND_LABEL[i.kind] ?? i.kind}</span> {i.action}
      </p>
      <p className="rec-meta">
        Impact {i.impact} · confidence {i.confidence} · effort {i.effort} · risk {i.risk} · basis{" "}
        {i.basis} · {i.status}
      </p>
      <p className="rec-meta">{i.rationale}</p>
      <p className="rec-verify">Verify: {i.verification}</p>
      <details>
        <summary>Evidence</summary>
        <dl className="observed">
          {Object.entries(i.evidence).map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{show(value)}</dd>
            </div>
          ))}
        </dl>
      </details>
      <div className="button-row">
        <button
          type="button"
          className="secondary"
          disabled={disabled || !i.refresh_candidate}
          title={i.refresh_candidate ? "" : "This finding does not call for a content refresh"}
          onClick={onTask}
        >
          Create refresh brief
        </button>
        {i.status !== "dismissed" ? (
          <button type="button" className="secondary" disabled={disabled} onClick={() => onStatus("dismissed")}>
            Dismiss
          </button>
        ) : (
          <button type="button" className="secondary" disabled={disabled} onClick={() => onStatus("open")}>
            Reopen
          </button>
        )}
      </div>
    </li>
  );
}

function TaskItem({
  task,
  disabled,
  onMove,
}: {
  task: RefreshTask;
  disabled: boolean;
  onMove: (to: string) => void;
}) {
  return (
    <li>
      <strong className="url-cell">{task.page_url}</strong>{" "}
      <span className="badge">{task.status.replace(/_/g, " ")}</span>
      <p className="form-hint">{task.brief.reason}</p>
      <ul className="notes">
        {task.brief.proposals?.map((p) => (
          <li key={p}>{p}</li>
        ))}
        {task.brief.constraints?.map((c) => (
          <li key={c}>
            <em>{c}</em>
          </li>
        ))}
      </ul>
      <div className="button-row">
        {(NEXT_TASK_STATUS[task.status] ?? []).map((n) => (
          <button key={n.to} type="button" className="secondary" disabled={disabled} onClick={() => onMove(n.to)}>
            {n.label}
          </button>
        ))}
        {task.status !== "dismissed" && (
          <button type="button" className="secondary" disabled={disabled} onClick={() => onMove("dismissed")}>
            Dismiss
          </button>
        )}
      </div>
    </li>
  );
}
