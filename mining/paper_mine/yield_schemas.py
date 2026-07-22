"""Schema contracts for ProteoBench mining yield / funnel observability.

Every pipeline unit that can be counted or dropped is typed here so yield
snapshots stay comparable across runs and releases.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class StageId(str, Enum):
    """Ordered funnel stages (coarse grain, left → right)."""

    cube_inventory = "S00_cube_inventory"
    cube_priority = "S01_cube_priority"
    cube_pilot_scope = "S02_cube_pilot_scope"
    search_packs = "S03_search_packs"
    papers_fetched = "S04_papers_fetched"
    papers_ranked = "S05_papers_ranked_top_k"
    candidates_extracted = "S06_candidates_extracted"
    candidates_ok = "S07_candidates_ok"
    data_gated = "S08_data_gated"
    viability_high = "S09_viability_high"
    review_keep = "S10_review_keep"
    eval_stub = "S11_eval_stub"


STAGE_ORDER: list[StageId] = list(StageId)

STAGE_META: dict[StageId, dict[str, str]] = {
    StageId.cube_inventory: {
        "label": "Cube inventory",
        "unit": "cell",
        "description": "All cells in cube_inventory CSV",
    },
    StageId.cube_priority: {
        "label": "Priority cells",
        "unit": "cell",
        "description": "final_selection_target_v0 > 0 (cube feasibility prune)",
    },
    StageId.cube_pilot_scope: {
        "label": "Pilot batches",
        "unit": "cell",
        "description": "Priority cells in configured pilot batches (e.g. B01/B02)",
    },
    StageId.search_packs: {
        "label": "Search packs",
        "unit": "cell",
        "description": "Exported search packs (export_queue)",
    },
    StageId.papers_fetched: {
        "label": "Papers fetched",
        "unit": "paper",
        "description": "Paper JSON on disk after stage1",
    },
    StageId.papers_ranked: {
        "label": "Papers ranked (top-K)",
        "unit": "paper",
        "description": "Papers kept for Claude after stage2",
    },
    StageId.candidates_extracted: {
        "label": "Candidates extracted",
        "unit": "candidate",
        "description": "Candidate cards written by stage3",
    },
    StageId.candidates_ok: {
        "label": "Extract OK",
        "unit": "candidate",
        "description": "extract_status == ok",
    },
    StageId.data_gated: {
        "label": "Data-ready",
        "unit": "candidate",
        "description": "OK + ≥1 public accession + data_feasibility high|medium",
    },
    StageId.viability_high: {
        "label": "Viability high",
        "unit": "candidate",
        "description": "Model/self viability == high (aggressive keep bar)",
    },
    StageId.review_keep: {
        "label": "Review keep",
        "unit": "candidate",
        "description": "Latest expert verdict == keep",
    },
    StageId.eval_stub: {
        "label": "Eval stubs",
        "unit": "candidate",
        "description": "Draft eval JSON stubs emitted by stage4",
    },
}


class DropReasonCode(str, Enum):
    """Machine-stable drop / attrition reason codes."""

    # Cube
    cube_not_priority = "cube_not_priority"
    cube_batch_out_of_scope = "cube_batch_out_of_scope"
    cube_weak_cell_viability = "cube_weak_cell_viability"
    pack_not_exported = "pack_not_exported"

    # Fetch / rank
    no_papers_fetched = "no_papers_fetched"
    paper_below_top_k = "paper_below_top_k"
    ranked_missing = "ranked_missing"

    # Extract
    extract_missing = "extract_missing"
    extract_failed = "extract_failed"
    extract_status_not_ok = "extract_status_not_ok"

    # Data gates (aggressive)
    no_public_accession = "no_public_accession"
    data_feasibility_unknown = "data_feasibility_unknown"
    data_feasibility_low = "data_feasibility_low"
    required_inputs_empty = "required_inputs_empty"
    inputs_marked_missing = "inputs_marked_missing"

    # Quality / viability
    scientific_plausibility_low = "scientific_plausibility_low"
    viability_medium = "viability_medium"
    viability_low = "viability_low"

    # Review
    review_unreviewed = "review_unreviewed"
    review_revise = "review_revise"
    review_reject = "review_reject"

    # Package
    stub_not_emitted = "stub_not_emitted"

    # Issue taxonomy (from free-text issues)
    issue_no_public_data = "issue_no_public_data"
    issue_missing_metrics = "issue_missing_metrics"
    issue_ground_truth_unspecified = "issue_ground_truth_unspecified"
    issue_abstract_only = "issue_abstract_only"
    issue_wrong_fit = "issue_wrong_fit"
    issue_open_ended = "issue_open_ended"
    issue_other = "issue_other"

    other = "other"
    unknown = "unknown"


class DropReason(BaseModel):
    code: DropReasonCode
    count: int = 0
    label: str = ""
    examples: list[str] = Field(default_factory=list)


class StageCount(BaseModel):
    stage_id: StageId
    label: str
    unit: str
    description: str
    entered: int
    passed: int
    dropped: int
    conversion_from_prev: float | None = None  # passed / prev.passed
    conversion_from_top: float | None = None  # passed / first.passed
    drop_reasons: list[DropReason] = Field(default_factory=list)


class IssueBucket(BaseModel):
    code: DropReasonCode
    label: str
    count: int
    example_candidate_ids: list[str] = Field(default_factory=list)


class CellYield(BaseModel):
    cube_cell_id: str
    batch_id: str | None = None
    analytical_category: str | None = None
    evidence_family: str | None = None
    archetype_title: str | None = None
    cell_viability: str | None = None
    final_selection_target_v0: int | None = None
    candidate_generation_target: int | None = None

    n_papers_fetched: int = 0
    n_papers_ranked: int = 0
    n_candidates: int = 0
    n_candidates_ok: int = 0
    n_with_accession: int = 0
    n_data_gated: int = 0
    n_viability_high: int = 0
    n_viability_medium: int = 0
    n_viability_low: int = 0
    n_review_keep: int = 0
    n_review_revise: int = 0
    n_review_reject: int = 0
    n_review_unreviewed: int = 0
    n_stubs: int = 0

    bottleneck_stage: StageId | None = None
    primary_drop_codes: list[DropReasonCode] = Field(default_factory=list)


class CandidateYieldRow(BaseModel):
    """Per-candidate survival record for drill-down."""

    candidate_id: str
    cube_cell_id: str
    batch_id: str | None = None
    extract_status: str | None = None
    viability: str | None = None
    data_feasibility: str | None = None
    scientific_plausibility: str | None = None
    n_accessions: int = 0
    n_required_inputs: int = 0
    review_verdict: str | None = None  # keep|revise|reject|None
    has_stub: bool = False
    survived_to: StageId
    drop_codes: list[DropReasonCode] = Field(default_factory=list)
    issue_codes: list[DropReasonCode] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    public_data_accessions: list[str] = Field(default_factory=list)
    bounded_question: str = ""
    archetype_title: str = ""


class CostSummary(BaseModel):
    n_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)


class YieldSnapshot(BaseModel):
    """Versioned, schema-validated observability snapshot for one mining run."""

    schema_version: str = "1.0.0"
    generated_at: str = Field(default_factory=utc_now_iso)
    run_root: str = ""
    config_batches: list[str] = Field(default_factory=list)
    only_final_selection: bool = True
    top_k_for_claude: int = 5

    stages: list[StageCount] = Field(default_factory=list)
    cells: list[CellYield] = Field(default_factory=list)
    candidates: list[CandidateYieldRow] = Field(default_factory=list)
    issue_buckets: list[IssueBucket] = Field(default_factory=list)
    cost: CostSummary = Field(default_factory=CostSummary)

    # Headline KPIs (redundant with stages for easy UI binding)
    kpis: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


# Human labels for drop codes (UI)
DROP_REASON_LABELS: dict[DropReasonCode, str] = {
    DropReasonCode.cube_not_priority: "Not priority (final_selection_target_v0 = 0)",
    DropReasonCode.cube_batch_out_of_scope: "Outside pilot batches",
    DropReasonCode.cube_weak_cell_viability: "Weak cube cell_viability",
    DropReasonCode.pack_not_exported: "No search pack exported",
    DropReasonCode.no_papers_fetched: "No papers fetched for cell",
    DropReasonCode.paper_below_top_k: "Paper not in stage2 top-K",
    DropReasonCode.ranked_missing: "Ranked file missing for cell",
    DropReasonCode.extract_missing: "No extract for ranked paper slot",
    DropReasonCode.extract_failed: "Extract call failed",
    DropReasonCode.extract_status_not_ok: "extract_status != ok",
    DropReasonCode.no_public_accession: "No public data accession",
    DropReasonCode.data_feasibility_unknown: "data_feasibility = unknown",
    DropReasonCode.data_feasibility_low: "data_feasibility = low",
    DropReasonCode.required_inputs_empty: "required_inputs empty",
    DropReasonCode.inputs_marked_missing: "Inputs marked missing in source",
    DropReasonCode.scientific_plausibility_low: "scientific_plausibility = low",
    DropReasonCode.viability_medium: "viability = medium (aggressive prune)",
    DropReasonCode.viability_low: "viability = low",
    DropReasonCode.review_unreviewed: "Not yet reviewed",
    DropReasonCode.review_revise: "Review: revise",
    DropReasonCode.review_reject: "Review: reject",
    DropReasonCode.stub_not_emitted: "No eval stub emitted",
    DropReasonCode.issue_no_public_data: "Issue: no public data",
    DropReasonCode.issue_missing_metrics: "Issue: missing metrics / run IDs",
    DropReasonCode.issue_ground_truth_unspecified: "Issue: ground truth unspecified",
    DropReasonCode.issue_abstract_only: "Issue: abstract-only / thin text",
    DropReasonCode.issue_wrong_fit: "Issue: poor cube-cell fit",
    DropReasonCode.issue_open_ended: "Issue: open-ended / not bounded",
    DropReasonCode.issue_other: "Issue: other",
    DropReasonCode.other: "Other",
    DropReasonCode.unknown: "Unknown",
}
