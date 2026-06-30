# Redrob Hackathon — Candidate Ranker

Ranks the 100K-candidate pool against the "Senior AI Engineer — Founding Team"
job description and produces the top-100 submission CSV.

## Quick start

```bash
python rank.py --candidates ./candidates.jsonl.gz --jd ./job_description.md --out ./submission.csv
python validate_submission.py submission.csv
```

(`candidates.jsonl`, uncompressed, also works — the loader sniffs the
extension. `sample_candidates.json`, the pretty-printed JSON array from the
hackathon bundle, also works for quick local testing.)

Optional: add `--debug-out ./debug_report.csv` to also write a wider
per-candidate breakdown (every component score, all flags, evidence sources)
for your own spot-checking. This is **not** part of the official submission —
`submission_spec.md` fixes the submission CSV to exactly
`candidate_id,rank,score,reasoning`, so the debug report is always written to
a separate file.

No GPU, no network calls, no external packages — pure Python standard
library. Verified against the real, full 100,000-candidate `candidates.jsonl`:
**61.0 seconds wall-clock, ~1.84 GB peak RAM**, well inside the 5-minute /
16 GB / CPU-only budget. (An earlier local run on the original author's
MacBook Air completed the same workload in 14.8s — timing varies by machine,
both are comfortably inside budget.)

For the full file-by-file / function-by-function reference, see
`ARCHITECTURE.md`. For what changed in this revision and why, see
`CHANGES.md`. For what's deliberately left undone and why, see
`NEXT_STEPS.md`.

## Why this approach (not embeddings-first)

The JD's own "note for hackathon participants" states the trap directly: the
dataset contains keyword-stuffed profiles (AI skills listed at "expert" level
with near-zero actual duration/assessment backing) designed to score highly
under naive semantic/keyword similarity. We verified this in the released
50-candidate sample — `CAND_0000001` lists "advanced" NLP/GANs/LoRA/Fine-tuning
skills but their own Redrob skill-assessment scores for those exact skills are
38–65/100, and their summary openly says *"I'm building competence on the ML
side."* An embedding-similarity-first ranker would likely rank this candidate
highly; ours doesn't.

So the architecture is **feature/rule-first, semantic-similarity-minor**:

| Component | Weight | What it checks |
|---|---|---|
| `title_career_match` | 0.30 | Title classification + "shipped a real system" language in career history — the JD's actual core ask |
| `skill_credibility` | 0.20 | Skill claims weighted by duration, endorsements, Redrob skill-assessment scores, AND now cross-domain depth (retrieval + vector-db + eval together, not one keyword) — not raw keyword presence |
| `behavioral_availability` | 0.13 | Recency of activity, recruiter response rate, interview completion, notice-period fit — the JD explicitly asks to down-weight unavailable candidates and has an explicit notice-period preference curve |
| `experience_fit` | 0.12 | Soft band around the JD's 5-9 yr range, with the JD's own stated exceptions |
| `career_trajectory` | 0.07 | Domain convergence over time + earned seniority progression — independent of (and complementary to) the title_chaser disqualifier below |
| `semantic_similarity` | 0.05 | JD-text vs. profile-text token overlap — kept deliberately minor since this is the channel keyword-stuffers exploit |
| `location_fit` | 0.03 | Pune/Noida preferred, Tier-1 India welcome, no-visa-sponsorship penalty outside India |
| `consistency_penalty` | 0.10 | Internal-inconsistency / honeypot-style checks (see below) |

A separate **disqualifier layer** applies multiplicative penalties for the
JD's explicit "things we explicitly do NOT want" list: pure-research-only,
recent-LangChain-only, tech-lead-no-code-18mo, title-chaser (escalating titles
via short hops — see note below), consulting-only-no-product, and
excluded-domain-without-NLP.

### Honeypot / consistency checks

The spec's example honeypot ("8 years at a company founded 3 years ago")
needs a `company_founded_year` field that **does not exist** in
`candidate_schema.json` — so that exact check isn't computable from the
released schema. We check the inconsistencies that *are* computable:

- Years-of-experience vs. years-since-graduation mismatch
- "Expert" proficiency claimed with ≤3 months duration
- Claimed advanced/expert proficiency contradicted by a low Redrob
  skill-assessment score for that same skill
- Total `career_history` duration vs. stated `years_of_experience`

We deliberately did **not** use duplicated career-history description text as
a signal — spot-checking the sample data showed templated descriptions are a
generic synthetic-data artifact shared across most candidates, not a
honeypot-specific signal, so using it would have penalized normal profiles.

### Known false-positive we found and fixed

Our first pass flagged `CAND_0000031` (the strongest fit in the sample) as a
`title_chaser` because of average job tenure ≤18 months across 4 roles. On
inspection, this candidate has steady IC-level progression through real
product companies (Zomato → Uber → Mad Street Den → Swiggy) — no
title-escalation pattern. The JD's actual complaint is about
*Senior → Staff → Principal* hopping, not tenure length alone, so
`title_chaser` now requires **both** short average tenure **and** a detected
seniority-escalation pattern across the title strings. See
`test_features.py::test_title_chaser_does_not_flag_steady_ic_progression`.

## Files

```
rank.py                 — CLI entrypoint; orchestration only
features.py             — feature extraction (title classification, skill
                           credibility + domain-depth, disqualifier detection,
                           consistency checks, behavioral scoring incl. notice
                           period, career trajectory, location fit, evidence
                           coverage, shipped-evidence quoting)
scorer.py                — combines features into final composite + builds
                           the reasoning string from the SAME features used
                           to score (no separate hallucination-prone step;
                           quotes the candidate's own career_history text)
jd_requirements.py       — the JD's rubric as structured, editable data
                           (now includes a 2-level skill taxonomy)
test_features.py         — unit tests, incl. the two cases the whole task
                           hinges on (strong-fit vs keyword-stuffer), the
                           title_chaser regression test above, and a direct
                           regression test for templated reasoning
validate_submission.py   — official format validator (from hackathon bundle)
data/job_description.md  — JD, extracted to plain text
data/sample_candidates.json — 50-candidate sample from the hackathon bundle
ARCHITECTURE.md          — file-by-file, function-by-function reference
CHANGES.md               — what changed in the latest revision and why
NEXT_STEPS.md            — what's left, ordered by expected impact
```

### A note on reasoning quality

An earlier version of `build_reasoning()` used one fixed sentence for any
candidate classified `strong_ml` with 2+ "shipped" phrase hits. Checked
directly against the real, full 100K-candidate submission output, this
produced 94/100 rows sharing that sentence verbatim — exactly the
"all-identical reasoning" failure mode `submission_spec.md` Section 3
explicitly penalizes at Stage 4. The current version
(`features.shipped_evidence_snippet`) instead quotes the actual matching
sentence fragment from each candidate's own `career_history` description.
Verified on the same real output: 100/100 unique reasoning strings, 0
containing the old canned clause. See `CHANGES.md` for the full before/after.

## Running tests

```bash
python test_features.py
# or: python -m pytest test_features.py -v
```

20 tests, including a direct regression test asserting the old templated
reasoning clause never reappears (`test_reasoning_is_not_templated_across_distinct_candidates`).

## Pre-computation

None required for the current approach — there is no embedding model to
download or index to build. If a future iteration adds an offline embedding
model (e.g. a locally-cached sentence-transformer), it would be loaded from
disk during the 5-minute ranking window with zero network calls; per the
spec, generating/caching those embeddings ahead of time is treated as
pre-computation and is allowed to exceed 5 minutes separately.

## Compute environment tested

See `submission_metadata.yaml` for the declared environment. Stress-tested
locally by replicating the 50-sample candidates to a synthetic 100K-row file
matching the real pool's approximate size (~465 MB uncompressed JSONL) to
confirm runtime/memory headroom ahead of receiving the real
`candidates.jsonl.gz`.
