import { useEffect, useState } from "react";

import { ApiError } from "../../services/api";
import {
  disconnect,
  listProperties,
  selectProperty,
  startAuthorization,
  verifyConnection,
} from "../../services/gsc";
import type { GscStatus, Site } from "../../types/gsc";

interface Props {
  status: GscStatus;
  onStatus: (status: GscStatus) => void;
  onRefresh: () => void;
  onUnauthorized: () => void;
}

const STATE_LABEL: Record<GscStatus["state"], string> = {
  not_configured: "Not configured",
  disconnected: "Disconnected",
  connected: "Connected",
  reauthorization_required: "Reconnect required",
};

export function ConnectionPanel({ status, onStatus, onRefresh, onUnauthorized }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [sites, setSites] = useState<Site[] | null>(null);
  const [choice, setChoice] = useState("");

  // Coming back from the Google tab: refresh the state when this window is focused again.
  useEffect(() => {
    window.addEventListener("focus", onRefresh);
    return () => window.removeEventListener("focus", onRefresh);
  }, [onRefresh]);

  async function run<T>(name: string, action: () => Promise<T>, done?: (value: T) => void) {
    setBusy(name);
    setError(null);
    setMessage(null);
    try {
      const value = await action();
      done?.(value);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) {
        onUnauthorized();
        return;
      }
      setError(failure instanceof Error ? failure.message : "The request failed.");
      onRefresh();
    } finally {
      setBusy(null);
    }
  }

  const connect = () =>
    run("connect", startAuthorization, ({ authorization_url }) => {
      window.open(authorization_url, "_blank", "noopener");
      setMessage(
        "Google opened in a new tab. Approve read-only access there, then return to this tab.",
      );
    });

  const connected = status.state === "connected";
  return (
    <section className="panel" aria-labelledby="gsc-conn-heading">
      <h2 id="gsc-conn-heading">
        Connection <span className={`badge conn-${status.state}`}>{STATE_LABEL[status.state]}</span>
      </h2>

      {status.state === "not_configured" && (
        <div className="notice" role="status">
          <p>
            Search Console is not set up. AURORA needs a Google OAuth client. Missing settings:{" "}
            <strong>{status.missing_settings.join(", ")}</strong>. Follow the setup steps in
            docs/search-console.md, then restart AURORA. Nothing is shown here until it is
            connected; no data is made up.
          </p>
        </div>
      )}

      {status.state === "reauthorization_required" && (
        <p className="notice error" role="alert">
          Google no longer accepts the stored authorization (it was revoked or expired). Connect
          again.
        </p>
      )}

      {!status.can_store_token && status.oauth_client_configured && (
        <p className="notice" role="status">
          This machine cannot store a refresh token securely, so the Connect button is disabled.
          Put GOOGLE_REFRESH_TOKEN in .env instead (see docs/search-console.md).
        </p>
      )}

      <dl className="facts">
        <div>
          <dt>Access</dt>
          <dd>Read-only (webmasters.readonly)</dd>
        </div>
        <div>
          <dt>Token storage</dt>
          <dd>
            {status.token_source === "none"
              ? "no token"
              : status.token_source === "stored"
                ? `encrypted (${status.token_storage})`
                : "environment (GOOGLE_REFRESH_TOKEN)"}
          </dd>
        </div>
        <div>
          <dt>Property</dt>
          <dd>{status.selected_property ?? "none selected"}</dd>
        </div>
        <div>
          <dt>Last verified</dt>
          <dd>
            {status.last_verified_at ? new Date(status.last_verified_at).toLocaleString() : "not yet"}
          </dd>
        </div>
      </dl>

      <div className="button-row">
        {status.oauth_client_configured && status.token_source !== "environment" && (
          <button
            type="button"
            className="primary"
            disabled={busy !== null || !status.can_store_token}
            onClick={() => void connect()}
          >
            {connected ? "Reconnect" : "Connect Google Search Console"}
          </button>
        )}
        {status.state !== "not_configured" && status.token_source !== "none" && (
          <button
            type="button"
            className="secondary"
            disabled={busy !== null}
            onClick={() =>
              void run("verify", verifyConnection, (s) => {
                onStatus(s);
                setMessage("Google accepted the stored authorization.");
              })
            }
          >
            Test connection
          </button>
        )}
        {status.token_source === "stored" && (
          <button
            type="button"
            className="secondary"
            disabled={busy !== null}
            onClick={() => {
              if (window.confirm("Remove the stored Google authorization from AURORA?\nStored performance data is kept.")) {
                void run("disconnect", () => disconnect(false), onStatus);
              }
            }}
          >
            Disconnect
          </button>
        )}
      </div>
      <p className="form-hint">
        “Test connection” and “Load properties” each make one request to Google. Everything else on
        this page reads data already stored in AURORA.
      </p>

      {connected && (
        <div className="property-picker">
          <button
            type="button"
            className="secondary"
            disabled={busy !== null}
            onClick={() => void run("sites", listProperties, (list) => {
              setSites(list);
              setChoice(list.find((s) => s.verified)?.site_url ?? "");
            })}
          >
            Load properties
          </button>
          {sites && (
            <>
              <label htmlFor="gsc-property">Verified property</label>
              <select id="gsc-property" value={choice} onChange={(e) => setChoice(e.target.value)}>
                <option value="">Choose…</option>
                {sites.map((s) => (
                  <option key={s.site_url} value={s.site_url} disabled={!s.verified}>
                    {s.site_url} ({s.permission_level}
                    {s.verified ? "" : ", not verified"})
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="primary"
                disabled={!choice || busy !== null}
                onClick={() => void run("select", () => selectProperty(choice), onStatus)}
              >
                Use this property
              </button>
            </>
          )}
        </div>
      )}

      {message && (
        <p className="notice" role="status">
          {message}
        </p>
      )}
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
