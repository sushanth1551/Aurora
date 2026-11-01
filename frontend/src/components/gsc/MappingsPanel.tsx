import { useState, type FormEvent } from "react";

import { ApiError } from "../../services/api";
import { addMapping, listMappings, setMappingActive } from "../../services/gsc";
import { useLoad } from "./useLoad";

interface Props {
  onUnauthorized: () => void;
  onChanged: () => void;
}

export function MappingsPanel({ onUnauthorized, onChanged }: Props) {
  const [version, setVersion] = useState(0);
  const mappings = useLoad(() => listMappings(), version, onUnauthorized);
  const [url, setUrl] = useState("");
  const [keyword, setKeyword] = useState("");
  const [articleId, setArticleId] = useState("");
  const [published, setPublished] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await addMapping({
        page_url: url.trim(),
        ...(keyword.trim() ? { keyword: keyword.trim() } : {}),
        ...(articleId.trim() ? { article_id: articleId.trim() } : {}),
        ...(published ? { published_at: new Date(published).toISOString() } : {}),
      });
      setUrl("");
      setKeyword("");
      setArticleId("");
      setPublished("");
      setVersion((v) => v + 1);
      onChanged();
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) {
        onUnauthorized();
        return;
      }
      setError(failure instanceof Error ? failure.message : "The request failed.");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(id: string, active: boolean) {
    try {
      await setMappingActive(id, active);
      setVersion((v) => v + 1);
      onChanged();
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) onUnauthorized();
      else setError(failure instanceof Error ? failure.message : "The request failed.");
    }
  }

  return (
    <section className="panel" aria-labelledby="gsc-map-heading">
      <h2 id="gsc-map-heading">Articles and pages</h2>
      <p className="form-hint">
        Link a published page (and its target keyword) to an article. A page without data yet is
        explained rather than shown as zero: new pages usually need days to be indexed and to appear
        in Search Console.
      </p>
      <form className="research-form" onSubmit={submit}>
        <label htmlFor="map-url">Published page URL</label>
        <input id="map-url" className="full" type="url" value={url} onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.example.com/blog/my-article" maxLength={2048} required />
        <div className="field-row">
          <div>
            <label htmlFor="map-keyword">Target keyword</label>
            <input id="map-keyword" value={keyword} onChange={(e) => setKeyword(e.target.value)} maxLength={300} />
          </div>
          <div>
            <label htmlFor="map-article">Article id (optional)</label>
            <input id="map-article" value={articleId} onChange={(e) => setArticleId(e.target.value)}
              placeholder="UUID" maxLength={36} />
          </div>
          <div>
            <label htmlFor="map-published">Published on</label>
            <input id="map-published" type="date" value={published} onChange={(e) => setPublished(e.target.value)} />
          </div>
        </div>
        <button type="submit" className="primary" disabled={busy || !url.trim()}>
          Save mapping
        </button>
      </form>
      {error && <p className="notice error" role="alert">{error}</p>}
      {mappings.error && <p className="notice error" role="alert">{mappings.error}</p>}
      <ul className="history">
        {mappings.data?.map((m) => (
          <li key={m.id}>
            <span className="url-cell">{m.page_url}</span>
            {m.keyword ? ` · “${m.keyword}”` : ""}
            {m.published_at ? ` · published ${new Date(m.published_at).toLocaleDateString()}` : ""}{" "}
            <button type="button" className="link-button" onClick={() => void toggle(m.id, false)}>
              deactivate
            </button>
          </li>
        ))}
        {mappings.data?.length === 0 && <li className="form-hint">No mappings yet.</li>}
      </ul>
    </section>
  );
}
