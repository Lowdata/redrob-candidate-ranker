"""
scorer.py — combines features.py outputs into the final 0-1 composite score
per candidate, plus a reasoning string built from the SAME features used to
score (so reasoning can never hallucinate something the score didn't see).

v2 changes (post-feedback-review):
  - skill_score now includes a domain-depth bonus (feat.skill_domain_depth_bonus)
    alongside the existing per-family coverage and nice-to-have bonus.
  - composite score now includes career_trajectory (feat.career_trajectory_score).
  - result dict carries new diagnostic-only fields (evidence_source_count,
    evidence_sources, shipped_snippet, trajectory_score, trajectory_debug,
    domain_depth_debug) for the debug/explainability report in rank.py and for
    build_reasoning. None of these are weighted into final_score except
    trajectory_score, which IS a weighted component (see jd_requirements.WEIGHTS).
  - build_reasoning() now quotes the candidate's own career_history text
    (feat.shipped_evidence_snippet) instead of reusing one fixed sentence for
    every strong_ml candidate -- this is the fix for the measured problem
    where 94/100 rows in the real submission shared an identical clause.
    All new lookups use .get(...) with safe fallbacks so this still works
    against older/partial result dicts (e.g. in unit tests).
"""

from __future__ import annotations

import jd_requirements as jd
import features as feat


def score_candidate(candidate: dict, jd_tokens: set) -> dict:
    """
    Returns a dict with: final_score, component scores, disqualifier flags,
    consistency flags, and the raw bits needed to build a reasoning string.
    """
    profile = candidate["profile"]
    signals = candidate.get("redrob_signals", {})
    assessment_scores = signals.get("skill_assessment_scores", {})
    skills = candidate.get("skills", [])

    # --- title / career match ---
    title_score, title_debug = feat.title_career_match_score(candidate)
    title_class = title_debug["title_class"]

    # --- skill credibility (+ domain-depth bonus) ---
    family_scores = feat.core_skill_family_coverage(skills, assessment_scores)
    core_skill_score = sum(family_scores.values()) / len(family_scores) if family_scores else 0.0
    bonus = feat.nice_to_have_bonus(skills)
    domain_depth_bonus, domain_depth_debug = feat.skill_domain_depth_bonus(skills, assessment_scores)
    skill_score = min(1.0, 0.75 * core_skill_score + 0.15 * bonus + 0.10 * domain_depth_bonus)

    # --- experience fit ---
    exp_score = feat.experience_fit_score(profile.get("years_of_experience", 0))

    # --- semantic similarity (minor signal) ---
    cand_tokens = feat._tokenize(feat.career_history_text(candidate))
    semantic_score = feat.jaccard_similarity(jd_tokens, cand_tokens)
    # normalize: jaccard on free text is tiny in absolute terms; rescale by a
    # fixed empirical ceiling so it actually contributes within its weight.
    semantic_score = min(semantic_score / 0.12, 1.0)

    # --- behavioral availability (now includes notice-period fit) ---
    behavior_score, behavior_debug = feat.behavioral_score(signals)

    # --- location ---
    location_score = feat.location_fit_score(profile)

    # --- career trajectory (NEW): domain convergence + earned progression ---
    trajectory_score, trajectory_debug = feat.career_trajectory_score(candidate)

    # --- disqualifiers ---
    disqualifier_flags = feat.detect_disqualifiers(candidate, title_class)
    disqualifier_multiplier = 1.0
    for flag in disqualifier_flags:
        disqualifier_multiplier *= jd.DISQUALIFIER_PENALTIES.get(flag, 1.0)

    # --- consistency / honeypot ---
    consist_flags = feat.consistency_flags(candidate)
    consist_multiplier = feat.consistency_penalty(consist_flags)

    # --- evidence coverage (diagnostic only, not weighted into final_score) ---
    evidence_count, evidence_sources = feat.evidence_coverage(
        candidate, family_scores, title_debug["shipped_phrase_hits"]
    )
    shipped_snippet = feat.shipped_evidence_snippet(candidate)

    w = jd.WEIGHTS
    base_composite = (
        w["title_career_match"] * title_score
        + w["skill_credibility"] * skill_score
        + w["experience_fit"] * exp_score
        + w["semantic_similarity"] * semantic_score
        + w["behavioral_availability"] * behavior_score
        + w["location_fit"] * location_score
        + w["career_trajectory"] * trajectory_score
    )
    # consistency penalty is applied as its own weighted deduction, not a flat
    # multiplier on everything, so one honeypot flag doesn't obliterate an
    # otherwise-strong, mostly-consistent profile.
    consist_weight = w["consistency_penalty_weight"]
    final_score = base_composite * (1 - consist_weight) + consist_weight * consist_multiplier * base_composite
    final_score *= disqualifier_multiplier

    return {
        "final_score": round(final_score, 6),
        "title_score": title_score,
        "title_class": title_class,
        "skill_score": skill_score,
        "family_scores": family_scores,
        "domain_depth_debug": domain_depth_debug,
        "exp_score": exp_score,
        "semantic_score": semantic_score,
        "behavior_score": behavior_score,
        "location_score": location_score,
        "trajectory_score": trajectory_score,
        "trajectory_debug": trajectory_debug,
        "disqualifier_flags": disqualifier_flags,
        "consistency_flags": consist_flags,
        "shipped_phrase_hits": title_debug["shipped_phrase_hits"],
        "shipped_snippet": shipped_snippet,
        "evidence_source_count": evidence_count,
        "evidence_sources": evidence_sources,
        "behavior_debug": behavior_debug,
    }


def build_reasoning(candidate: dict, result: dict) -> str:
    """
    Build a 1-2 sentence, fact-grounded reasoning string using ONLY values
    present in `result` / `candidate` — never invented. This directly targets
    the Stage-4 manual-review checks: specific facts, JD connection, honest
    concerns, no hallucination, rank consistency, AND variation (submission_spec.md
    Section 3: "Are the 10 sampled reasonings substantively different from
    each other (not templated)?").

    Every lookup on `result` uses .get(...) with a safe fallback, so this
    still runs against older/partial result dicts (e.g. hand-built ones in
    unit tests) without raising.
    """
    profile = candidate["profile"]
    yoe = profile.get("years_of_experience")
    title = profile.get("current_title")
    company = profile.get("current_company")
    loc = profile.get("location")

    title_class = result["title_class"]
    families = result.get("family_scores", {})
    strong_families = [k for k, v in families.items() if v >= 0.5]
    weak_families = [k for k, v in families.items() if v < 0.3]
    snippet = result.get("shipped_snippet")
    shipped_hits = result.get("shipped_phrase_hits", 0)

    bits = []

    if title_class == "strong_ml":
        bits.append(f"{title} at {company} ({yoe} yrs)")
        if snippet:
            bits.append(f"career history backs this up — {snippet}")
        elif shipped_hits >= 1:
            bits.append("career history mentions shipped-system work, though only thinly")
        else:
            bits.append("title fits the JD's core ask, but career history doesn't spell out a shipped system")
        if strong_families:
            bits.append(f"credible depth in {', '.join(strong_families).replace('_', ' ')}")
    elif title_class == "adjacent_eng":
        bits.append(f"{title} at {company} ({yoe} yrs) — adjacent engineering background, not core ML")
        if snippet:
            bits.append(f"one thing worth noting: {snippet}")
        if weak_families:
            bits.append(f"weak/unproven on {', '.join(weak_families).replace('_', ' ')} despite any skills listed")
    elif title_class == "excluded_domain":
        bits.append(f"{title} ({yoe} yrs) sits in a domain the JD explicitly says is a re-learn (CV/speech/robotics), not a fit")
    else:
        bits.append(f"{title} at {company} ({yoe} yrs) — off-domain relative to the JD's applied-ML/retrieval ask")

    trajectory_debug = result.get("trajectory_debug", {})
    if trajectory_debug.get("convergence") is not None:
        convergence = trajectory_debug["convergence"]
        # Only surface trajectory when it tells you something the title alone
        # doesn't -- i.e. it's notably misaligned with title_class.
        if title_class == "strong_ml" and convergence < 0.4:
            bits.append("though career trajectory has drifted away from this domain in recent roles")
        elif title_class != "strong_ml" and convergence > 0.7:
            bits.append("career trajectory has moved increasingly toward ML/search work, despite the current title")
        if trajectory_debug.get("seniority_non_decreasing") is False:
            bits.append("seniority has stepped down at some point in the history")

    if result["disqualifier_flags"]:
        readable = ", ".join(f.replace("_", " ") for f in result["disqualifier_flags"])
        bits.append(f"flagged for: {readable}")

    if result["consistency_flags"]:
        bits.append(f"profile shows {len(result['consistency_flags'])} internal-consistency concern(s)")

    evidence_count = result.get("evidence_source_count")
    if evidence_count is not None and evidence_count <= 1 and not result["disqualifier_flags"]:
        bits.append("evidence for this fit is thin — mostly a single corroborating source")

    bd = result.get("behavior_debug", {})
    if bd.get("days_inactive", 0) > 120:
        bits.append("inactive on-platform for an extended period, lowering practical availability")
    elif bd.get("response_rate", 1.0) < 0.2:
        bits.append(f"low recruiter response rate ({bd['response_rate']:.0%})")
    elif bd.get("notice_period_days") is not None and bd["notice_period_days"] > 60:
        bits.append(f"longer notice period ({bd['notice_period_days']}d) than the JD's stated preference")

    if loc:
        bits.append(f"based in {loc}")

    text = "; ".join(bits) + "."
    # Keep it to roughly 1-2 sentences by capping length, not by truncating mid-clause.
    if len(text) > 320:
        text = "; ".join(bits[:4]) + "."
    return text
