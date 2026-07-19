import { useCallback, useEffect, useMemo, useState } from "react";

type Candidate = {
  candidate_id: string;
  cube_cell_id: string;
  batch_id?: string;
  archetype_title?: string;
  analytical_category?: string;
  evidence_family?: string;
  task_depth?: string;
  bounded_objective?: string;
  task_sketch?: string;
  bounded_question?: string;
  grader_hint?: string;
  viability?: string;
  scientific_plausibility?: string;
  data_feasibility?: string;
  fits_cell_rationale?: string;
  ground_truth_sketch?: string;
  public_data_accessions?: string[];
  supporting_excerpts?: string[];
  issues?: string[];
  structured_output_schema?: Record<string, unknown>;
  source_paper?: {
    pmid?: string;
    doi?: string;
    title?: string;
    year?: number;
  };
  reviewed_by_me?: boolean;
  review_count?: number;
};

type Summary = {
  n_candidates: number;
  n_feedback: number;
  by_verdict: Record<string, number>;
  by_cell: Record<
    string,
    { candidates: number; reviewed: number; keep: number; revise: number; reject: number }
  >;
};

const TAG_OPTIONS = [
  "wrong_modality",
  "not_proteomics",
  "too_open_ended",
  "no_public_data",
  "cell_overlap",
  "overclaim",
  "thin_evidence",
];

const STORAGE_KEY = "proteobench_review_session";

function loadSession(): { token: string; reviewer: string } | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

async function api<T>(
  path: string,
  token: string,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Review-Token": token,
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || res.statusText);
  }
  return res.json();
}

export default function App() {
  const saved = loadSession();
  const [token, setToken] = useState(saved?.token || "proteobench-review");
  const [reviewer, setReviewer] = useState(saved?.reviewer || "");
  const [authed, setAuthed] = useState(Boolean(saved?.reviewer && saved?.token));
  const [tab, setTab] = useState<"review" | "admin">("review");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [onlyUnreviewed, setOnlyUnreviewed] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);

  const [verdict, setVerdict] = useState<"keep" | "revise" | "reject" | "">("");
  const [confidence, setConfidence] = useState("med");
  const [notes, setNotes] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const selected = useMemo(
    () => candidates.find((c) => c.candidate_id === selectedId) || null,
    [candidates, selectedId]
  );

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const q = new URLSearchParams();
      if (reviewer) q.set("reviewer", reviewer);
      if (onlyUnreviewed) q.set("only_unreviewed", "true");
      const data = await api<{ candidates: Candidate[] }>(
        `/api/candidates?${q.toString()}`,
        token
      );
      setCandidates(data.candidates);
      if (!selectedId && data.candidates.length) {
        setSelectedId(data.candidates[0].candidate_id);
      } else if (
        selectedId &&
        !data.candidates.some((c) => c.candidate_id === selectedId) &&
        data.candidates.length
      ) {
        setSelectedId(data.candidates[0].candidate_id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [token, reviewer, onlyUnreviewed, selectedId]);

  const refreshSummary = useCallback(async () => {
    try {
      const s = await api<Summary>("/api/admin/summary", token);
      setSummary(s);
    } catch {
      /* ignore on review tab */
    }
  }, [token]);

  useEffect(() => {
    if (authed) {
      void refresh();
      void refreshSummary();
    }
  }, [authed, refresh, refreshSummary]);

  function signIn(e: React.FormEvent) {
    e.preventDefault();
    if (!reviewer.trim()) {
      setError("Enter your name");
      return;
    }
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ token, reviewer: reviewer.trim() })
    );
    setAuthed(true);
    setError(null);
  }

  function signOut() {
    localStorage.removeItem(STORAGE_KEY);
    setAuthed(false);
    setCandidates([]);
    setSelectedId(null);
  }

  function toggleTag(t: string) {
    setTags((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
  }

  async function submitFeedback() {
    if (!selected || !verdict) return;
    setSubmitting(true);
    setError(null);
    setStatus(null);
    try {
      await api("/api/feedback", token, {
        method: "POST",
        body: JSON.stringify({
          candidate_id: selected.candidate_id,
          reviewer,
          verdict,
          confidence,
          notes,
          tags,
        }),
      });
      setStatus(`Saved ${verdict} for ${selected.candidate_id}`);
      setVerdict("");
      setNotes("");
      setTags([]);
      await refresh();
      await refreshSummary();
      // advance to next unreviewed if possible
      const next = candidates.find(
        (c) => c.candidate_id !== selected.candidate_id && !c.reviewed_by_me
      );
      if (next) setSelectedId(next.candidate_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  if (!authed) {
    return (
      <div className="app-shell">
        <form className="card gate" onSubmit={signIn}>
          <h1>ProteoBench Review</h1>
          <p>Expert keep / revise / reject on mined candidate tasks.</p>
          <label htmlFor="name">Your name</label>
          <input
            id="name"
            type="text"
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            placeholder="e.g. alice"
            autoFocus
          />
          <label htmlFor="token">Review token</label>
          <input
            id="token"
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
          {error && <div className="error">{error}</div>}
          <div className="btn-row">
            <button className="btn btn-primary" type="submit">
              Enter
            </button>
          </div>
        </form>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <div className="topbar">
        <div className="brand">
          Proteo<span>Bench</span> Review
        </div>
        <div className="tabs">
          <button
            className={`btn ${tab === "review" ? "active" : ""}`}
            onClick={() => setTab("review")}
          >
            Review
          </button>
          <button
            className={`btn ${tab === "admin" ? "active" : ""}`}
            onClick={() => {
              setTab("admin");
              void refreshSummary();
            }}
          >
            Admin
          </button>
        </div>
        <div className="muted">
          {reviewer} ·{" "}
          <button className="btn" type="button" onClick={signOut}>
            Sign out
          </button>
        </div>
      </div>

      {tab === "admin" ? (
        <div className="card">
          <h2>Admin summary</h2>
          {summary ? (
            <>
              <p className="muted">
                {summary.n_candidates} candidates · {summary.n_feedback} feedback
                rows · keep {summary.by_verdict.keep || 0} · revise{" "}
                {summary.by_verdict.revise || 0} · reject{" "}
                {summary.by_verdict.reject || 0}
              </p>
              <p>
                <a href="/api/admin/export.csv" onClick={(e) => {
                  e.preventDefault();
                  fetch("/api/admin/export.csv", {
                    headers: { "X-Review-Token": token },
                  })
                    .then((r) => r.blob())
                    .then((blob) => {
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = "feedback_export.csv";
                      a.click();
                      URL.revokeObjectURL(url);
                    });
                }}>
                  Download feedback CSV
                </a>
              </p>
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>Cell</th>
                    <th>Cands</th>
                    <th>Reviewed</th>
                    <th>Keep</th>
                    <th>Revise</th>
                    <th>Reject</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(summary.by_cell).map(([cell, row]) => (
                    <tr key={cell}>
                      <td>{cell}</td>
                      <td>{row.candidates}</td>
                      <td>{row.reviewed}</td>
                      <td>{row.keep || 0}</td>
                      <td>{row.revise || 0}</td>
                      <td>{row.reject || 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <p className="muted">No summary yet.</p>
          )}
        </div>
      ) : (
        <div className="layout">
          <div className="card">
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <strong>Queue ({candidates.length})</strong>
              <label className="muted" style={{ margin: 0, fontWeight: 400 }}>
                <input
                  type="checkbox"
                  checked={onlyUnreviewed}
                  onChange={(e) => setOnlyUnreviewed(e.target.checked)}
                />{" "}
                Unreviewed
              </label>
            </div>
            <ul className="queue-list">
              {candidates.map((c) => (
                <li key={c.candidate_id}>
                  <button
                    type="button"
                    className={c.candidate_id === selectedId ? "active" : ""}
                    onClick={() => {
                      setSelectedId(c.candidate_id);
                      setVerdict("");
                      setNotes("");
                      setTags([]);
                      setStatus(null);
                    }}
                  >
                    <div className="title">{c.archetype_title || c.candidate_id}</div>
                    <div className="muted">
                      {c.cube_cell_id}
                      {c.reviewed_by_me ? " · done" : ""}
                    </div>
                  </button>
                </li>
              ))}
              {!candidates.length && (
                <li className="muted" style={{ padding: "0.75rem 0.5rem" }}>
                  No candidates. Run paper_mine stage4 with --review-snapshot.
                </li>
              )}
            </ul>
          </div>

          <div className="card">
            {selected ? (
              <>
                <div>
                  <span className="pill">{selected.cube_cell_id}</span>
                  <span className="pill">{selected.task_depth || "?"}</span>
                  <span className="pill">viability {selected.viability || "?"}</span>
                  {selected.batch_id && <span className="pill">{selected.batch_id}</span>}
                </div>
                <h2 style={{ marginBottom: 0.25 }}>{selected.archetype_title}</h2>
                <p className="muted" style={{ marginTop: 0 }}>
                  {selected.analytical_category} · {selected.evidence_family}
                </p>

                <dl className="meta-grid">
                  <div>
                    <dt>Paper</dt>
                    <dd>
                      {selected.source_paper?.pmid ? (
                        <a
                          href={`https://pubmed.ncbi.nlm.nih.gov/${selected.source_paper.pmid}/`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {selected.source_paper.title || `PMID ${selected.source_paper.pmid}`}
                        </a>
                      ) : (
                        selected.source_paper?.title || "—"
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>Year / grader</dt>
                    <dd>
                      {selected.source_paper?.year || "—"} · {selected.grader_hint || "—"}
                    </dd>
                  </div>
                  <div>
                    <dt>Accessions</dt>
                    <dd>
                      {(selected.public_data_accessions || []).join(", ") || "none listed"}
                    </dd>
                  </div>
                  <div>
                    <dt>Plausibility / data</dt>
                    <dd>
                      {selected.scientific_plausibility} / {selected.data_feasibility}
                    </dd>
                  </div>
                </dl>

                <h3>Task sketch</h3>
                <p>{selected.task_sketch || "—"}</p>
                <h3>Bounded question</h3>
                <p>{selected.bounded_question || "—"}</p>
                <h3>Fits cell</h3>
                <p>{selected.fits_cell_rationale || "—"}</p>
                <h3>GT sketch</h3>
                <p className="muted">{selected.ground_truth_sketch || "—"}</p>

                {!!(selected.supporting_excerpts || []).length && (
                  <>
                    <h3>Excerpts</h3>
                    {(selected.supporting_excerpts || []).map((ex, i) => (
                      <div className="excerpt" key={i}>
                        {ex}
                      </div>
                    ))}
                  </>
                )}

                {!!(selected.issues || []).length && (
                  <>
                    <h3>Issues (model)</h3>
                    <ul>
                      {(selected.issues || []).map((x, i) => (
                        <li key={i}>{x}</li>
                      ))}
                    </ul>
                  </>
                )}

                <hr style={{ border: 0, borderTop: "1px solid var(--border)", margin: "1.2rem 0" }} />

                <h3>Your verdict</h3>
                <div className="btn-row">
                  <button
                    type="button"
                    className={`btn btn-ok ${verdict === "keep" ? "btn-primary" : ""}`}
                    onClick={() => setVerdict("keep")}
                  >
                    Keep
                  </button>
                  <button
                    type="button"
                    className={`btn btn-warn ${verdict === "revise" ? "btn-primary" : ""}`}
                    onClick={() => setVerdict("revise")}
                  >
                    Revise
                  </button>
                  <button
                    type="button"
                    className={`btn btn-danger ${verdict === "reject" ? "btn-primary" : ""}`}
                    onClick={() => setVerdict("reject")}
                  >
                    Reject
                  </button>
                </div>

                <label htmlFor="confidence">Confidence</label>
                <select
                  id="confidence"
                  value={confidence}
                  onChange={(e) => setConfidence(e.target.value)}
                >
                  <option value="low">low</option>
                  <option value="med">med</option>
                  <option value="high">high</option>
                </select>

                <label htmlFor="notes">
                  Notes {verdict === "revise" || verdict === "reject" ? "(required)" : "(optional)"}
                </label>
                <textarea
                  id="notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Why keep/revise/reject?"
                />

                <div className="tags">
                  {TAG_OPTIONS.map((t) => (
                    <span
                      key={t}
                      className={`tag ${tags.includes(t) ? "on" : ""}`}
                      onClick={() => toggleTag(t)}
                    >
                      {t}
                    </span>
                  ))}
                </div>

                <div className="btn-row">
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={!verdict || submitting}
                    onClick={() => void submitFeedback()}
                  >
                    {submitting ? "Saving…" : "Submit feedback"}
                  </button>
                </div>
                {error && <div className="error">{error}</div>}
                {status && <div className="success">{status}</div>}
              </>
            ) : (
              <p className="muted">Select a candidate from the queue.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
