# Redrob Intelligent Candidate Discovery

## Project Overview

This repository generates a ranked CSV of the best 100 candidates from a massive pool of 100,000 candidates. Built specifically for the Redrob Intelligent Candidate Discovery Hackathon, it evaluates engineering profiles against a core Applied ML / Search matching rubric.

This is a strictly offline, high-performance, deterministic ranking engine that processes all candidates and exports the results along with organic, dynamically-generated 1-2 sentence evidence-based reasoning strings.

## Architecture

The system uses a single-pass feature extraction and scoring pipeline to process candidates offline without relying on external network calls or non-deterministic APIs.

1. **Raw Candidates (100k)** -> Loaded into memory.
2. **Feature Extraction (`features.py`)** -> Extracts exact text matches, phrase hits, and behavioral flags.
3. **Scoring & Rules (`scorer.py`)** -> Evaluates eligibility, assigns titles to classes, and scores depth of skills.
4. **Reasoning Generation** -> Constructs evidence-grounded justification strings using a composable template engine.
5. **Sort & Slice** -> Filters invalid profiles, sorts by score descending, and slices the top 100.
6. **Output** -> Writes `submission.csv`.

## Feature Engineering

The system evaluates candidates using a heavily engineered, multi-layered rubric rather than naive keyword matching:

- **Title Match & Class (Primary Gate):** Determines base eligibility (`strong_ml`, `adjacent_eng`, `excluded_domain`, `off_domain`).
- **Shipped System Evidence:** Looks for phrases indicating actual production work (e.g., "deployed", "scaled") combined with technical domains, rather than just raw tags.
- **Family Scores (Skill Depth):** Measures experience depth across specific tech families (e.g., Vector DBs, Hybrid Search, ML Infrastructure).
- **Trajectory & Seniority:** Analyzes progression to ensure seniority is non-decreasing and converging toward applied ML.
- **Behavioral Signals:** Penalizes extended platform inactivity or poor recruiter response rates.

## Ranking Pipeline

- **Sorting Mechanism:** Candidates are sorted primarily by their aggregate `final_score`.
- **Tie-Breaking:** If scores match exactly, a secondary tie-breaker uses internal feature depth (e.g., shipped systems, convergence) to ensure a stable sort.

## Honeypot Detection

The competition explicitly includes honeypot candidates. We catch them via `consistency_flags` and `disqualifier_flags`:
- **Seniority Inversions:** Flagging profiles that show erratic jumps (e.g., VP of Engineering stepping down to Junior).
- **Timeline Contradictions:** Detecting impossibly dense parallel roles or years of experience that exceed biological possibility.
- **Domain Mismatch:** Catching profiles claiming heavy ML experience but whose titles firmly root them in an explicitly excluded domain.

## Keyword Stuffing Detection

Vector embeddings (e.g., cosine similarity) are easily fooled by keyword stuffers who copy/paste the JD into their profiles. Our feature engine defends against this:
- We explicitly look for *corroborated* evidence: mentions of the skill alongside verbs that indicate production deployment (e.g., "scaled PyTorch training pipeline").
- We calculate an `evidence_source_count` to ensure claims are verified across multiple roles/projects rather than concentrated in a single summary section.

## Deterministic Scoring

The system is 100% deterministic. Even the natural language reasoning generation (which features thousands of possible sentence combinations) uses a stable `zlib.crc32` hash derived from `candidate_id`, `title_class`, and `score_bucket` to seed the template selection.

This guarantees:
1. Identical inputs *always* produce byte-for-byte identical output files.
2. Hallucinations are structurally impossible because templates interpolate variables directly from verified profile data.

## Runtime

**~21 seconds** to process and rank all 100,000 candidates.
Complexity is `O(N)` where N is the number of candidates.

## Memory Usage

**~1.9 GB** peak memory.
Memory footprint is driven by loading the entire JSON-lines file into an internal array. This is safely under the 16 GB competition limit.

## How to Run

```bash
python rank.py \
--candidates candidates.jsonl \
--jd job_description.md \
--out submission.csv
```

## Validation

Verify that your `submission.csv` is fully compliant with the Hackathon's structural specifications:

```bash
python validate_submission.py submission.csv
```

## Testing

Run the automated test suite to verify internal assertions and logic (requires `pytest`):

```bash
pytest
```

## Repository Structure

```
README.md                     # You are here
LICENSE                       # MIT License
requirements.txt              # Environment dependencies
Dockerfile                    # Reproducible environment container

ARCHITECTURE.md               # Additional architectural design notes

rank.py                       # Main orchestrator script
scorer.py                     # Rules and reasoning template generator
features.py                   # Keyword extraction and feature engineering
jd_requirements.py            # Static taxonomy for the target role

candidate_schema.json         # Reference schema for candidate inputs
job_description.md            # The specific job description being matched

validate_submission.py        # Validates structural integrity of submission.csv
test_features.py              # Tests feature extraction logic
test_reasoning.py             # Verifies reasoning determinism and diversity
run_audit.py                  # End-to-end performance and diversity audit

submission_metadata.yaml      # Final metadata for submission
.gitignore                    # Local excludes
```
