# Architecture Reference

This is the file-by-file, function-by-function reference for the ranker.
Read `README.md` first for the quick-start and the high-level "why this
approach" pitch. This doc exists so you (or a Stage-5 interviewer) can find
exactly where any number comes from in under a minute.

For what changed in this version and why, see `CHANGES.md`. For what's
deliberately left undone, see `NEXT_STEPS.md`.

---

## File map

```
rank.py                  CLI entrypoint — orchestration only, no scoring logic
features.py               feature extraction — one function per signal
scorer.py                 combines features into final score + reasoning text
jd_requirements.py         the JD's rubric as structured, editable data (no logic)
test_features.py           unit + regression tests
validate_submission.py     official format validator (from hackathon bundle, unmodified)
candidate_schema.json      official candidate schema (from hackathon bundle, unmodified)
submission_metadata.yaml   filled-in portal metadata
data/job_description.md    JD, extracted to plain text
data/sample_candidates.json 50-candidate sample from the hackathon bundle
```

Design rule the whole codebase follows: **rank.py never computes a score
directly.** Every number in the output CSV traces back through `scorer.py` to
a named function in `features.py`, and every threshold/weight that function
uses lives in `jd_requirements.py` with a comment pointing at the sentence in
the JD it came from. If you can't find where a number comes from, that's a
bug in this rule, not a property of the system.

---

## `jd_requirements.py` — the rubric, not the code

Pure data, no functions. This file is what changes if the JD changes; nothing
in `features.py` or `scorer.py` should need to change if you only update
numbers here.

| Constant | What it encodes | JD source |
|---|---|---|
| `EXPERIENCE_BAND`, `EXPERIENCE_SOFT_MARGIN` | 5-9yr band, soft falloff outside it | "Experience Required: 5-9 years... This is a range, not a requirement." |
| `SKILL_TAXONOMY` | Two-level skill hierarchy: domain → leaf-family → matching substrings | "Things you absolutely need" |
| `CORE_SKILL_FAMILIES` | Flattened view of `SKILL_TAXONOMY` (same data, single-level) — kept so every consumer that scores one family at a time doesn't need to know about the nesting | same |
| `NICE_TO_HAVE_SKILLS` | Small bonus only, never a requirement | "Things we'd like you to have but won't reject you for" |
| `STRONG_TITLE_KEYWORDS` / `ADJACENT_TITLE_KEYWORDS` / `EXCLUDED_DOMAIN_TITLE_KEYWORDS` / `OFF_DOMAIN_TITLE_KEYWORDS` | Title → trust-level classification | "applied ML/AI roles at product companies" vs CV/speech/robotics vs generic eng titles |
| `MANAGEMENT_TRACK_TITLE_KEYWORDS` | Title-based fallback trigger for the tech-lead-no-code disqualifier | "hasn't written production code in the last 18 months because you've moved into architecture or tech lead roles" |
| `DISQUALIFIER_PENALTIES`, `CONSULTING_FIRMS` | Multiplicative penalties for the JD's explicit "do NOT want" list | "Things we explicitly do NOT want" |
| `PREFERRED_LOCATIONS`, `TIER1_INDIA_LOCATIONS` | Location scoring tiers | "Pune/Noida preferred... Tier-1 India cities welcome... no visa sponsorship" |
| `NOTICE_PERIOD_BREAKPOINTS`, `NOTICE_PERIOD_FLOOR`, `NOTICE_PERIOD_UNKNOWN` | Soft preference curve, not a cutoff | "We'd love sub-30-day notice... 30+ day notice candidates are still in scope but the bar gets higher" |
| `WEIGHTS` | Final composite weights, sums to 1.0 (enforced by `test_weights_sum_to_one`) | derived from how much text/emphasis each JD section gets |

---

## `features.py` — one function per signal

Every function takes raw candidate fields (or a pre-extracted subset) and
returns either a 0-1 score or a list of string flags. No function here
calls another scoring component's output — `scorer.py` is the only place
features get combined, so each function in this file is independently
testable and independently explainable.

### Title / domain classification
- **`classify_title(title)`** → `strong_ml | adjacent_eng | excluded_domain | off_domain`. The single most important classification in the system — almost everything downstream conditions on this.
- **`_seniority_rank(title)`**, **`SENIORITY_LADDER`** — shared seniority-ordering used by both the `title_chaser` disqualifier and the new `career_trajectory_score`.

### Skill credibility
- **`skill_credibility(skill, assessment_scores)`** — 0-1 credibility for *one* claimed skill, blending proficiency label, duration, endorsements, and (if present) the candidate's own Redrob assessment score for that exact skill. This is the core anti-keyword-stuffing mechanism: a high proficiency label with near-zero duration and no assessment backing scores low almost by construction.
- **`core_skill_family_coverage(skills, assessment_scores)`** — per required family (embeddings/retrieval, vector-db, eval, python), the best credibility found among matching skills.
- **`skill_domain_depth_bonus(skills, assessment_scores)`** *(new)* — rewards a candidate who is credible across **multiple** subdomains within `search_engineering` (retrieval + vector-db + eval), not just one. Single-leaf domains (currently `core_engineering`, just python) are skipped — there's no "breadth" to reward within one leaf. This is the hierarchical-taxonomy feature from the review: harder for a keyword-stuffer to fake three corroborated subdomains than one substring match.
- **`nice_to_have_bonus(skills)`** — small bonus, saturates after ~4 distinct hits, per the JD's explicit "won't reject you for" framing.

### Career history / title match (the JD's actual core ask)
- **`career_history_text(candidate)`** — concatenates all career_history titles/descriptions + profile summary/headline into one text blob for phrase matching.
- **`title_career_match_score(candidate)`** — combines title classification with presence of "shipped a real system" language (`SHIPPED_SYSTEM_PHRASES`). Weighted 0.30, the single highest weight in the system, because the JD's hackathon note explicitly frames this as the actual test: *"if their career history shows they built a recommendation system at a product company, they're a fit."*
- **`shipped_evidence_snippet(candidate)`** *(new)* — finds the actual sentence fragment in the candidate's own `career_history[].description` that matched a shipped-system phrase, and returns it with the role/company it came from. **This is the direct fix for the templated-reasoning problem** (see `CHANGES.md`): `build_reasoning()` now quotes this instead of a fixed sentence.

### Career trajectory *(new)*
- **`career_trajectory_score(candidate)`** — two things the base title-match score doesn't capture: (1) is the career *trending toward* the JD's domain over time (recency-weighted), and (2) is seniority non-decreasing across real tenures. Deliberately kept separate from `title_chaser`: that disqualifier only fires on *short*-tenure title escalation; this rewards a long, earned climb (e.g. the sample data's CAND_0000031: Zomato → Uber → Mad Street Den → Swiggy) which should score *well*, not be penalized twice or ignored.

### Disqualifiers
- **`detect_disqualifiers(candidate, title_class)`** — returns the list of flags from `DISQUALIFIER_PENALTIES`. Each flag traces to one bullet in the JD's "Things we explicitly do NOT want" section. The `tech_lead_no_code_18mo` check now has two independent triggers: the original phrase-match against free text, and a new title-based fallback (`MANAGEMENT_TRACK_TITLE_KEYWORDS` + ≥18mo in that role) for candidates whose current title is plainly management-track even if their text never uses one of the literal phrases.

### Experience / notice period
- **`experience_fit_score(yoe)`** — soft band around 5-9 years.
- **`notice_period_fit(days)`** *(new)* — soft curve (sub-30d full credit, 30-60d 0.7, 60-90d 0.5, 90+d floor at 0.3, unknown treated neutrally at 0.6), per the JD's explicit "bar gets higher, not a cliff" framing.

### Behavioral availability
- **`behavioral_score(signals)`** — recency of activity, open-to-work flag, recruiter response rate, interview completion rate, and (new) notice-period fit, weighted per the JD's explicit instruction to down-weight unavailable candidates.

### Location
- **`location_fit_score(profile)`** — Pune/Noida full credit, other Tier-1 India cities partial, outside India low (no visa sponsorship, case-by-case).

### Consistency / honeypot
- **`consistency_flags(candidate)`** — internal-inconsistency checks computable from the actual schema (YOE-vs-graduation mismatch, expert-claim-with-near-zero-duration, claimed-proficiency-vs-assessment-score contradiction, career-history-span-vs-YOE mismatch). The spec's literal honeypot example needs a `company_founded_year` field that doesn't exist in `candidate_schema.json`, so this checks what's actually computable instead of pretending to check something it can't.
- **`consistency_penalty(flags)`** — bounded multiplier, floor at 0.3, so accumulating flags doesn't fully zero out an otherwise strong profile.

### Evidence coverage *(new, diagnostic only)*
- **`evidence_coverage(candidate, family_scores, shipped_hits)`** — counts how many *independent* profile sections (skills, career_history, Redrob assessment, GitHub activity, summary text) corroborate the candidate's claimed fit. **Does not feed `final_score`** — most of this signal is already captured by `skill_credibility` (uses assessment scores) and `title_career_match` (uses career_history text), so adding it as a weighted component would double-count. It exists purely for the debug report and for one possible "evidence is thin" honest-concern line in reasoning.

### Semantic similarity (minor, by design)
- **`_tokenize`, `jaccard_similarity`** — plain token-overlap, no embeddings. Kept deliberately at low weight (0.05) since this is exactly the channel keyword-stuffers exploit — the JD's own hackathon note calls this trap out directly.

---

## `scorer.py` — combination + reasoning

- **`score_candidate(candidate, jd_tokens)`** — calls every feature function above, combines them via `jd_requirements.WEIGHTS` into `final_score`, applies the disqualifier multiplier and the consistency-penalty deduction, and returns a result dict carrying every component score plus all the debug fields needed downstream. Nothing in here is opaque: every number in the return dict has a name that maps to a `features.py` function.
- **`build_reasoning(candidate, result)`** — builds the 1-2 sentence reasoning string for the submission CSV using *only* values already in `result`/`candidate`. The strong_ml branch now prefers `result["shipped_snippet"]` (a real quote from this candidate's own career history) over a fixed sentence — this is the reasoning-variation fix. Also surfaces trajectory-vs-title misalignment, thin-evidence warnings, and notice-period concerns when they're informative, so reasoning length and content adapt to what's actually true about each candidate rather than always filling the same four slots.

---

## `rank.py` — orchestration only

- **`load_candidates` / `load_candidates_from_json_array` / `detect_input_format`** — streaming loaders for `.jsonl`, `.jsonl.gz`, and the pretty-printed `sample_candidates.json` array format.
- **`build_jd_tokens(jd_text)`** — tokenizes the JD body for the semantic-similarity signal, stripping the "Final note for the participants" meta-section so words like "hackathon" and "dataset" don't pollute the overlap.
- **`rank_candidates(...)`** — scores every candidate, never lets one malformed record kill the run (catches and warns instead).
- **`_ranked_top(scored, top_n)`** *(new, shared)* — the single sort-then-truncate function used by both CSV writers, so the official submission and the debug report are always in identical order. Sorts on the *rounded* score (not the raw float) before tie-breaking on `candidate_id` — see `test_csv_sort_matches_rounded_score_not_raw_float` for why this matters.
- **`write_submission_csv(...)`** — writes the official 4-column CSV exactly matching `submission_spec.md` Section 2.
- **`write_debug_csv(...)`** *(new, opt-in via `--debug-out`)* — writes a wider per-candidate breakdown (component scores, all flags, evidence sources, trajectory debug) to a **separate file**. Explicitly not merged into the submission CSV, because Section 2 fixes that file's header to exactly `candidate_id,rank,score,reasoning` and extra columns would fail the auto-validator.
- **`peak_rss_mb()`** — now returns `None` gracefully on platforms without the `resource` module (native Windows) instead of crashing.

---

## How a number flows end to end (worked example)

Say you want to know why `CAND_0042871` is ranked where it is:

1. Run `python rank.py --candidates ... --debug-out ./debug_report.csv` — find the row for that ID.
2. `debug_report.csv` gives you every component score (`skill_score`, `trajectory_score`, etc.) plus all flags.
3. Each component score traces to exactly one function in `features.py` (see the table above).
4. Each threshold/weight that function used traces to a named constant in `jd_requirements.py`, with a comment citing the JD sentence.
5. The `reasoning` column in the actual submission CSV is built from the same `result` dict — it's not a separate, potentially-inconsistent explanation, it's a rendering of the same numbers.

This is also the answer to "how do I prep for the Stage 5 interview": walk the interviewer through this same chain for any candidate they pick.
