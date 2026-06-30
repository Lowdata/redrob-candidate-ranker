# Next Steps

What this codebase does well right now, what's a known limitation, and what's
worth spending remaining time on — roughly ordered by expected impact per
hour of work. This is the honest "what's left" doc, written for your own
planning and for the Stage-5 interview question "what would you do with more
time."

## Where things stand

The ranker is feature/rule-based by design (not embedding-first — see
`README.md`), runs in 61s / ~1.8GB RAM against the real 100K pool (budget:
300s / 16GB), passes the official validator, and now produces 100% unique,
evidence-quoting reasoning on the real output (was 6% unique before this
revision — see `CHANGES.md`). 20/20 unit tests pass, including direct
regression tests for the two bugs found by inspection (templated reasoning,
the title_chaser false positive on steady IC progression documented in the
original README).

## High-value, not yet done

**Most-recent-role preference in `shipped_evidence_snippet`.** Right now it
returns the *first* career_history entry that matches a shipped-system
phrase, which is sometimes an older role, not the current one. Spot-checking
the real output shows this occasionally produces reasoning like *"NLP
Engineer at Haptik (current); career history backs this up — AI Engineer at
Meta: ..."* — factually correct (it's quoting their real past job) but
slightly confusing to read at a glance, since the quoted company differs from
the current one named in the first clause. Low effort fix: prefer the
*current* role's description if it matches, otherwise fall back to most
recent, otherwise first match. ~30 min.

**Cross-validation / corroboration as a scored bonus, not just diagnostic.**
`evidence_coverage()` currently only feeds the debug report and an
occasional reasoning line. A version of this idea that *does* feed
`final_score` — e.g. a small multiplicative bonus when a skill is
independently corroborated by both `career_history` text and a Redrob
assessment score — is plausible and was flagged as valuable by every review.
It was held back here specifically to avoid double-counting (most of this
signal already lives inside `skill_credibility`), but a more careful version
scoped to cases `skill_credibility` doesn't already cover (e.g. corroboration
between `skills` and `career_history` specifically, independent of
assessment scores) could be worth 0.5-1 day of careful work plus new tests to
make sure it doesn't just re-reward the same evidence twice.

**Pattern analysis on the real top 500, not just top 100.** This was the one
piece of advice that showed up at the end of every review and wasn't acted
on yet: rerun the ranker with `--top-n 500` against `candidates.jsonl`, pull
the resulting `debug_report.csv`, and manually skim for recurring false
positives (does anything in 100-500 look like an obvious keyword-stuffer that
slipped through the credibility weighting?) and false negatives (does
anything ranked 200+ look like a strong, plainly-stated fit that's being
under-scored because of phrasing quirks not covered by `SHIPPED_SYSTEM_PHRASES`
or `CORE_SKILL_FAMILIES`?). This is the highest-leverage remaining activity
precisely because it's the only one grounded in this specific dataset rather
than general principles — same argument made in the third review's closing
section, and it remains the strongest path to closing the gap with whatever
hidden ground-truth labels exist. ~2-3 hours, no code changes required first;
code changes only if a real pattern turns up.

## Medium value

**Expand `SHIPPED_SYSTEM_PHRASES` / `CORE_SKILL_FAMILIES` substring lists
based on what the top-500 pass above finds.** These lists were written by
reading the JD and the 50-candidate sample; the real 100K pool almost
certainly contains phrasings ("served," "rolled out," "drove adoption of")
that mean the same thing but won't match today. This is naturally downstream
of the pattern-analysis step above — don't guess at new phrases without
evidence they're missing real matches.

**Honeypot self-check.** `submission_spec.md` Section 7 says honeypot rate
>10% in the top 100 causes Stage-3 disqualification regardless of composite
score, and `submission_metadata_template.yaml` has an explicit (optional)
`honeypot_check_done` declaration field. `consistency_flags()` already
catches the computable inconsistency patterns (YOE-vs-graduation,
expert-claim-with-near-zero-duration, assessment-score contradiction,
career-span mismatch), but there's no script that explicitly counts how many
of the current top-100 carry 2+ such flags as a proxy honeypot-rate estimate
before submitting. ~1 hour to write a small standalone check using the
existing `debug_report.csv` output (the `consistency_flags` column is already
there) — count rows with ≥2 flags as the proxy.

## Low value / explicitly deprioritized (see `CHANGES.md` for full reasoning)

- Company-prestige or Big-Tech-name-based scoring — not supportable from JD text, several reviews flagged the bias risk.
- Splitting `jd_requirements.py` into many small YAML files — restructuring without rubric improvement.
- Any embedding model, even for retrieval-only / recall-only use ahead of the rule engine — adds a dependency, a precompute step, and surface area for the Stage-3 reproduction check, for a benefit (catching paraphrased-but-relevant matches) that `SHIPPED_SYSTEM_PHRASES` already partially covers and that the pattern-analysis step above can quantify before deciding it's worth the cost.
- Further runtime optimization — already at ~20% of the time budget.

## If you only have one more hour

Run the top-500 pattern-analysis pass described above. Every other item on
this list either depends on what that pass finds, or is lower-impact than
spending that hour looking at real candidates instead of guessing.
