# Ranking Methodology

This document outlines the architectural decisions, algorithms, and defensive strategies used in the Redrob Intelligent Candidate Discovery ranking system.

## 1. Pipeline Architecture

The system uses a strictly deterministic, single-pass evaluation pipeline to process candidates offline without relying on external network calls.

```mermaid
graph TD
    A[Raw Candidates (100k)] --> B[Feature Extraction (features.py)]
    B --> C[Scoring & Rules (scorer.py)]
    C --> D[Reasoning Generation]
    D --> E[Sort & Top 100 Slice]
    E --> F[submission.csv]
```

## 2. Feature List & Weighting Strategy

The system evaluates candidates using a heavily engineered, multi-layered rubric rather than naive keyword matching. Key features include:

- **Title Match & Class (Primary Gate):** Determines base eligibility. Candidates are bucketed into `strong_ml`, `adjacent_eng`, `excluded_domain`, and `off_domain`.
- **Shipped System Evidence:** Looks for phrases indicating actual production work (e.g., "deployed", "scaled", "shipped") combined with technical domains, rather than just raw skill tags.
- **Family Scores (Skill Depth):** Measures the depth of experience across specific tech families (e.g., Vector DBs, Hybrid Search, ML Infrastructure).
- **Trajectory & Seniority:** Analyzes career progression to ensure seniority is non-decreasing and the candidate's career is converging towards applied ML, not diverging.
- **Behavioral Signals:** Penalizes extended platform inactivity or poor recruiter response rates.

## 3. Why No Embeddings or Hosted LLMs?

While modern pipelines default to LLMs and semantic search, they were intentionally excluded here for the following reasons:
1. **Keyword Stuffing Vulnerability:** Vector embeddings (e.g., cosine similarity) are easily fooled by keyword stuffers who copy/paste the JD into their profiles. Feature engineering is far more robust against this.
2. **Speed & Offline Constraint:** The competition requires scoring 100,000 candidates offline in under 5 minutes. An LLM (even quantized local models) or heavy embedding passes would massively violate this runtime constraint.
3. **Determinism:** LLMs introduce randomness. This system guarantees that the same input will *always* produce the exact same byte-for-byte output and reasoning.

## 4. Defending Against Keyword Stuffers

We combat keyword stuffing by evaluating **context** rather than frequency. 
- A candidate with 50 mentions of "PyTorch" is not automatically ranked highly.
- The scorer explicitly looks for *corroborated* evidence: mentions of the skill alongside verbs that indicate production deployment (e.g., "scaled PyTorch training pipeline").
- We calculate an `evidence_source_count` to ensure claims are verified across multiple roles/projects rather than concentrated in a single summary section.

## 5. Detecting Inconsistent / Honeypot Profiles

The competition explicitly includes honeypot candidates. We catch them via `consistency_flags` and `disqualifier_flags`:
- **Seniority Inversions:** Flagging profiles that show erratic jumps (e.g., VP of Engineering stepping down to Junior Data Scientist).
- **Timeline Contradictions:** Detecting impossibly dense parallel roles or years of experience that exceed biological possibility.
- **Domain Mismatch:** Catching profiles that claim heavy ML experience but whose titles firmly root them in an explicitly excluded domain (like Hardware or traditional IT).

## 6. Determinism & Reasoning Generation

The system is 100% deterministic. Even the natural language reasoning generation (which features ~250,000 possible sentence combinations) uses a stable `zlib.crc32` hash derived from `candidate_id`, `title_class`, and `score_bucket` to seed the template selection. 

This ensures:
1. No two runs ever drift.
2. Reviewers see organic, non-robotic sentences that are strictly grounded in candidate facts.
3. Hallucinations are structurally impossible because the templates can only interpolate variables pulled directly from the candidate's verified profile data.

## 7. Computational Complexity

- **Runtime:** `O(N)` where N is the number of candidates. The feature extraction operates in a single, independent pass per candidate. Total execution time for 100,000 candidates is ~21 seconds.
- **Environment:** CPU-only.
- **Network:** 100% Offline.
- **Peak Memory:** ~1.9 GB (Driven by loading the 100k JSON objects entirely into memory).

## 8. Limitations & Future Improvements

- **Memory Optimization:** The current pipeline loads the entire 100,000 JSON array into memory before ranking. Switching to a streaming `JSON Lines` reader paired with a bounded `heapq` (size 100) would reduce the memory footprint from ~1.9 GB to under 50 MB.
- **Expanded Taxonomy:** The tech family dictionaries are currently hardcoded for the specific Hackathon JD. In a general-purpose ATS, these would need to be loaded dynamically from a knowledge graph.
