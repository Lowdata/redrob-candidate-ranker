#!/usr/bin/env python3
"""
rank.py — produces the top-100 ranked CSV submission for the Redrob hackathon.

Usage:
    python rank.py --candidates ./candidates.jsonl --jd ./job_description.md --out ./submission.csv
    python rank.py --candidates ./candidates.jsonl.gz --jd ./job_description.md --out ./submission.csv

    # optional, NOT part of the official submission -- a wider per-candidate
    # breakdown (component scores, flags, evidence sources) for your own
    # spot-checking and Stage-5 interview prep:
    python rank.py --candidates ./candidates.jsonl --jd ./job_description.md --out ./submission.csv --debug-out ./debug_report.csv

Design constraints honored:
    - CPU only, no GPU calls anywhere in this file or its imports.
    - No network calls anywhere in this file or its imports.
    - Designed to run on 100K candidates within 5 min / 16 GB on a laptop-class CPU.

This script is intentionally a thin orchestration layer. All scoring logic
lives in features.py / scorer.py / jd_requirements.py so it's reviewable and
testable on its own.

v2 change: the `resource` module is POSIX-only (no-op on native Windows).
Previously this script imported it unconditionally, so peak_rss_mb() would
crash on Windows. It's now optional with a graceful fallback -- harmless on
the typical Linux-based sandbox platforms (HF Spaces, Colab, Replit, Docker),
but avoids a hard crash if anyone runs this on Windows directly.
"""

import argparse
import csv
import gzip
import json
import platform
import re
import sys
import time
from pathlib import Path

try:
    import resource
    _HAS_RESOURCE = True
except ImportError:  # Windows has no `resource` module
    resource = None
    _HAS_RESOURCE = False

import features as feat
import scorer


def load_candidates(path: Path):
    """Stream candidates from .jsonl or .jsonl.gz without loading the whole
    file into memory as text (still fine at 100K rows / ~465MB uncompressed,
    but streaming keeps headroom under the 16GB cap)."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_candidates_from_json_array(path: Path):
    """Fallback loader for the sample_candidates.json pretty-printed array format."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for c in data:
        yield c


def load_jd_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text


def build_jd_tokens(jd_text: str) -> set:
    """Tokenize the JD body for the minor semantic-similarity signal.
    We deliberately strip the hackathon-participant meta-section (the part
    addressed to participants, not describing the role) so it doesn't pollute
    the token overlap with words like 'hackathon', 'dataset', 'keyword'."""
    cutoff_markers = [
        "Final note for the participants",
        "Final note for the participants of the Redrob hackathon",
    ]
    text = jd_text
    for marker in cutoff_markers:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
            break
    return feat._tokenize(text)


def detect_input_format(path: Path) -> str:
    if path.suffix == ".gz":
        return "jsonl_gz"
    if path.suffix == ".jsonl":
        return "jsonl"
    if path.suffix == ".json":
        # could be a JSON array (sample_candidates.json) — sniff first char
        with open(path, "r", encoding="utf-8") as f:
            first = f.read(1)
        return "json_array" if first == "[" else "jsonl"
    raise ValueError(f"Unrecognized candidates file extension: {path.suffix}")


def rank_candidates(candidates_path: Path, jd_path: Path):
    jd_text = load_jd_text(jd_path)
    jd_tokens = build_jd_tokens(jd_text)

    fmt = detect_input_format(candidates_path)
    if fmt == "json_array":
        stream = load_candidates_from_json_array(candidates_path)
    else:
        stream = load_candidates(candidates_path)

    scored = []
    n = 0
    for candidate in stream:
        n += 1
        try:
            result = scorer.score_candidate(candidate, jd_tokens)
        except Exception as e:  # never let one malformed record kill the run
            sys.stderr.write(f"WARN: skipping {candidate.get('candidate_id', '?')}: {e}\n")
            continue
        scored.append((candidate, result))

    sys.stderr.write(f"Scored {len(scored)} / {n} candidates.\n")
    return scored


def _ranked_top(scored, top_n: int):
    """Shared by write_submission_csv and write_debug_csv so the two outputs
    are always in identical order -- same rounded-score-then-candidate_id
    sort, computed once."""
    enriched = [
        (candidate, result, round(result["final_score"], 4))
        for candidate, result in scored
    ]
    enriched.sort(key=lambda cr: (-cr[2], cr[0]["candidate_id"]))
    return enriched[:top_n]


def write_submission_csv(scored, out_path: Path, top_n: int = 100):
    # CRITICAL: sort on the SAME rounded value that gets written to the CSV.
    # Two candidates can have different raw final_score floats that round to
    # an identical 4-decimal display value — if we sort on the raw float but
    # the validator checks tie-break ordering on the rounded score actually
    # written, a "tie" that only appears after rounding can come out in the
    # wrong candidate_id order. Round first, then sort, so what we compare is
    # exactly what ends up on disk.
    top = _ranked_top(scored, top_n)
    if len(top) < top_n:
        sys.stderr.write(
            f"WARN: only {len(top)} candidates available, fewer than required {top_n}.\n"
        )

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["candidate_id", "rank", "score", "reasoning"])
        for rank, (candidate, result, score) in enumerate(top, start=1):
            reasoning = scorer.build_reasoning(candidate, result)
            writer.writerow([candidate["candidate_id"], rank, f"{score:.4f}", reasoning])

    sys.stderr.write(f"Wrote {len(top)} rows to {out_path}\n")


DEBUG_FIELDS = [
    "candidate_id", "rank", "score", "title_class", "skill_score",
    "exp_score", "semantic_score", "behavior_score", "location_score",
    "trajectory_score", "trajectory_convergence", "seniority_non_decreasing",
    "disqualifier_flags", "consistency_flags", "evidence_source_count",
    "evidence_sources", "shipped_phrase_hits", "days_inactive",
    "response_rate", "notice_period_days",
]


def write_debug_csv(scored, out_path: Path, top_n: int = 100):
    """
    OPT-IN, NOT part of the official submission. submission_spec.md Section 2
    fixes the submission CSV header to exactly
    candidate_id,rank,score,reasoning -- writing extra columns into that file
    would fail the auto-validator. This is a SEPARATE file for the team's own
    use: spot-checking the ranking and prepping for the Stage-5 interview.
    """
    top = _ranked_top(scored, top_n)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DEBUG_FIELDS)
        writer.writeheader()
        for rank, (candidate, result, score) in enumerate(top, start=1):
            td = result.get("trajectory_debug", {})
            bd = result.get("behavior_debug", {})
            writer.writerow({
                "candidate_id": candidate["candidate_id"],
                "rank": rank,
                "score": f"{score:.4f}",
                "title_class": result.get("title_class"),
                "skill_score": round(result.get("skill_score", 0), 4),
                "exp_score": round(result.get("exp_score", 0), 4),
                "semantic_score": round(result.get("semantic_score", 0), 4),
                "behavior_score": round(result.get("behavior_score", 0), 4),
                "location_score": round(result.get("location_score", 0), 4),
                "trajectory_score": round(result.get("trajectory_score", 0), 4),
                "trajectory_convergence": td.get("convergence"),
                "seniority_non_decreasing": td.get("seniority_non_decreasing"),
                "disqualifier_flags": "|".join(result.get("disqualifier_flags", [])),
                "consistency_flags": "|".join(result.get("consistency_flags", [])),
                "evidence_source_count": result.get("evidence_source_count"),
                "evidence_sources": "|".join(result.get("evidence_sources", [])),
                "shipped_phrase_hits": result.get("shipped_phrase_hits"),
                "days_inactive": bd.get("days_inactive"),
                "response_rate": bd.get("response_rate"),
                "notice_period_days": bd.get("notice_period_days"),
            })
    sys.stderr.write(f"Wrote debug report ({len(top)} rows) to {out_path}\n")


def peak_rss_mb():
    """
    Peak resident set size for THIS process, in MB. stdlib-only (resource
    module) — no psutil needed. Returns None on platforms without `resource`
    (native Windows); the caller handles that gracefully.
    NOTE: ru_maxrss units differ by OS — macOS reports bytes, Linux reports
    kilobytes. This script's own process measurement is informational only;
    the authoritative number for your submission is whatever `/usr/bin/time -l`
    (macOS) or `/usr/bin/time -v` (Linux) reports for the whole process from
    OUTSIDE, since that also captures interpreter startup overhead this
    self-measurement misses.
    """
    if not _HAS_RESOURCE:
        return None
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if platform.system() == "Darwin":  # macOS reports bytes
        return raw / (1024 * 1024)
    return raw / 1024  # Linux reports KB


def main():
    parser = argparse.ArgumentParser(description="Rank candidates for the Redrob hackathon JD.")
    parser.add_argument("--candidates", required=True, type=Path, help="Path to candidates.jsonl[.gz] or sample_candidates.json")
    parser.add_argument("--jd", required=True, type=Path, help="Path to job_description.md/.txt")
    parser.add_argument("--out", required=True, type=Path, help="Output CSV path (official submission format)")
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument(
        "--debug-out", type=Path, default=None,
        help="Optional: write a wider per-candidate breakdown to this path for "
             "your own spot-checking. NOT part of the official submission.",
    )
    args = parser.parse_args()

    start = time.time()
    scored = rank_candidates(args.candidates, args.jd)
    write_submission_csv(scored, args.out, top_n=args.top_n)
    if args.debug_out:
        write_debug_csv(scored, args.debug_out, top_n=args.top_n)
    elapsed = time.time() - start
    sys.stderr.write(f"Done in {elapsed:.1f}s\n")

    rss = peak_rss_mb()
    if rss is not None:
        sys.stderr.write(f"Peak RSS (self-reported, {platform.system()}): {rss:.1f} MB\n")
        sys.stderr.write(
            "Note: for an authoritative figure, wrap this command with "
            "'/usr/bin/time -l' (macOS) or '/usr/bin/time -v' (Linux) instead.\n"
        )
    else:
        sys.stderr.write(
            f"Peak RSS self-reporting unavailable on {platform.system()} "
            "(no `resource` module — expected on native Windows). "
            "Use an external memory profiler or Task Manager if you need this figure here.\n"
        )


if __name__ == "__main__":
    main()
