import { useCallback, useEffect, useRef, useState } from "react";

import { ConnectionPanel } from "../components/gsc/ConnectionPanel";
import { InsightsPanel } from "../components/gsc/InsightsPanel";
import { MappingsPanel } from "../components/gsc/MappingsPanel";
import { PerformancePanel } from "../components/gsc/PerformancePanel";
import { SchedulesPanel } from "../components/gsc/SchedulesPanel";
import { SyncPanel } from "../components/gsc/SyncPanel";
import { ApiError } from "../services/api";
import { getStatus } from "../services/gsc";
import type { GscStatus } from "../types/gsc";

interface SearchConsolePageProps {
  onUnauthorized: () => void;
}

export function SearchConsolePage({ onUnauthorized }: SearchConsolePageProps) {
  const [status, setStatus] = useState<GscStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dataVersion, setDataVersion] = useState(0);
  const unauthorized = useRef(onUnauthorized);
  unauthorized.current = onUnauthorized;

  const refresh = useCallback(() => {
    getStatus()
      .then((value) => {
        setStatus(value);
        setError(null);
      })
      .catch((failure: unknown) => {
        if (failure instanceof ApiError && failure.status === 401) {
          unauthorized.current();
          return;
        }
        setError(failure instanceof Error ? failure.message : "The request failed.");
      });
  }, []);

  useEffect(refresh, [refresh]);
  const bump = useCallback(() => setDataVersion((n) => n + 1), []);
  const stableUnauthorized = useCallback(() => unauthorized.current(), []);

  return (
    <section className="seo-view" aria-labelledby="gsc-heading">
      <div className="seo-intro">
        <p className="eyebrow">SEARCH CONSOLE</p>
        <h1 id="gsc-heading">How your pages perform in Google</h1>
        <p className="hero-copy">
          AURORA reads your own Search Console data with read-only access, stores it, and points
          out what changed: low click-through, declines, new queries and possible cannibalization.
          It never edits your site; improvements become drafts that need a fresh approval.
        </p>
      </div>

      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {!status && !error && <p role="status">Loading…</p>}

      {status && (
        <>
          <ConnectionPanel
            status={status}
            onStatus={setStatus}
            onRefresh={refresh}
            onUnauthorized={stableUnauthorized}
          />
          {status.state === "connected" && !status.selected_property && (
            <p className="notice" role="status">
              Load your properties above and choose a verified one to continue.
            </p>
          )}
          {status.state === "connected" && status.selected_property && (
            <>
              <SyncPanel
                property={status.selected_property}
                onSynced={bump}
                onUnauthorized={stableUnauthorized}
              />
              <PerformancePanel refreshKey={dataVersion} onUnauthorized={stableUnauthorized} />
              <InsightsPanel refreshKey={dataVersion} onUnauthorized={stableUnauthorized} />
              <MappingsPanel onUnauthorized={stableUnauthorized} onChanged={bump} />
              <SchedulesPanel
                schedulerEnabled={status.scheduler_enabled}
                onUnauthorized={stableUnauthorized}
              />
            </>
          )}
        </>
      )}
    </section>
  );
}
