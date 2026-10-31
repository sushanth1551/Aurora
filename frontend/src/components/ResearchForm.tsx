import { useState, type FormEvent } from "react";

interface ResearchFormProps {
  onSubmit: (topic: string) => Promise<void>;
  busy: boolean;
}

export function ResearchForm({ onSubmit, busy }: ResearchFormProps) {
  const [topic, setTopic] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedTopic = topic.trim();
    if (trimmedTopic.length < 2) return;
    await onSubmit(trimmedTopic);
  }

  return (
    <form className="research-form" onSubmit={handleSubmit}>
      <label htmlFor="research-topic">What would you like to research?</label>
      <div className="input-row">
        <input
          id="research-topic"
          name="topic"
          value={topic}
          onChange={(event) => setTopic(event.target.value)}
          placeholder="A company, market, or research topic"
          maxLength={500}
          minLength={2}
          required
        />
        <button type="submit" disabled={busy || topic.trim().length < 2}>
          {busy ? "Submitting…" : "Start research"}
        </button>
      </div>
      <p className="form-hint">
        Research execution is not implemented yet. This scaffold only validates
        requests and does not start research.
      </p>
    </form>
  );
}
