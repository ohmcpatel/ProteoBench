"""Pydantic models for the ProteoBench paper-mine pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskDepth(str, Enum):
    atomic = "Atomic"
    composite = "Composite"
    decision = "Decision"


class SearchPack(BaseModel):
    cube_cell_id: str
    batch_id: str
    analytical_category_code: str
    analytical_category: str
    evidence_family_code: str
    evidence_family: str
    task_depth: str
    task_depth_definition: str
    cell_viability: str
    candidate_generation_target: int
    final_selection_target_v0: int
    archetype_title: str
    bounded_objective: str
    example_agent_inputs: list[Any] = Field(default_factory=list)
    example_structured_output: dict[str, Any] | list[Any] | None = None
    likely_grader_types: list[str] = Field(default_factory=list)
    primary_failure_modes: list[str] = Field(default_factory=list)
    acquisition_method_tags: list[str] = Field(default_factory=list)
    experiment_type_tags: list[str] = Field(default_factory=list)
    scientific_context_tags: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    seed_queries: list[str] = Field(default_factory=list)


class PaperRecord(BaseModel):
    cube_cell_id: str
    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    title: str = ""
    abstract: str = ""
    year: int | None = None
    journal: str | None = None
    authors: str | None = None
    is_oa: bool = False
    fulltext: str = ""
    fulltext_source: str | None = None
    text_quality: str = "abstract_only"  # abstract_only | fulltext_oa
    query_used: str = ""
    source_api: str = "europepmc"
    fetched_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    rank_score: float | None = None
    rank_reasons: list[str] = Field(default_factory=list)

    @property
    def paper_key(self) -> str:
        if self.pmid:
            return f"pmid{self.pmid}"
        if self.pmcid:
            return self.pmcid.replace(":", "")
        if self.doi:
            return "doi_" + self.doi.replace("/", "_").replace(".", "_")
        return "unknown"


class InputRole(BaseModel):
    role: str
    likely_format: str = "to_be_determined"


class SourcePaper(BaseModel):
    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    title: str = ""
    year: int | None = None


class CandidateCard(BaseModel):
    candidate_id: str
    cube_cell_id: str
    source_paper: SourcePaper
    archetype_title: str
    task_sketch: str
    bounded_question: str
    required_inputs: list[InputRole] = Field(default_factory=list)
    structured_output_schema: dict[str, Any] = Field(default_factory=dict)
    grader_hint: str = "multiple_choice"
    ground_truth_sketch: str = ""
    public_data_accessions: list[str] = Field(default_factory=list)
    fits_cell_rationale: str = ""
    scientific_plausibility: str = "medium"  # high|medium|low
    data_feasibility: str = "unknown"  # high|medium|low|unknown
    viability: str = "medium"  # high|medium|low
    issues: list[str] = Field(default_factory=list)
    supporting_excerpts: list[str] = Field(default_factory=list)
    extract_status: str = "ok"
    model: str | None = None
    extracted_at: str | None = None


class ReviewVerdict(str, Enum):
    keep = "keep"
    revise = "revise"
    reject = "reject"


class ReviewVote(BaseModel):
    feedback_id: str
    candidate_id: str
    reviewer: str
    verdict: ReviewVerdict
    confidence: str = "med"  # low|med|high
    notes: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
