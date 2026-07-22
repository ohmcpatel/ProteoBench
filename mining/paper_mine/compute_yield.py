#!/usr/bin/env python3
"""Compute a schema-validated yield / funnel snapshot for the mining pipeline.

Reads cube + stage artifacts from disk (no network). Safe to re-run any time.

  python compute_yield.py
  python compute_yield.py --out out/observability/yield_snapshot.json

Exit 0 always when snapshot writes; exit 1 on hard config/path errors.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from common import load_config, read_json, read_jsonl, write_json
from yield_schemas import (
    DROP_REASON_LABELS,
    STAGE_META,
    CandidateYieldRow,
    CellYield,
    CostSummary,
    DropReason,
    DropReasonCode,
    IssueBucket,
    StageCount,
    StageId,
    YieldSnapshot,
    utc_now_iso,
)

# review_app feedback lives next to paper_mine under mining/
MINING_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FEEDBACK = MINING_ROOT / "review_app" / "backend" / "data" / "feedback.jsonl"

MISSING_INPUT_RE = re.compile(
    r"not provided|not available|missing from|cannot be constructed|no run.?id|"
    r"abstract.?only|not present in",
    re.I,
)


def _latest_verdict(feedback_rows: list[dict[str, Any]]) -> dict[str, str]:
    """candidate_id → latest verdict (by created_at, then file order)."""
    staged: dict[str, tuple[str, str, str]] = {}
    for i, row in enumerate(feedback_rows):
        cid = row.get("candidate_id")
        if not cid:
            continue
        ts = str(row.get("created_at") or "")
        order = f"{i:06d}"
        cur = staged.get(cid)
        if cur is None or (ts, order) > (cur[0], cur[1]):
            staged[cid] = (ts, order, str(row.get("verdict") or ""))
    return {cid: v for cid, (_, _, v) in staged.items()}


def classify_issue_text(text: str) -> DropReasonCode:
    t = text.lower()
    if any(
        k in t
        for k in (
            "no public",
            "accession",
            "not deposited",
            "no dataset",
            "no pxd",
            "data not",
            "unavailable data",
        )
    ):
        return DropReasonCode.issue_no_public_data
    if any(
        k in t
        for k in (
            "metric",
            "run id",
            "run-level",
            "qc table",
            "no numeric",
            "threshold",
            "identifier",
        )
    ):
        return DropReasonCode.issue_missing_metrics
    if any(k in t for k in ("ground truth", "gold", "cannot instantiate", "unspecified gt")):
        return DropReasonCode.issue_ground_truth_unspecified
    if any(k in t for k in ("abstract", "full text", "fulltext", "thin", "retrieved text")):
        return DropReasonCode.issue_abstract_only
    if any(k in t for k in ("poor fit", "does not fit", "wrong", "not match", "mismatch")):
        return DropReasonCode.issue_wrong_fit
    if any(k in t for k in ("open-ended", "open ended", "essay", "not bounded", "too broad")):
        return DropReasonCode.issue_open_ended
    return DropReasonCode.issue_other


def inputs_marked_missing(card: dict[str, Any]) -> bool:
    for item in card.get("required_inputs") or []:
        if not isinstance(item, dict):
            continue
        blob = f"{item.get('role', '')} {item.get('likely_format', '')}"
        if MISSING_INPUT_RE.search(blob):
            return True
    q = str(card.get("bounded_question") or "")
    if MISSING_INPUT_RE.search(q) and "cannot" in q.lower():
        return True
    return False


def candidate_drop_codes(card: dict[str, Any], verdict: str | None, has_stub: bool) -> list[DropReasonCode]:
    codes: list[DropReasonCode] = []
    status = str(card.get("extract_status") or "")
    if status and status != "ok":
        codes.append(DropReasonCode.extract_status_not_ok)

    acc = card.get("public_data_accessions") or []
    if not acc:
        codes.append(DropReasonCode.no_public_accession)

    feas = str(card.get("data_feasibility") or "unknown").lower()
    if feas == "low":
        codes.append(DropReasonCode.data_feasibility_low)
    elif feas in ("", "unknown"):
        codes.append(DropReasonCode.data_feasibility_unknown)

    if not (card.get("required_inputs") or []):
        codes.append(DropReasonCode.required_inputs_empty)
    elif inputs_marked_missing(card):
        codes.append(DropReasonCode.inputs_marked_missing)

    if str(card.get("scientific_plausibility") or "").lower() == "low":
        codes.append(DropReasonCode.scientific_plausibility_low)

    via = str(card.get("viability") or "").lower()
    if via == "low":
        codes.append(DropReasonCode.viability_low)
    elif via == "medium":
        codes.append(DropReasonCode.viability_medium)

    if verdict is None:
        codes.append(DropReasonCode.review_unreviewed)
    elif verdict == "revise":
        codes.append(DropReasonCode.review_revise)
    elif verdict == "reject":
        codes.append(DropReasonCode.review_reject)

    if not has_stub:
        codes.append(DropReasonCode.stub_not_emitted)

    return codes


def survived_to_stage(card: dict[str, Any], verdict: str | None, has_stub: bool) -> StageId:
    if has_stub:
        return StageId.eval_stub
    if verdict == "keep":
        return StageId.review_keep
    if str(card.get("viability") or "").lower() == "high" and is_data_gated(card):
        # high viability data-gated but not kept / stubbed
        if verdict in (None, "revise", "reject"):
            return StageId.viability_high
    if is_data_gated(card):
        return StageId.data_gated
    if str(card.get("extract_status") or "") == "ok":
        # ok but failed data gate
        return StageId.candidates_ok
    return StageId.candidates_extracted


def is_data_gated(card: dict[str, Any]) -> bool:
    if str(card.get("extract_status") or "") != "ok":
        return False
    if not (card.get("public_data_accessions") or []):
        return False
    feas = str(card.get("data_feasibility") or "unknown").lower()
    if feas not in ("high", "medium"):
        return False
    if inputs_marked_missing(card):
        return False
    return True


def load_cost(costs_dir: Path) -> CostSummary:
    path = costs_dir / "calls.csv"
    if not path.exists():
        return CostSummary()
    n = 0
    inn = 0
    out = 0
    by_status: Counter[str] = Counter()
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            n += 1
            try:
                inn += int(float(row.get("input_tokens") or 0))
            except ValueError:
                pass
            try:
                out += int(float(row.get("output_tokens") or 0))
            except ValueError:
                pass
            by_status[str(row.get("status") or "unknown")] += 1
    return CostSummary(
        n_calls=n,
        input_tokens=inn,
        output_tokens=out,
        by_status=dict(by_status),
    )


def _reason(code: DropReasonCode, count: int, examples: list[str] | None = None) -> DropReason:
    return DropReason(
        code=code,
        count=count,
        label=DROP_REASON_LABELS.get(code, code.value),
        examples=(examples or [])[:5],
    )


def build_stage(
    stage_id: StageId,
    entered: int,
    passed: int,
    reasons: list[DropReason],
    prev_passed: int | None,
    top_passed: int | None,
) -> StageCount:
    meta = STAGE_META[stage_id]
    dropped = max(0, entered - passed)
    conv_prev = None
    if prev_passed and prev_passed > 0:
        conv_prev = round(passed / prev_passed, 4)
    conv_top = None
    if top_passed and top_passed > 0:
        conv_top = round(passed / top_passed, 4)
    return StageCount(
        stage_id=stage_id,
        label=meta["label"],
        unit=meta["unit"],
        description=meta["description"],
        entered=entered,
        passed=passed,
        dropped=dropped,
        conversion_from_prev=conv_prev,
        conversion_from_top=conv_top,
        drop_reasons=sorted(reasons, key=lambda r: (-r.count, r.code.value)),
    )


def compute_snapshot(
    cfg: dict[str, Any],
    feedback_path: Path | None = None,
) -> YieldSnapshot:
    paths = cfg["paths"]
    cube_path = Path(cfg["cube_csv"])
    queue_dir = Path(paths["queue_dir"])
    papers_dir = Path(paths["papers_dir"])
    ranked_dir = Path(paths["ranked_dir"])
    candidates_dir = Path(paths["candidates_dir"])
    dataset_dir = Path(paths["dataset_dir"])
    costs_dir = Path(paths["costs_dir"])
    top_k = int(cfg.get("top_k_for_claude", 5))
    batches = list(cfg.get("batches") or [])
    only_final = bool(cfg.get("only_final_selection", True))

    notes: list[str] = []
    if not cube_path.exists():
        raise FileNotFoundError(f"cube CSV missing: {cube_path}")

    df = pd.read_csv(cube_path)
    n_inventory = len(df)

    if "final_selection_target_v0" in df.columns:
        df_priority = df[df["final_selection_target_v0"] > 0]
    else:
        df_priority = df
        notes.append("final_selection_target_v0 column missing; priority = all cells")
    n_priority = len(df_priority)

    if batches and "batch_id" in df_priority.columns:
        df_scope = df_priority[df_priority["batch_id"].isin(batches)]
    else:
        df_scope = df_priority
    n_scope = len(df_scope)

    # Cube drop reasons (inventory → priority → scope)
    n_not_priority = n_inventory - n_priority
    n_out_batch = n_priority - n_scope
    weak_viability = 0
    if "cell_viability" in df.columns:
        viab = df["cell_viability"].astype(str).str.lower().isin(
            ["weak", "poor", "low", "natural"]
        )
        if "final_selection_target_v0" in df.columns:
            weak_viability = int((viab & (df["final_selection_target_v0"] > 0)).sum())
        else:
            weak_viability = int(viab.sum())

    packs_path = queue_dir / "search_packs.jsonl"
    packs = read_jsonl(packs_path) if packs_path.exists() else []
    if not packs_path.exists():
        notes.append("search_packs.jsonl missing — run export_queue.py")
    pack_by_cell = {p["cube_cell_id"]: p for p in packs}
    n_packs = len(packs)

    # Papers
    papers_by_cell: dict[str, list[Path]] = {}
    if papers_dir.exists():
        for cell_dir in papers_dir.iterdir():
            if cell_dir.is_dir():
                papers_by_cell[cell_dir.name] = list(cell_dir.glob("*.json"))
    n_papers = sum(len(v) for v in papers_by_cell.values())

    # Ranked
    ranked_by_cell: dict[str, list[dict[str, Any]]] = {}
    for pack in packs:
        cell = pack["cube_cell_id"]
        rpath = ranked_dir / f"{cell}.json"
        if rpath.exists():
            data = read_json(rpath)
            ranked_by_cell[cell] = list(data.get("papers") or [])
        else:
            ranked_by_cell[cell] = []
    n_ranked = sum(len(v) for v in ranked_by_cell.values())

    # Candidates
    candidates: list[dict[str, Any]] = []
    if (dataset_dir / "candidates.jsonl").exists():
        candidates = read_jsonl(dataset_dir / "candidates.jsonl")
    elif candidates_dir.exists():
        for path in sorted(candidates_dir.glob("*/*.json")):
            try:
                candidates.append(read_json(path))
            except Exception:
                continue
    n_cand = len(candidates)
    n_ok = sum(1 for c in candidates if c.get("extract_status") == "ok")

    fb_path = feedback_path or DEFAULT_FEEDBACK
    feedback = read_jsonl(fb_path) if fb_path.exists() else []
    if not fb_path.exists():
        notes.append(f"feedback file missing: {fb_path}")
    verdicts = _latest_verdict(feedback)

    stub_dir = dataset_dir / "eval_stubs"
    stub_ids: set[str] = set()
    if stub_dir.exists():
        for p in stub_dir.glob("*.json"):
            try:
                stub_ids.add(str(read_json(p).get("id") or p.stem))
            except Exception:
                stub_ids.add(p.stem)

    n_data = sum(1 for c in candidates if is_data_gated(c))
    n_high = sum(1 for c in candidates if str(c.get("viability") or "").lower() == "high")
    # Aggressive intersection often used as "true keep funnel"
    n_high_and_data = sum(
        1
        for c in candidates
        if str(c.get("viability") or "").lower() == "high" and is_data_gated(c)
    )
    n_keep = sum(1 for c in candidates if verdicts.get(c.get("candidate_id")) == "keep")
    n_stubs = sum(1 for c in candidates if c.get("candidate_id") in stub_ids)
    if not n_stubs and stub_dir.exists():
        n_stubs = len(list(stub_dir.glob("*.json")))

    # --- Stages ---
    stages: list[StageCount] = []
    # S00 inventory
    stages.append(
        build_stage(
            StageId.cube_inventory,
            entered=n_inventory,
            passed=n_inventory,
            reasons=[],
            prev_passed=None,
            top_passed=n_inventory,
        )
    )
    # S01 priority
    stages.append(
        build_stage(
            StageId.cube_priority,
            entered=n_inventory,
            passed=n_priority,
            reasons=[_reason(DropReasonCode.cube_not_priority, n_not_priority)]
            if n_not_priority
            else [],
            prev_passed=n_inventory,
            top_passed=n_inventory,
        )
    )
    # S02 pilot scope
    batch_reasons = []
    if n_out_batch:
        batch_reasons.append(_reason(DropReasonCode.cube_batch_out_of_scope, n_out_batch))
    if weak_viability:
        batch_reasons.append(_reason(DropReasonCode.cube_weak_cell_viability, weak_viability))
    stages.append(
        build_stage(
            StageId.cube_pilot_scope,
            entered=n_priority,
            passed=n_scope,
            reasons=batch_reasons,
            prev_passed=n_priority,
            top_passed=n_inventory,
        )
    )
    # S03 packs
    pack_drop = max(0, n_scope - n_packs)
    stages.append(
        build_stage(
            StageId.search_packs,
            entered=n_scope,
            passed=n_packs,
            reasons=[_reason(DropReasonCode.pack_not_exported, pack_drop)] if pack_drop else [],
            prev_passed=n_scope,
            top_passed=n_inventory,
        )
    )
    # S04 papers (entered = theoretical pack slots isn't cells; report papers count,
    # drop cells with zero papers)
    empty_fetch_cells = [cid for cid in pack_by_cell if not papers_by_cell.get(cid)]
    stages.append(
        build_stage(
            StageId.papers_fetched,
            entered=n_papers + len(empty_fetch_cells),  # conceptual: papers + empty cells
            passed=n_papers,
            reasons=[
                _reason(
                    DropReasonCode.no_papers_fetched,
                    len(empty_fetch_cells),
                    empty_fetch_cells,
                )
            ]
            if empty_fetch_cells
            else [],
            prev_passed=n_packs,
            top_passed=n_inventory,
        )
    )
    # S05 ranked
    below_k = max(0, n_papers - n_ranked)
    missing_rank = [cid for cid in pack_by_cell if not (ranked_dir / f"{cid}.json").exists()]
    rank_reasons = []
    if below_k:
        rank_reasons.append(_reason(DropReasonCode.paper_below_top_k, below_k))
    if missing_rank:
        rank_reasons.append(
            _reason(DropReasonCode.ranked_missing, len(missing_rank), missing_rank)
        )
    stages.append(
        build_stage(
            StageId.papers_ranked,
            entered=n_papers,
            passed=n_ranked,
            reasons=rank_reasons,
            prev_passed=n_papers,
            top_passed=n_inventory,
        )
    )
    # S06 extracted
    extract_gap = max(0, n_ranked - n_cand)
    stages.append(
        build_stage(
            StageId.candidates_extracted,
            entered=n_ranked,
            passed=n_cand,
            reasons=[_reason(DropReasonCode.extract_missing, extract_gap)] if extract_gap else [],
            prev_passed=n_ranked,
            top_passed=n_inventory,
        )
    )
    # S07 ok
    not_ok = n_cand - n_ok
    stages.append(
        build_stage(
            StageId.candidates_ok,
            entered=n_cand,
            passed=n_ok,
            reasons=[_reason(DropReasonCode.extract_status_not_ok, not_ok)] if not_ok else [],
            prev_passed=n_cand,
            top_passed=n_inventory,
        )
    )
    # S08 data gated
    data_drop_counter: Counter[DropReasonCode] = Counter()
    data_examples: dict[DropReasonCode, list[str]] = defaultdict(list)
    for c in candidates:
        if is_data_gated(c):
            continue
        cid = str(c.get("candidate_id") or "")
        if str(c.get("extract_status") or "") != "ok":
            continue  # already counted earlier
        for code in candidate_drop_codes(c, verdicts.get(cid), cid in stub_ids):
            if code in (
                DropReasonCode.no_public_accession,
                DropReasonCode.data_feasibility_low,
                DropReasonCode.data_feasibility_unknown,
                DropReasonCode.required_inputs_empty,
                DropReasonCode.inputs_marked_missing,
            ):
                data_drop_counter[code] += 1
                if len(data_examples[code]) < 5:
                    data_examples[code].append(cid)
    data_reasons = [
        _reason(code, cnt, data_examples.get(code)) for code, cnt in data_drop_counter.items()
    ]
    stages.append(
        build_stage(
            StageId.data_gated,
            entered=n_ok,
            passed=n_data,
            reasons=data_reasons,
            prev_passed=n_ok,
            top_passed=n_inventory,
        )
    )
    # S09 viability high (of all candidates; aggressive)
    via_counter: Counter[DropReasonCode] = Counter()
    via_ex: dict[DropReasonCode, list[str]] = defaultdict(list)
    for c in candidates:
        via = str(c.get("viability") or "").lower()
        cid = str(c.get("candidate_id") or "")
        if via == "high":
            continue
        code = DropReasonCode.viability_low if via == "low" else DropReasonCode.viability_medium
        via_counter[code] += 1
        if len(via_ex[code]) < 5:
            via_ex[code].append(cid)
    stages.append(
        build_stage(
            StageId.viability_high,
            entered=n_cand,
            passed=n_high,
            reasons=[_reason(c, n, via_ex.get(c)) for c, n in via_counter.items()],
            prev_passed=n_data,
            top_passed=n_inventory,
        )
    )
    # S10 review keep
    rev_counter: Counter[DropReasonCode] = Counter()
    rev_ex: dict[DropReasonCode, list[str]] = defaultdict(list)
    for c in candidates:
        cid = str(c.get("candidate_id") or "")
        v = verdicts.get(cid)
        if v == "keep":
            continue
        if v == "revise":
            code = DropReasonCode.review_revise
        elif v == "reject":
            code = DropReasonCode.review_reject
        else:
            code = DropReasonCode.review_unreviewed
        rev_counter[code] += 1
        if len(rev_ex[code]) < 5:
            rev_ex[code].append(cid)
    stages.append(
        build_stage(
            StageId.review_keep,
            entered=n_cand,
            passed=n_keep,
            reasons=[_reason(c, n, rev_ex.get(c)) for c, n in rev_counter.items()],
            prev_passed=n_high,
            top_passed=n_inventory,
        )
    )
    # S11 stubs
    stub_drop = max(0, n_high - n_stubs)  # stubs currently only for high viability
    stages.append(
        build_stage(
            StageId.eval_stub,
            entered=n_high if n_high else n_cand,
            passed=n_stubs,
            reasons=[_reason(DropReasonCode.stub_not_emitted, stub_drop)] if stub_drop else [],
            prev_passed=n_keep if n_keep else n_high,
            top_passed=n_inventory,
        )
    )

    # --- Per-candidate rows ---
    cand_rows: list[CandidateYieldRow] = []
    issue_counter: Counter[DropReasonCode] = Counter()
    issue_examples: dict[DropReasonCode, list[str]] = defaultdict(list)

    for c in candidates:
        cid = str(c.get("candidate_id") or "")
        cell = str(c.get("cube_cell_id") or "")
        pack = pack_by_cell.get(cell, {})
        v = verdicts.get(cid)
        has_stub = cid in stub_ids
        drops = candidate_drop_codes(c, v, has_stub)
        issue_codes: list[DropReasonCode] = []
        for iss in c.get("issues") or []:
            code = classify_issue_text(str(iss))
            issue_codes.append(code)
            issue_counter[code] += 1
            if len(issue_examples[code]) < 8 and cid not in issue_examples[code]:
                issue_examples[code].append(cid)
        cand_rows.append(
            CandidateYieldRow(
                candidate_id=cid,
                cube_cell_id=cell,
                batch_id=pack.get("batch_id") or c.get("batch_id"),
                extract_status=c.get("extract_status"),
                viability=c.get("viability"),
                data_feasibility=c.get("data_feasibility"),
                scientific_plausibility=c.get("scientific_plausibility"),
                n_accessions=len(c.get("public_data_accessions") or []),
                n_required_inputs=len(c.get("required_inputs") or []),
                review_verdict=v,
                has_stub=has_stub,
                survived_to=survived_to_stage(c, v, has_stub),
                drop_codes=drops,
                issue_codes=issue_codes,
                issues=[str(x) for x in (c.get("issues") or [])][:8],
                public_data_accessions=[str(x) for x in (c.get("public_data_accessions") or [])],
                bounded_question=str(c.get("bounded_question") or "")[:500],
                archetype_title=str(c.get("archetype_title") or pack.get("archetype_title") or ""),
            )
        )

    issue_buckets = [
        IssueBucket(
            code=code,
            label=DROP_REASON_LABELS.get(code, code.value),
            count=cnt,
            example_candidate_ids=issue_examples.get(code, [])[:8],
        )
        for code, cnt in sorted(issue_counter.items(), key=lambda x: -x[1])
    ]

    # --- Per-cell ---
    cells: list[CellYield] = []
    scope_ids = set(df_scope["cube_cell_id"].astype(str)) if n_scope else set()
    # Prefer packs; also include scope cells without packs
    cell_ids = sorted(set(pack_by_cell) | scope_ids)
    cands_by_cell: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        cands_by_cell[str(c.get("cube_cell_id"))].append(c)

    for cell in cell_ids:
        pack = pack_by_cell.get(cell, {})
        row = df[df["cube_cell_id"].astype(str) == cell]
        crow = row.iloc[0].to_dict() if len(row) else {}
        cs = cands_by_cell.get(cell, [])
        n_via_h = sum(1 for c in cs if str(c.get("viability") or "").lower() == "high")
        n_via_m = sum(1 for c in cs if str(c.get("viability") or "").lower() == "medium")
        n_via_l = sum(1 for c in cs if str(c.get("viability") or "").lower() == "low")
        keeps = sum(1 for c in cs if verdicts.get(c.get("candidate_id")) == "keep")
        revs = sum(1 for c in cs if verdicts.get(c.get("candidate_id")) == "revise")
        rejs = sum(1 for c in cs if verdicts.get(c.get("candidate_id")) == "reject")
        unrev = sum(1 for c in cs if c.get("candidate_id") not in verdicts)
        stubs = sum(1 for c in cs if c.get("candidate_id") in stub_ids)

        # bottleneck: first zero among pipeline metrics
        metrics_seq = [
            (StageId.papers_fetched, len(papers_by_cell.get(cell, []))),
            (StageId.papers_ranked, len(ranked_by_cell.get(cell, []))),
            (StageId.candidates_extracted, len(cs)),
            (StageId.candidates_ok, sum(1 for c in cs if c.get("extract_status") == "ok")),
            (StageId.data_gated, sum(1 for c in cs if is_data_gated(c))),
            (StageId.viability_high, n_via_h),
            (StageId.review_keep, keeps),
            (StageId.eval_stub, stubs),
        ]
        bottleneck = None
        for sid, val in metrics_seq:
            if val == 0:
                bottleneck = sid
                break

        # primary drop codes across candidates
        code_c: Counter[DropReasonCode] = Counter()
        for c in cs:
            cid = str(c.get("candidate_id") or "")
            for code in candidate_drop_codes(c, verdicts.get(cid), cid in stub_ids):
                if code in (
                    DropReasonCode.no_public_accession,
                    DropReasonCode.data_feasibility_low,
                    DropReasonCode.data_feasibility_unknown,
                    DropReasonCode.viability_low,
                    DropReasonCode.viability_medium,
                    DropReasonCode.inputs_marked_missing,
                    DropReasonCode.extract_status_not_ok,
                ):
                    code_c[code] += 1
        if not cs and not papers_by_cell.get(cell):
            code_c[DropReasonCode.no_papers_fetched] += 1
        elif not cs:
            code_c[DropReasonCode.extract_missing] += 1

        cells.append(
            CellYield(
                cube_cell_id=cell,
                batch_id=str(pack.get("batch_id") or crow.get("batch_id") or "") or None,
                analytical_category=pack.get("analytical_category")
                or crow.get("analytical_category"),
                evidence_family=pack.get("evidence_family") or crow.get("evidence_family"),
                archetype_title=pack.get("archetype_title") or crow.get("archetype_title"),
                cell_viability=str(pack.get("cell_viability") or crow.get("cell_viability") or "")
                or None,
                final_selection_target_v0=int(crow["final_selection_target_v0"])
                if crow.get("final_selection_target_v0") == crow.get("final_selection_target_v0")
                and crow.get("final_selection_target_v0") is not None
                else pack.get("final_selection_target_v0"),
                candidate_generation_target=pack.get("candidate_generation_target")
                or (
                    int(crow["candidate_generation_target"])
                    if crow.get("candidate_generation_target")
                    == crow.get("candidate_generation_target")
                    and crow.get("candidate_generation_target") is not None
                    else None
                ),
                n_papers_fetched=len(papers_by_cell.get(cell, [])),
                n_papers_ranked=len(ranked_by_cell.get(cell, [])),
                n_candidates=len(cs),
                n_candidates_ok=sum(1 for c in cs if c.get("extract_status") == "ok"),
                n_with_accession=sum(1 for c in cs if c.get("public_data_accessions")),
                n_data_gated=sum(1 for c in cs if is_data_gated(c)),
                n_viability_high=n_via_h,
                n_viability_medium=n_via_m,
                n_viability_low=n_via_l,
                n_review_keep=keeps,
                n_review_revise=revs,
                n_review_reject=rejs,
                n_review_unreviewed=unrev,
                n_stubs=stubs,
                bottleneck_stage=bottleneck,
                primary_drop_codes=[c for c, _ in code_c.most_common(5)],
            )
        )

    cost = load_cost(costs_dir)

    kpis = {
        "cube_inventory": n_inventory,
        "cube_priority": n_priority,
        "cube_pilot_scope": n_scope,
        "search_packs": n_packs,
        "papers_fetched": n_papers,
        "papers_ranked": n_ranked,
        "candidates": n_cand,
        "candidates_ok": n_ok,
        "data_gated": n_data,
        "viability_high": n_high,
        "viability_high_and_data": n_high_and_data,
        "review_keep": n_keep,
        "eval_stubs": n_stubs,
        "pct_priority_of_inventory": round(100 * n_priority / n_inventory, 2) if n_inventory else 0,
        "pct_data_of_ok": round(100 * n_data / n_ok, 2) if n_ok else 0,
        "pct_high_of_candidates": round(100 * n_high / n_cand, 2) if n_cand else 0,
        "pct_accessions_of_ok": round(
            100
            * sum(1 for c in candidates if c.get("extract_status") == "ok" and c.get("public_data_accessions"))
            / n_ok,
            2,
        )
        if n_ok
        else 0,
        "yield_inventory_to_high": round(100 * n_high / n_inventory, 4) if n_inventory else 0,
        "yield_scope_to_data": round(100 * n_data / n_scope, 2) if n_scope else 0,
        "yield_ranked_to_high": round(100 * n_high / n_ranked, 2) if n_ranked else 0,
    }

    return YieldSnapshot(
        schema_version="1.0.0",
        generated_at=utc_now_iso(),
        run_root=str(Path(paths["dataset_dir"]).parent.resolve()),
        config_batches=batches,
        only_final_selection=only_final,
        top_k_for_claude=top_k,
        stages=stages,
        cells=sorted(cells, key=lambda x: x.cube_cell_id),
        candidates=sorted(cand_rows, key=lambda x: x.candidate_id),
        issue_buckets=issue_buckets,
        cost=cost,
        kpis=kpis,
        notes=notes,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute mining pipeline yield snapshot")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSON path (default: out/observability/yield_snapshot.json)",
    )
    parser.add_argument(
        "--feedback",
        type=Path,
        default=None,
        help="Path to review feedback.jsonl",
    )
    parser.add_argument("--pretty", action="store_true", help="Print KPI summary to stdout")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except Exception as e:
        print(f"ERROR loading config: {e}", file=sys.stderr)
        return 1

    # Ensure observability dir is known
    obs_dir = Path(cfg["paths"].get("observability_dir") or (Path(cfg["paths"]["dataset_dir"]).parent / "observability"))
    if not obs_dir.is_absolute():
        obs_dir = Path(__file__).resolve().parent / obs_dir
    obs_dir.mkdir(parents=True, exist_ok=True)
    out = args.out or (obs_dir / "yield_snapshot.json")

    try:
        snap = compute_snapshot(cfg, feedback_path=args.feedback)
    except Exception as e:
        print(f"ERROR computing yield: {e}", file=sys.stderr)
        raise

    # Validate round-trip
    snap = YieldSnapshot.model_validate(snap.model_dump())
    write_json(out, snap.model_dump(mode="json"))
    # Also write latest pointer
    write_json(obs_dir / "latest.json", {"path": str(out.resolve()), "generated_at": snap.generated_at})

    print(f"Wrote {out}")
    if args.pretty or True:
        k = snap.kpis
        print(
            "Funnel: "
            f"inventory={k.get('cube_inventory')} → priority={k.get('cube_priority')} → "
            f"scope={k.get('cube_pilot_scope')} → packs={k.get('search_packs')} → "
            f"papers={k.get('papers_fetched')} → ranked={k.get('papers_ranked')} → "
            f"cands={k.get('candidates')} → ok={k.get('candidates_ok')} → "
            f"data={k.get('data_gated')} → high={k.get('viability_high')} → "
            f"keep={k.get('review_keep')} → stubs={k.get('eval_stubs')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
