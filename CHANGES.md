# Changes — v1 → v2

This revision was driven by three AI-generated reviews of the v1 codebase
(pasted into chat) plus one measured finding against the real 100K-candidate
output. Every change below is tied to a specific, checkable reason — not a
vibe. Items the reviews suggested that were **not** implemented are listed
too, with the reasoning for skipping them, so the "why" is on record for the
Stage-5 interview.

## What was actually broken (found by measurement, not review)

**Templated reasoning.** Before touching anything the reviews suggested, the
real `submission.csv` (100K-pool run) was checked directly:

- 94/100 rows shared one identical clause verbatim: *"career history shows
  shipped ranking/retrieval work, matching the JD's core ask."*
- Only 6 unique reasoning strings existed across the top 100.

`submission_spec.md` Section 3 explicitly penalizes this: *"All-identical
reasoning strings"* and *"Templated reasoning that just inserts the
candidate's name"* are both listed under "What's penalized," and "Variation"
is one of the six Stage-4 manual-review checks. This was the single highest-
priority fix, and it's the one item where the urgency wasn't a guess.

**Fix:** `features.shipped_evidence_snippet()` (new) finds the actual
sentence fragment in the candidate's own `career_history[].description` that
matched a shipped-system phrase and returns it with the role/company it came
from. `scorer.build_reasoning()` now quotes this instead of the fixed
sentence.

**Verified on the real 100K pool after the fix:** 100/100 unique reasoning
strings, 0/100 contain the old canned clause, 89/100 unique "shipped work"
quotes (the remaining duplication is the synthetic dataset itself reusing
description text across candidates — a known data artifact already called
out in the original README, not a code bug).

## Changes made (all reviews agreed on these, in some form)

| Change | File(s) | Why |
|---|---|---|
| Reasoning quotes real career_history text instead of a fixed sentence | `features.py`, `scorer.py` | Fixes the measured bug above; directly targets Stage-4's "Variation" and "no hallucination" checks |
| Hierarchical skill taxonomy (`SKILL_TAXONOMY`) + domain-depth bonus | `jd_requirements.py`, `features.py` | Rewards corroborated breadth across retrieval+vector-db+eval, not one matched substring — harder for a keyword-stuffer to fake. Flattened view (`CORE_SKILL_FAMILIES`) kept byte-identical so this is additive, not a rubric rewrite |
| Career trajectory score (domain convergence + earned progression) | `features.py`, `jd_requirements.py` (new `career_trajectory` weight, 0.07) | None of v1's scoring rewarded a long, earned climb through the right domain. Deliberately separate from `title_chaser`, which only fires on *short*-tenure escalation — a steady multi-year climb (the sample data's CAND_0000031 pattern) should score well here, not be double-penalized or ignored |
| Notice-period soft-curve scoring | `features.py`, `jd_requirements.py` | JD has an explicit preference curve ("sub-30-day... 30+ still in scope but bar gets higher") that nothing previously used, despite `notice_period_days` being in the schema |
| Broadened `tech_lead_no_code_18mo` to also trigger on current title (management-track keyword + ≥18mo tenure) | `features.py`, `jd_requirements.py` (new `MANAGEMENT_TRACK_TITLE_KEYWORDS`) | The original only fired on literal free-text phrases; a candidate whose current title is plainly "Engineering Manager" with 2+ years there matches the JD's stated disqualifier even without using one of those exact phrases |
| Evidence-coverage diagnostic | `features.py`, `scorer.py` | Counts independent corroborating profile sections per candidate. Kept **diagnostic-only** (not weighted into `final_score`) — most of this signal already exists inside `skill_credibility` and `title_career_match`; adding it as a weighted component would double-count the same evidence twice |
| Cross-platform `resource` import fix | `rank.py` | `resource` is POSIX-only; the unconditional import would crash on native Windows. Now optional with a graceful fallback in `peak_rss_mb()` |
| Opt-in `--debug-out` CSV | `rank.py` | A wider per-candidate breakdown (all component scores, all flags, evidence sources) for the team's own spot-checking and interview prep. Kept as a **separate file**, never merged into the official submission CSV, since `submission_spec.md` Section 2 fixes that file's header to exactly `candidate_id,rank,score,reasoning` |
| 9 new/updated unit tests | `test_features.py` | One per new feature, plus a direct regression test (`test_reasoning_is_not_templated_across_distinct_candidates`) asserting the old canned clause never reappears |

`WEIGHTS` were rebalanced to make room for `career_trajectory` (0.07):
`semantic_similarity` 0.10→0.05 and `location_fit` 0.05→0.03. Both were
already documented as deliberately minor/tie-breaker signals in v1; neither
review nor a re-read of the JD argued for increasing them, so they absorbed
the trim rather than `title_career_match` or `skill_credibility`, which are
the JD's actual core asks. `test_weights_sum_to_one` enforces this stays
correct.

## Suggested but deliberately NOT implemented

| Suggestion | Why it was rejected |
|---|---|
| Big Tech / company-prestige penalty | Doesn't exist in the actual JD. The JD's complaint ("if you've spent your career at Google or Meta and you want a well-scoped role with a defined ladder, this isn't it") is about *expectations and fit*, not employer identity — nothing in `candidate_schema.json` lets you infer expectations from a company name without introducing exactly the prestige bias one review warned against |
| 11-file YAML "knowledge engine" / `ranking_knowledge.md` | Restructuring `jd_requirements.py` into many small YAML files changes file count, not rubric quality. The single-file structure already satisfies the actual goal (reviewable, editable, offline, no-LLM-in-the-loop config) that motivated the suggestion. This was flagged in two of the three reviews as "architecture theater" — agreed |
| Confidence score as a customer-facing second metric (separate from evidence_coverage) | `submission_spec.md` Section 2 fixes the CSV to exactly 4 columns; a "confidence" column would fail the auto-validator. The underlying idea (surface how corroborated a score is) is implemented as `evidence_coverage`, exposed only in the optional debug CSV and occasionally in reasoning text, not as a new scored column |
| Running an LLM during ranking (any form) | `submission_spec.md` Section 3 explicitly forbids hosted LLM API calls during ranking and caps runtime at 5 minutes — not viable at 100K-candidate scale regardless of how it's framed |
| Aggressive runtime optimization | Already 61s on a 100K real pool against a 300s budget (and 14.8s in the original author's local Mac run). Further optimization has near-zero expected payoff against this constraint; time was spent on reasoning quality and feature accuracy instead, per all three reviews' stated priority order |

## Verification performed

- `python test_features.py` — 20/20 passing.
- `python rank.py --candidates ./candidates.jsonl --jd ./data/job_description.md --out ./submission.csv --debug-out ./debug_report.csv` against the **real, full 100,000-row `candidates.jsonl`** (not the 50-row sample) — 61.0s wall-clock, ~1.84GB peak RSS.
- `python validate_submission.py submission.csv` — passes the official format validator.
- Direct measurement of reasoning uniqueness before/after on the real output (see above).
