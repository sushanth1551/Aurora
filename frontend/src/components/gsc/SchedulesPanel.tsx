import { useState, type FormEvent } from "react";

import { ApiError } from "../../services/api";
import {
  approveSchedule,
  createSchedule,
  listSchedules,
  setScheduleEnabled,
} from "../../services/gsc";
import { useLoad } from "./useLoad";

interface Props {
  schedulerEnabled: boolean;
  onUnauthorized: () => void;
}

export function SchedulesPanel({ schedulerEnabled, onUnauthorized }: Props) {
  const [version, setVersion] = useState(0);
  const schedules = useLoad(() => listSchedules(), version, onUnauthorized);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"sync" | "analyze">("sync");
  const [hours, setHours] = useState("24");
  const [budget, setBudget] = useState("3");
  const [error, setError] = useState<string | null>(null);

  async function act(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      setVersion((v) => v + 1);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) onUnauthorized();
      else setError(failure instanceof Error ? failure.message : "The request failed.");
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void act(() =>
      createSchedule({
        name: name.trim(),
        kind,
        interval_hours: Number.parseInt(hours, 10),
        lookback_days: 7,
        budget_requests: kind === "sync" ? Number.parseInt(budget, 10) : 0,
      }),
    );
    setName("");
  }

  return (
    <section className="panel" aria-labelledby="gsc-sched-heading">
      <h2 id="gsc-sched-heading">Schedules (optional)</h2>
      <p className="notice" role="status">
        {schedulerEnabled
          ? "The scheduler is ON for this installation. Only enabled schedules whose ceiling you approved will run."
          : "The scheduler is OFF (GSC_SCHEDULER_ENABLED=false), so nothing runs automatically even if a schedule is enabled below."}
      </p>
      <form className="research-form" onSubmit={submit}>
        <div className="field-row">
          <div>
            <label htmlFor="sch-name">Name</label>
            <input id="sch-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required />
          </div>
          <div>
            <label htmlFor="sch-kind">Kind</label>
            <select id="sch-kind" value={kind} onChange={(e) => setKind(e.target.value as "sync" | "analyze")}>
              <option value="sync">Fetch data</option>
              <option value="analyze">Analyze stored data</option>
            </select>
          </div>
          <div>
            <label htmlFor="sch-hours">Every (hours, min 6)</label>
            <input id="sch-hours" className="narrow" type="number" min={6} value={hours} onChange={(e) => setHours(e.target.value)} />
          </div>
          {kind === "sync" && (
            <div>
              <label htmlFor="sch-budget">Request ceiling per run</label>
              <input id="sch-budget" className="narrow" type="number" min={2} value={budget} onChange={(e) => setBudget(e.target.value)} />
            </div>
          )}
        </div>
        <button type="submit" className="secondary" disabled={!name.trim()}>
          Create (disabled until approved)
        </button>
      </form>
      {error && <p className="notice error" role="alert">{error}</p>}
      {schedules.error && <p className="notice error" role="alert">{schedules.error}</p>}
      <ul className="history">
        {schedules.data?.map((s) => (
          <li key={s.id}>
            <strong>{s.name}</strong> · {s.kind === "sync" ? `fetch, ceiling ${s.budget_requests} requests` : "analyze"} ·
            every {s.interval_hours} h ·{" "}
            <span className={`badge ${s.enabled ? "status-completed" : ""}`}>{s.enabled ? "enabled" : "disabled"}</span>{" "}
            {s.approved_at ? `approved by ${s.approved_by}` : "not approved"}
            {s.last_status ? ` · last run ${s.last_status}` : ""}
            <div className="button-row">
              {!s.approved_at && (
                <button type="button" className="secondary"
                  onClick={() => void act(() => approveSchedule(s.id, s.budget_requests))}>
                  Approve ceiling ({s.budget_requests})
                </button>
              )}
              {s.approved_at && (
                <button type="button" className="secondary"
                  onClick={() => void act(() => setScheduleEnabled(s.id, !s.enabled))}>
                  {s.enabled ? "Disable" : "Enable"}
                </button>
              )}
            </div>
          </li>
        ))}
        {schedules.data?.length === 0 && <li className="form-hint">No schedules.</li>}
      </ul>
    </section>
  );
}
