import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  CandidateYieldRow,
  CellYield,
  DropoffEdge,
  YieldSnapshot,
} from "./types";

const STORAGE_KEY = "proteobench_obs_session";

function loadToken(): string {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw).token || "proteobench-review";
  } catch {
    /* ignore */
  }
  return "proteobench-review";
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

function pct(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

function badgeClass(v?: string | null): string {
  const x = (v || "").toLowerCase();
  if (x === "high" || x === "keep" || x === "ok") return "badge high";
  if (x === "medium" || x === "revise" || x === "unknown") return "badge medium";
  if (x === "low" || x === "reject") return "badge low";
  return "badge";
}

function shortStage(id?: string | null): string {
  if (!id) return "—";
  return id.replace(/^S\d+_/, "").replace(/_/g, " ");
}

export default function App() {
  const [token, setToken] = useState(loadToken);
  const [authed, setAuthed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [recomputing, setRecomputing] = useState(false);
  const [snap, setSnap] = useState<YieldSnapshot | null>(null);
  const [edges, setEdges] = useState<DropoffEdge[]>([]);
  const [tab, setTab] = useState<"funnel" | "cells" | "candidates" | "issues">(
    "funnel"
  );

  const [batchFilter, setBatchFilter] = useState("");
  const [cellFilter, setCellFilter] = useState("");
  const [dropFilter, setDropFilter] = useState("");
  const [viaFilter, setViaFilter] = useState("");
  const [selectedCand, setSelectedCand] = useState<CandidateYieldRow | null>(
    null
  );
  const [selectedCell, setSelectedCell] = useState<CellYield | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [y, d] = await Promise.all([
        api<YieldSnapshot>("/api/yield", token),
        api<{ edges: DropoffEdge[] }>("/api/yield/dropoffs", token),
      ]);
      setSnap(y);
      setEdges(d.edges || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (authed) void refresh();
  }, [authed, refresh]);

  function signIn(e: React.FormEvent) {
    e.preventDefault();
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ token }));
    setAuthed(true);
    setError(null);
  }

  async function recompute() {
    setRecomputing(true);
    setError(null);
    try {
      await api("/api/yield/recompute", token, { method: "POST" });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRecomputing(false);
    }
  }

  const maxPassed = useMemo(() => {
    if (!snap?.stages?.length) return 1;
    return Math.max(...snap.stages.map((s) => s.passed), 1);
  }, [snap]);

  const batches = useMemo(() => {
    if (!snap) return [] as string[];
    const s = new Set<string>();
    for (const c of snap.cells) if (c.batch_id) s.add(c.batch_id);
    return [...s].sort();
  }, [snap]);

  const filteredCells = useMemo(() => {
    if (!snap) return [] as CellYield[];
    return snap.cells.filter((c) => {
      if (batchFilter && c.batch_id !== batchFilter) return false;
      if (cellFilter && c.cube_cell_id !== cellFilter) return false;
      return true;
    });
  }, [snap, batchFilter, cellFilter]);

  const filteredCandidates = useMemo(() => {
    if (!snap) return [] as CandidateYieldRow[];
    return snap.candidates.filter((c) => {
      if (batchFilter && c.batch_id !== batchFilter) return false;
      if (cellFilter && c.cube_cell_id !== cellFilter) return false;
      if (viaFilter && (c.viability || "").toLowerCase() !== viaFilter) return false;
      if (dropFilter && !c.drop_codes.includes(dropFilter)) return false;
      return true;
    });
  }, [snap, batchFilter, cellFilter, viaFilter, dropFilter]);

  const allDropCodes = useMemo(() => {
    if (!snap) return [] as string[];
    const s = new Set<string>();
    for (const c of snap.candidates) for (const d of c.drop_codes) s.add(d);
    return [...s].sort();
  }, [snap]);

  if (!authed) {
    return (
      <div className="app-shell">
        <form className="card gate" onSubmit={signIn}>
          <h1>
            ProteoBench <span style={{ color: "var(--accent)" }}>Yield</span>
          </h1>
          <p>
            Pipeline observability — funnel conversion, drop reasons, and per-cell
            attrition. Same token as the review app.
          </p>
          <div className="field">
            <label htmlFor="token">X-Review-Token</label>
            <input
              id="token"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
            />
          </div>
          {error && <div className="error">{error}</div>}
          <button className="btn btn-primary" type="submit">
            Open dashboard
          </button>
        </form>
      </div>
    );
  }

  const k = snap?.kpis || {};

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          ProteoBench <span>Yield</span>
          <span className="sub">
            Mining funnel observability · schema v
            {snap?.schema_version || "…"}
            {snap?.generated_at ? ` · ${snap.generated_at}` : ""}
          </span>
        </div>
        <div className="top-actions">
          <button className="btn" onClick={() => void refresh()} disabled={loading}>
            {loading ? "Loading…" : "Refresh"}
          </button>
          <button
            className="btn btn-primary"
            onClick={() => void recompute()}
            disabled={recomputing}
          >
            {recomputing ? "Recomputing…" : "Recompute yield"}
          </button>
          <button
            className="btn"
            onClick={() => {
              setAuthed(false);
              setSnap(null);
            }}
          >
            Lock
          </button>
        </div>
      </header>

      {error && <div className="error">{error}</div>}

      {snap?.notes?.length ? (
        <p className="notes">Notes: {snap.notes.join(" · ")}</p>
      ) : null}

      <div className="kpi-grid">
        <div className="kpi">
          <div className="label">Inventory</div>
          <div className="value">{k.cube_inventory ?? "—"}</div>
          <div className="hint">cube cells</div>
        </div>
        <div className="kpi">
          <div className="label">Priority</div>
          <div className="value">{k.cube_priority ?? "—"}</div>
          <div className="hint">{String(k.pct_priority_of_inventory ?? "—")}% of inventory</div>
        </div>
        <div className="kpi">
          <div className="label">Pilot scope</div>
          <div className="value">{k.cube_pilot_scope ?? "—"}</div>
          <div className="hint">batches {(snap?.config_batches || []).join(", ") || "—"}</div>
        </div>
        <div className="kpi">
          <div className="label">Papers</div>
          <div className="value">{k.papers_fetched ?? "—"}</div>
          <div className="hint">→ ranked {k.papers_ranked ?? "—"}</div>
        </div>
        <div className="kpi">
          <div className="label">Candidates</div>
          <div className="value">{k.candidates ?? "—"}</div>
          <div className="hint">ok {k.candidates_ok ?? "—"}</div>
        </div>
        <div className="kpi warn">
          <div className="label">Data-gated</div>
          <div className="value">{k.data_gated ?? "—"}</div>
          <div className="hint">{String(k.pct_data_of_ok ?? "—")}% of OK</div>
        </div>
        <div className="kpi danger">
          <div className="label">Viability high</div>
          <div className="value">{k.viability_high ?? "—"}</div>
          <div className="hint">{String(k.pct_high_of_candidates ?? "—")}% of cands</div>
        </div>
        <div className="kpi ok">
          <div className="label">Review keep</div>
          <div className="value">{k.review_keep ?? "—"}</div>
          <div className="hint">stubs {k.eval_stubs ?? "—"}</div>
        </div>
        <div className="kpi">
          <div className="label">Accessions (OK)</div>
          <div className="value">{String(k.pct_accessions_of_ok ?? "—")}%</div>
          <div className="hint">high∩data {k.viability_high_and_data ?? "—"}</div>
        </div>
        <div className="kpi">
          <div className="label">Stage3 cost</div>
          <div className="value">{snap?.cost?.n_calls ?? "—"}</div>
          <div className="hint">
            {snap?.cost
              ? `${(snap.cost.input_tokens / 1000).toFixed(0)}k in / ${(snap.cost.output_tokens / 1000).toFixed(0)}k out`
              : "—"}
          </div>
        </div>
      </div>

      <div className="tabs">
        {(
          [
            ["funnel", "Funnel & drop-off"],
            ["cells", "Cells"],
            ["candidates", "Candidates"],
            ["issues", "Issue taxonomy"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={`tab ${tab === id ? "active" : ""}`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "funnel" && snap && (
        <div className="layout-2">
          <section className="card">
            <h2 className="section-title">Yield funnel</h2>
            <p className="muted" style={{ marginTop: 0 }}>
              Bar width = passed count relative to inventory. Conversion % is vs
              previous stage (unit changes at papers → candidates).
            </p>
            <div className="funnel">
              {snap.stages.map((s) => (
                <div key={s.stage_id} className="funnel-row" title={s.description}>
                  <div className="funnel-label">
                    {s.label}
                    <span className="unit">
                      {s.unit} · {s.stage_id}
                    </span>
                  </div>
                  <div className="bar-track">
                    <div
                      className="bar-fill"
                      style={{
                        width: `${Math.max(2, (100 * s.passed) / maxPassed)}%`,
                      }}
                    />
                  </div>
                  <div className="funnel-num">
                    {s.passed}
                    <span className="pct">
                      prev {pct(s.conversion_from_prev)}
                      {s.dropped > 0 ? ` · −${s.dropped}` : ""}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="card">
            <h2 className="section-title">Why volume dropped</h2>
            <div className="drop-list">
              {edges.map((e) => (
                <div key={`${e.from_stage}->${e.to_stage}`} className="drop-edge">
                  <h4>
                    {e.from_label} → {e.to_label}{" "}
                    <span className="muted">
                      ({e.from_passed} → {e.to_passed}
                      {e.unit_from !== e.unit_to
                        ? ` · ${e.unit_from}→${e.unit_to}`
                        : ""}
                      )
                    </span>
                  </h4>
                  {!e.drop_reasons.length ? (
                    <p className="muted" style={{ margin: 0, fontSize: "0.8rem" }}>
                      No coded drop reasons (or unit change only).
                    </p>
                  ) : (
                    e.drop_reasons.map((r) => {
                      const maxR = Math.max(
                        ...e.drop_reasons.map((x) => x.count),
                        1
                      );
                      return (
                        <div key={r.code}>
                          <div className="reason-row">
                            <span>
                              <button
                                type="button"
                                className="chip"
                                onClick={() => {
                                  setDropFilter(r.code);
                                  setTab("candidates");
                                }}
                                title="Filter candidates with this code"
                              >
                                {r.code}
                              </button>{" "}
                              {r.label}
                            </span>
                            <strong style={{ textAlign: "right" }}>{r.count}</strong>
                          </div>
                          <div className="reason-bar">
                            <span style={{ width: `${(100 * r.count) / maxR}%` }} />
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              ))}
            </div>
          </section>
        </div>
      )}

      {tab === "cells" && snap && (
        <section className="card">
          <h2 className="section-title">Per-cell yield</h2>
          <div className="filters">
            <div className="field">
              <label>Batch</label>
              <select
                value={batchFilter}
                onChange={(e) => setBatchFilter(e.target.value)}
              >
                <option value="">All</option>
                {batches.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Cell</label>
              <select
                value={cellFilter}
                onChange={(e) => setCellFilter(e.target.value)}
              >
                <option value="">All</option>
                {snap.cells.map((c) => (
                  <option key={c.cube_cell_id} value={c.cube_cell_id}>
                    {c.cube_cell_id}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Cell</th>
                  <th>Batch</th>
                  <th>Papers</th>
                  <th>Ranked</th>
                  <th>Cands</th>
                  <th>OK</th>
                  <th>Acc</th>
                  <th>Data</th>
                  <th>High</th>
                  <th>Keep</th>
                  <th>Stub</th>
                  <th>Bottleneck</th>
                  <th>Top drops</th>
                </tr>
              </thead>
              <tbody>
                {filteredCells.map((c) => (
                  <tr
                    key={c.cube_cell_id}
                    className={
                      selectedCell?.cube_cell_id === c.cube_cell_id
                        ? "selected"
                        : ""
                    }
                    onClick={() => {
                      setSelectedCell(c);
                      setCellFilter(c.cube_cell_id);
                    }}
                    style={{ cursor: "pointer" }}
                  >
                    <td className="mono">{c.cube_cell_id}</td>
                    <td>{c.batch_id || "—"}</td>
                    <td>{c.n_papers_fetched}</td>
                    <td>{c.n_papers_ranked}</td>
                    <td>{c.n_candidates}</td>
                    <td>{c.n_candidates_ok}</td>
                    <td>{c.n_with_accession}</td>
                    <td>{c.n_data_gated}</td>
                    <td>{c.n_viability_high}</td>
                    <td>{c.n_review_keep}</td>
                    <td>{c.n_stubs}</td>
                    <td>{shortStage(c.bottleneck_stage)}</td>
                    <td className="mono">
                      {(c.primary_drop_codes || []).slice(0, 2).join(", ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {selectedCell && (
            <div className="detail">
              <h3 style={{ marginBottom: "0.25rem" }}>
                {selectedCell.cube_cell_id}{" "}
                <span className="muted">{selectedCell.archetype_title}</span>
              </h3>
              <p className="muted" style={{ marginTop: 0 }}>
                {selectedCell.analytical_category} · {selectedCell.evidence_family} ·
                cell_viability={selectedCell.cell_viability || "—"}
              </p>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setCellFilter(selectedCell.cube_cell_id);
                  setTab("candidates");
                }}
              >
                Show candidates for this cell
              </button>
            </div>
          )}
        </section>
      )}

      {tab === "candidates" && snap && (
        <section className="card">
          <h2 className="section-title">Candidate survival</h2>
          <div className="filters">
            <div className="field">
              <label>Batch</label>
              <select
                value={batchFilter}
                onChange={(e) => setBatchFilter(e.target.value)}
              >
                <option value="">All</option>
                {batches.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Cell</label>
              <select
                value={cellFilter}
                onChange={(e) => setCellFilter(e.target.value)}
              >
                <option value="">All</option>
                {snap.cells.map((c) => (
                  <option key={c.cube_cell_id} value={c.cube_cell_id}>
                    {c.cube_cell_id}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Viability</label>
              <select
                value={viaFilter}
                onChange={(e) => setViaFilter(e.target.value)}
              >
                <option value="">All</option>
                <option value="high">high</option>
                <option value="medium">medium</option>
                <option value="low">low</option>
              </select>
            </div>
            <div className="field">
              <label>Drop code</label>
              <select
                value={dropFilter}
                onChange={(e) => setDropFilter(e.target.value)}
              >
                <option value="">All</option>
                {allDropCodes.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              className="btn"
              onClick={() => {
                setBatchFilter("");
                setCellFilter("");
                setViaFilter("");
                setDropFilter("");
              }}
            >
              Clear filters
            </button>
          </div>
          <p className="muted">
            Showing {filteredCandidates.length} / {snap.candidates.length} candidates
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Cell</th>
                  <th>Via</th>
                  <th>Data feas.</th>
                  <th>Acc</th>
                  <th>Survived to</th>
                  <th>Review</th>
                  <th>Drops</th>
                </tr>
              </thead>
              <tbody>
                {filteredCandidates.map((c) => (
                  <tr
                    key={c.candidate_id}
                    className={
                      selectedCand?.candidate_id === c.candidate_id
                        ? "selected"
                        : ""
                    }
                    onClick={() => setSelectedCand(c)}
                    style={{ cursor: "pointer" }}
                  >
                    <td className="mono">{c.candidate_id}</td>
                    <td className="mono">{c.cube_cell_id}</td>
                    <td>
                      <span className={badgeClass(c.viability)}>
                        {c.viability || "—"}
                      </span>
                    </td>
                    <td>
                      <span className={badgeClass(c.data_feasibility)}>
                        {c.data_feasibility || "—"}
                      </span>
                    </td>
                    <td>{c.n_accessions}</td>
                    <td>{shortStage(c.survived_to)}</td>
                    <td>
                      <span className={badgeClass(c.review_verdict)}>
                        {c.review_verdict || "unreviewed"}
                      </span>
                    </td>
                    <td className="mono">
                      {c.drop_codes.slice(0, 3).join(", ")}
                      {c.drop_codes.length > 3 ? "…" : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {selectedCand && (
            <div className="detail">
              <h3 style={{ marginBottom: "0.2rem" }}>{selectedCand.candidate_id}</h3>
              <p className="muted" style={{ marginTop: 0 }}>
                {selectedCand.archetype_title}
              </p>
              <p style={{ fontSize: "0.9rem" }}>{selectedCand.bounded_question}</p>
              <div className="tag-row">
                {selectedCand.drop_codes.map((d) => (
                  <span key={d} className="chip" onClick={() => setDropFilter(d)}>
                    {d}
                  </span>
                ))}
              </div>
              {selectedCand.public_data_accessions?.length ? (
                <p className="mono">
                  accessions: {selectedCand.public_data_accessions.join(", ")}
                </p>
              ) : (
                <p className="muted">No public accessions</p>
              )}
              {selectedCand.issues?.length ? (
                <pre>{selectedCand.issues.map((i) => `• ${i}`).join("\n")}</pre>
              ) : null}
            </div>
          )}
        </section>
      )}

      {tab === "issues" && snap && (
        <section className="card">
          <h2 className="section-title">Issue taxonomy (from candidate.issues)</h2>
          <p className="muted" style={{ marginTop: 0 }}>
            Free-text issues classified into stable codes for tracking. Click a
            code to filter candidates that carry related drop signals when
            applicable.
          </p>
          {!snap.issue_buckets.length ? (
            <p className="muted">No issues recorded on candidates.</p>
          ) : (
            <div className="issue-grid">
              {snap.issue_buckets.map((b) => {
                const max = snap.issue_buckets[0]?.count || 1;
                return (
                  <div key={b.code}>
                    <div className="issue-row">
                      <span>
                        <strong className="mono">{b.code}</strong> — {b.label}
                      </span>
                      <strong style={{ textAlign: "right" }}>{b.count}</strong>
                    </div>
                    <div className="reason-bar">
                      <span style={{ width: `${(100 * b.count) / max}%` }} />
                    </div>
                    {b.example_candidate_ids?.length ? (
                      <p className="muted mono" style={{ margin: "0.25rem 0 0.6rem" }}>
                        e.g. {b.example_candidate_ids.slice(0, 3).join(", ")}
                      </p>
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
