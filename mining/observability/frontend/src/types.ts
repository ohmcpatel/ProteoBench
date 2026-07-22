export type DropReason = {
  code: string;
  count: number;
  label: string;
  examples: string[];
};

export type StageCount = {
  stage_id: string;
  label: string;
  unit: string;
  description: string;
  entered: number;
  passed: number;
  dropped: number;
  conversion_from_prev: number | null;
  conversion_from_top: number | null;
  drop_reasons: DropReason[];
};

export type CellYield = {
  cube_cell_id: string;
  batch_id?: string | null;
  analytical_category?: string | null;
  evidence_family?: string | null;
  archetype_title?: string | null;
  cell_viability?: string | null;
  n_papers_fetched: number;
  n_papers_ranked: number;
  n_candidates: number;
  n_candidates_ok: number;
  n_with_accession: number;
  n_data_gated: number;
  n_viability_high: number;
  n_viability_medium: number;
  n_viability_low: number;
  n_review_keep: number;
  n_review_revise: number;
  n_review_reject: number;
  n_review_unreviewed: number;
  n_stubs: number;
  bottleneck_stage?: string | null;
  primary_drop_codes: string[];
};

export type CandidateYieldRow = {
  candidate_id: string;
  cube_cell_id: string;
  batch_id?: string | null;
  extract_status?: string | null;
  viability?: string | null;
  data_feasibility?: string | null;
  scientific_plausibility?: string | null;
  n_accessions: number;
  n_required_inputs: number;
  review_verdict?: string | null;
  has_stub: boolean;
  survived_to: string;
  drop_codes: string[];
  issue_codes: string[];
  issues: string[];
  public_data_accessions: string[];
  bounded_question: string;
  archetype_title: string;
};

export type IssueBucket = {
  code: string;
  label: string;
  count: number;
  example_candidate_ids: string[];
};

export type CostSummary = {
  n_calls: number;
  input_tokens: number;
  output_tokens: number;
  by_status: Record<string, number>;
};

export type YieldSnapshot = {
  schema_version: string;
  generated_at: string;
  run_root: string;
  config_batches: string[];
  only_final_selection: boolean;
  top_k_for_claude: number;
  stages: StageCount[];
  cells: CellYield[];
  candidates: CandidateYieldRow[];
  issue_buckets: IssueBucket[];
  cost: CostSummary;
  kpis: Record<string, number | string>;
  notes: string[];
};

export type DropoffEdge = {
  from_stage: string;
  to_stage: string;
  from_label: string;
  to_label: string;
  from_passed: number;
  to_passed: number;
  dropped: number;
  conversion: number | null;
  unit_from: string;
  unit_to: string;
  drop_reasons: DropReason[];
};
