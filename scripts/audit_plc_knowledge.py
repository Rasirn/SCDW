"""Audit published LAD knowledge governance without fuzzy retrieval."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from scdw.common.paths import RAG_APPLICATION_RAW_DIR, RAG_KNOWLEDGE_DIR
from scdw.rag import KnowledgeLibrary


def audit() -> dict:
    library = KnowledgeLibrary(RAG_KNOWLEDGE_DIR)
    catalog = library.catalog()["items"]
    bodies = library.get_many([item["id"] for item in catalog])
    by_hash: dict[str, list[str]] = defaultdict(list)
    for item in bodies:
        by_hash[item["content_sha256"]].append(item["id"])
    exact_duplicates = [ids for ids in by_hash.values() if len(ids) > 1]
    near_duplicates = []
    for index, left in enumerate(catalog):
        left_caps = set(left.get("provides", []))
        for right in catalog[index + 1:]:
            right_caps = set(right.get("provides", []))
            union = left_caps | right_caps
            similarity = len(left_caps & right_caps) / len(union) if union else 0
            if similarity >= .9:
                near_duplicates.append({"left": left["id"], "right": right["id"], "provides_jaccard": round(similarity, 3)})
    distillation = json.loads((RAG_KNOWLEDGE_DIR / "distillation.json").read_text(encoding="utf-8"))
    traced = {str((PROJECT_ROOT / row["source"]).resolve()) for row in distillation.get("sources", [])}
    raw = {str(path.resolve()) for path in RAG_APPLICATION_RAW_DIR.glob("*.xml")}
    coverage = json.loads((RAG_KNOWLEDGE_DIR / "application_coverage.json").read_text(encoding="utf-8"))
    missing_evidence = [item["id"] for item in catalog if not item.get("source_refs")]
    experimental = [item["id"] for item in catalog if item.get("status") == "draft"]
    report = {
        "schema_version": 1,
        "catalog_sha256": hashlib.sha256(json.dumps(catalog, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "counts": {"catalog_items": len(catalog), "raw_application_files": len(raw)},
        "raw_not_distilled": sorted(raw - traced),
        "distillation_missing_raw": sorted(traced - raw),
        "uncovered": coverage.get("catalog_comparison", {}).get("uncovered", {}),
        "metadata_xml_mismatches": [],
        "exact_duplicate_content": exact_duplicates,
        "near_duplicate_capabilities": near_duplicates,
        "missing_source_evidence": missing_evidence,
        "experimental_items": experimental,
        "verification_states": {
            item["id"]: KnowledgeLibrary._verification_states(item) for item in catalog
        },
        "success": not (raw - traced) and not missing_evidence and not exact_duplicates,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=RAG_KNOWLEDGE_DIR / "audit_report.json")
    args = parser.parse_args()
    value = audit()
    args.output.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
