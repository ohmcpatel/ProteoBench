"""Shared helpers for paper_mine stages."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parent


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or (ROOT / "config.yaml")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Resolve relative paths against paper_mine/
    cube = Path(cfg["cube_csv"])
    if not cube.is_absolute():
        cfg["cube_csv"] = str((ROOT / cube).resolve())
    paths = cfg.setdefault("paths", {})
    for key, default in {
        "queue_dir": "queue",
        "papers_dir": "out/papers",
        "ranked_dir": "out/ranked",
        "candidates_dir": "out/candidates",
        "dataset_dir": "out/dataset",
        "costs_dir": "out/costs",
    }.items():
        p = Path(paths.get(key, default))
        if not p.is_absolute():
            p = ROOT / p
        paths[key] = str(p.resolve())
    return cfg


def ensure_dirs(cfg: dict[str, Any]) -> None:
    for key in cfg["paths"].values():
        Path(key).mkdir(parents=True, exist_ok=True)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.loads(f.read())


def parse_json_maybe(value: Any) -> Any:
    if value is None or (isinstance(value, float) and str(value) == "nan"):
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s or s.lower() == "nan":
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return value
    return value


def slug_words(text: str, n: int = 8) -> list[str]:
    stop = {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "for",
        "in",
        "on",
        "with",
        "from",
        "by",
        "is",
        "are",
        "be",
        "as",
        "that",
        "this",
        "using",
        "whether",
        "into",
        "one",
        "two",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-/+]{1,}", text)
    out: list[str] = []
    for w in words:
        lw = w.lower()
        if lw in stop:
            continue
        if lw not in {x.lower() for x in out}:
            out.append(w)
        if len(out) >= n:
            break
    return out


def build_seed_queries(
    title: str,
    evidence_family: str,
    acquisition_tags: list[str],
    experiment_tags: list[str],
    max_queries: int = 4,
) -> list[str]:
    """Build Europe PMC / PubMed-style free-text queries for a cube cell."""
    title_kw = " ".join(slug_words(title, 6))
    modality = evidence_family.split(":")[0].split("/")[0].strip()
    acq = " ".join(acquisition_tags[:3]) if acquisition_tags else ""
    exp = experiment_tags[0] if experiment_tags else "proteomics"

    candidates = [
        f"({title_kw}) AND proteomics",
        f"({modality}) AND ({title_kw}) AND proteomics",
        f"({acq}) AND ({slug_words(title, 4) and ' '.join(slug_words(title, 4))}) AND mass spectrometry"
        if acq
        else f"({modality}) AND quality control AND proteomics",
        f"({exp}) AND ({modality}) AND proteomics",
    ]
    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for q in candidates:
        qn = re.sub(r"\s+", " ", q).strip()
        if qn and qn not in seen:
            seen.add(qn)
            out.append(qn)
        if len(out) >= max_queries:
            break
    return out


def filter_packs(
    packs: list[dict[str, Any]],
    batches: list[str] | None = None,
    cell: str | None = None,
) -> list[dict[str, Any]]:
    out = packs
    if batches:
        bset = set(batches)
        out = [p for p in out if p.get("batch_id") in bset]
    if cell:
        out = [p for p in out if p.get("cube_cell_id") == cell]
    return out
