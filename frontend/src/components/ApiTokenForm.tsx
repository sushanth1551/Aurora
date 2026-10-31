import { useState, type FormEvent } from "react";

import { ApiError, setApiToken, verifyApiToken } from "../services/api";

interface ApiTokenFormProps {
  onConnected: () => void;
  notice?: string | null;
}

export function ApiTokenForm({ onConnected, notice }: ApiTokenFormProps) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = token.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      await verifyApiToken(value);
      setApiToken(value);
      setToken("");
      onConnected();
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.status === 401
          ? "That token was not accepted. Copy AURORA_API_TOKEN from your .env file."
          : failure instanceof Error
            ? failure.message
            : "Could not verify the token.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="research-form" onSubmit={handleSubmit}>
      <label htmlFor="api-token">Local API token</label>
      <div className="input-row">
        <input
          id="api-token"
          name="api-token"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder="Paste AURORA_API_TOKEN…"
          aria-describedby="api-token-hint"
          required
        />
        <button type="submit" disabled={busy || !token.trim()}>
          {busy ? "Checking…" : "Connect"}
        </button>
      </div>
      <p className="form-hint" id="api-token-hint">
        The token is the value of AURORA_API_TOKEN in the .env file (created by
        scripts\init-local-secrets.ps1). It is kept in this browser tab only.
      </p>
      {notice && !error && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
