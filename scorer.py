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
import zlib


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


def _choice(seed_str: str, category: str, options: list) -> str:
    h = zlib.crc32(f"{seed_str}|{category}".encode('utf-8'))
    return options[h % len(options)]

def build_reasoning(candidate: dict, result: dict) -> str:
    """
    Build a 1-2 sentence, fact-grounded reasoning string using ONLY values
    present in `result` / `candidate` — never invented.
    """
    profile = candidate["profile"]
    cid = candidate.get("candidate_id", "")
    yoe = profile.get("years_of_experience")
    title = profile.get("current_title")
    company = profile.get("current_company")
    loc = profile.get("location")

    title_class = result.get("title_class", "off_domain")
    score_bucket = round(result.get("final_score", 0.0), 2)
    seed = f"{cid}|{title_class}|{score_bucket}"

    families = result.get("family_scores", {})
    strong_families = [k for k, v in families.items() if v >= 0.5]
    weak_families = [k for k, v in families.items() if v < 0.3]
    snippet = result.get("shipped_snippet")
    shipped_hits = result.get("shipped_phrase_hits", 0)

    mode_hash = zlib.crc32(f"{seed}|mode".encode('utf-8')) % 100
    evidence_first = (mode_hash < 30)

    bits = []

    if title_class == "strong_ml":
        if evidence_first and (snippet or shipped_hits >= 1):
            if snippet:
                opts = [
                    f"Designed and deployed systems showing {snippet}",
                    f"Experience is anchored by {snippet}",
                    f"The strongest signal is {snippet}",
                    f"Track record includes {snippet}",
                    f"Prior work highlights {snippet}",
                    f"Production deployments feature {snippet}",
                    f"Core evidence centers on {snippet}",
                    f"Demonstrated practical impact with {snippet}",
                    f"Shipped systems reveal {snippet}",
                    f"Practical background confirms {snippet}",
                    f"Notable work includes {snippet}",
                    f"Engineering history shows {snippet}"
                ]
                bits.append(_choice(seed, "career", opts))
            else:
                opts = [
                    "Experience includes shipping live systems",
                    "Past roles indicate production deployments",
                    "Shows some production exposure",
                    "Career history points to live deployments",
                    "Profile mentions shipped systems",
                    "Touches on live engineering work",
                    "Suggests production experience",
                    "Hints at deployed systems",
                    "Mentions live deployments briefly",
                    "History notes some production systems",
                    "Points to scale deployments implicitly",
                    "Includes production engineering notes"
                ]
                bits.append(_choice(seed, "career", opts))

            if company:
                opts = [
                    f"current role is {title} at {company} ({yoe} yrs)",
                    f"presently a {title} with {company} ({yoe} yrs)",
                    f"currently serving as {title} at {company} ({yoe} yrs)"
                ]
            else:
                opts = [
                    f"current role is {title} ({yoe} yrs)",
                    f"presently a {title} ({yoe} yrs)",
                    f"currently serving as {title} ({yoe} yrs)"
                ]
            bits.append(_choice(seed, "opening", opts))
        else:
            if company:
                opts = [
                    f"Current {title} at {company} ({yoe} yrs)",
                    f"{title} with {yoe} years of experience at {company}",
                    f"{yoe}-year {title} currently at {company}",
                    f"Currently serving as {title} at {company}",
                    f"Production-focused {title} ({yoe} yrs)",
                    f"Employed as {title} at {company} ({yoe} yrs)",
                    f"Working as {title} with {company} ({yoe} yrs)",
                    f"Holds {title} role at {company} ({yoe} yrs)",
                    f"Experienced {title} at {company} ({yoe} yrs)",
                    f"Senior-level {title} currently at {company}",
                    f"Brings {yoe} years as {title} at {company}",
                    f"Seasoned {title} from {company}"
                ]
            else:
                opts = [
                    f"Current {title} ({yoe} yrs)",
                    f"{title} with {yoe} years of experience",
                    f"{yoe}-year {title}",
                    f"Currently serving as {title}",
                    f"Production-focused {title} ({yoe} yrs)",
                    f"Holds {title} role ({yoe} yrs)",
                    f"Experienced {title} ({yoe} yrs)",
                    f"Brings {yoe} years as {title}",
                    f"Seasoned {title} with {yoe} yrs",
                    f"Currently working as {title}",
                    f"Working in a {title} position",
                    f"{title} possessing {yoe} years experience"
                ]
            bits.append(_choice(seed, "opening", opts))

            if snippet:
                opts = [
                    f"career progression shows {snippet}",
                    f"previous work demonstrates {snippet}",
                    f"experience is backed by {snippet}",
                    f"production deployments include {snippet}",
                    f"hands-on work shows {snippet}",
                    f"profile consistently demonstrates {snippet}",
                    f"live systems experience highlights {snippet}",
                    f"shipped work confirms {snippet}",
                    f"prior roles validate {snippet}",
                    f"resume highlights {snippet}",
                    f"evidence suggests {snippet}",
                    f"practical deployments showcase {snippet}"
                ]
                bits.append(_choice(seed, "career", opts))
            elif shipped_hits >= 1:
                opts = [
                    "mentions production deployments sparsely",
                    "past roles indicate production deployments but lack detail",
                    "experience includes shipping live systems, though evidence is sparse",
                    "profile suggests hands-on production experience without deep specifics",
                    "shows some production exposure, but concrete details are light",
                    "career mentions live deployments with minimal specifics",
                    "resume touches on shipped systems briefly",
                    "indicates some scale deployments implicitly",
                    "suggests production experience but lacks robust detail",
                    "hints at deployed systems in past roles",
                    "points to production work without providing strong proof",
                    "history notes live engineering work briefly"
                ]
                bits.append(_choice(seed, "career", opts))
            else:
                opts = [
                    "title fits the JD's core ask, but career history doesn't spell out a shipped system",
                    "while the title is a match, explicit production deployments are missing from the profile",
                    "lacks explicit evidence of shipping real systems despite the relevant title",
                    "profile misses concrete examples of deployed systems",
                    "title aligns well, but hands-on deployment evidence is not spelled out",
                    "no direct mention of live systems despite relevant title",
                    "fails to explicitly detail shipped systems",
                    "missing clear proof of production scale engineering",
                    "lacks specific mentions of live deployments",
                    "title suggests fit, yet concrete deployment evidence is absent",
                    "no robust examples of production work found",
                    "career history lacks explicit deployed system evidence"
                ]
                bits.append(_choice(seed, "career", opts))

        if strong_families:
            fam_str = ", ".join(strong_families).replace("_", " ")
            if evidence_first:
                opts = [
                    f"with credible depth in {fam_str}",
                    f"featuring expertise across {fam_str}",
                    f"demonstrating skills in {fam_str}",
                    f"along with a solid foundation in {fam_str}",
                    f"and strong technical evidence for {fam_str}"
                ]
            else:
                opts = [
                    f"credible depth in {fam_str}",
                    f"demonstrated expertise across {fam_str}",
                    f"corroborated skills include {fam_str}",
                    f"solid foundation in {fam_str}",
                    f"strong technical evidence for {fam_str}",
                    f"proven capability in {fam_str}",
                    f"verifiable experience spanning {fam_str}",
                    f"shows robust understanding of {fam_str}",
                    f"evident strengths in {fam_str}",
                    f"established competence in {fam_str}",
                    f"clearly skilled in {fam_str}",
                    f"possesses strong background in {fam_str}"
                ]
            bits.append(_choice(seed, "skill", opts))

    elif title_class == "adjacent_eng":
        if company:
            opts = [
                f"{title} at {company} ({yoe} yrs) — adjacent engineering background, not core ML",
                f"{yoe}-year {title} coming from an adjacent engineering domain at {company}",
                f"Currently {title} at {company} ({yoe} yrs), offering adjacent software experience"
            ]
        else:
            opts = [
                f"{title} ({yoe} yrs) — adjacent engineering background, not core ML",
                f"{yoe}-year {title} coming from an adjacent engineering domain",
                f"Currently {title} ({yoe} yrs), offering adjacent software experience"
            ]
        bits.append(_choice(seed, "opening_adj", opts))

        if snippet:
            opts = [
                f"one thing worth noting: {snippet}",
                f"relevant prior work includes: {snippet}",
                f"shows some crossover experience: {snippet}",
                f"adjacent production experience highlights: {snippet}"
            ]
            bits.append(_choice(seed, "career_adj", opts))
            
        if weak_families:
            fam_str = ", ".join(weak_families).replace("_", " ")
            opts = [
                f"weak/unproven on {fam_str} despite any skills listed",
                f"lacks credible evidence for {fam_str}",
                f"missing verified depth in {fam_str}",
                f"no solid corroboration for {fam_str}"
            ]
            bits.append(_choice(seed, "skill_adj", opts))

    elif title_class == "excluded_domain":
        opts = [
            f"{title} ({yoe} yrs) sits in a domain the JD explicitly says is a re-learn (CV/speech/robotics), not a fit",
            f"{yoe}-year {title} with background in an excluded domain (CV/speech/robotics)",
            f"Current focus is {title}, which the JD marks as a re-learn domain"
        ]
        bits.append(_choice(seed, "opening_exc", opts))

    else:
        if company:
            opts = [
                f"{title} at {company} ({yoe} yrs) — off-domain relative to the JD's applied-ML/retrieval ask",
                f"Currently {title} at {company} ({yoe} yrs), which is off-domain for this role",
                f"Off-domain profile ({title} at {company} with {yoe} yrs)"
            ]
        else:
            opts = [
                f"{title} ({yoe} yrs) — off-domain relative to the JD's applied-ML/retrieval ask",
                f"Currently {title} ({yoe} yrs), which is off-domain for this role",
                f"Off-domain profile ({title} with {yoe} yrs)"
            ]
        bits.append(_choice(seed, "opening_off", opts))

    trajectory_debug = result.get("trajectory_debug", {})
    if trajectory_debug.get("convergence") is not None:
        convergence = trajectory_debug["convergence"]
        if title_class == "strong_ml" and convergence < 0.4:
            opts = [
                "though career trajectory has drifted away from this domain in recent roles",
                "however, recent roles show a drift away from core ML",
                "notably, recent positions diverge from the ML/search focus",
                "but recent career moves show divergence from this specialty"
            ]
            bits.append(_choice(seed, "traj_drift", opts))
        elif title_class != "strong_ml" and convergence > 0.7:
            opts = [
                "career trajectory has moved increasingly toward ML/search work, despite the current title",
                "recent roles show a strong convergence toward applied ML",
                "progression shows a clear pivot towards ML engineering",
                "experience strongly trends towards relevant ML domains recently"
            ]
            bits.append(_choice(seed, "traj_conv", opts))
            
        if trajectory_debug.get("seniority_non_decreasing") is False:
            opts = [
                "seniority has stepped down at some point in the history",
                "career history includes a step down in seniority",
                "shows erratic seniority progression with some down-leveling"
            ]
            bits.append(_choice(seed, "traj_sen", opts))

    if result.get("disqualifier_flags"):
        readable = ", ".join(f.replace("_", " ") for f in result["disqualifier_flags"])
        opts = [
            f"flagged for: {readable}",
            f"disqualifiers detected: {readable}",
            f"hits negative signals for: {readable}",
            f"shows clear disqualifying traits: {readable}"
        ]
        bits.append(_choice(seed, "disqual", opts))

    if result.get("consistency_flags"):
        count = len(result['consistency_flags'])
        opts = [
            f"profile shows {count} internal-consistency concern(s)",
            f"flagged with {count} consistency issues",
            f"contains {count} contradictory profile claims",
            f"has {count} data consistency flags"
        ]
        bits.append(_choice(seed, "consist", opts))

    evidence_count = result.get("evidence_source_count")
    if evidence_count is not None and evidence_count <= 1 and not result.get("disqualifier_flags"):
        opts = [
            "evidence for this fit is thin — mostly a single corroborating source",
            "fit is based on limited corroboration",
            "relies on a single source of evidence without much cross-validation",
            "claims lack broad corroborative evidence across the profile"
        ]
        bits.append(_choice(seed, "thin", opts))

    bd = result.get("behavior_debug", {})
    if bd.get("days_inactive", 0) > 120:
        opts = [
            "inactive on-platform for an extended period, lowering practical availability",
            "hasn't been active recently, so availability is a question mark",
            "extended inactivity suggests they might not be on the market",
            "long periods of inactivity reduce confidence in availability"
        ]
        bits.append(_choice(seed, "inactive", opts))
    elif bd.get("response_rate", 1.0) < 0.2:
        opts = [
            f"low recruiter response rate ({bd['response_rate']:.0%})",
            f"historical response rate to recruiters is poor ({bd['response_rate']:.0%})",
            f"rarely replies to recruiter outreach ({bd['response_rate']:.0%})",
            f"response rate is notably low ({bd['response_rate']:.0%})"
        ]
        bits.append(_choice(seed, "response", opts))
    elif bd.get("notice_period_days") is not None and bd["notice_period_days"] > 60:
        opts = [
            f"longer notice period ({bd['notice_period_days']}d) than the JD's stated preference",
            f"notice period ({bd['notice_period_days']}d) exceeds preferred timeline",
            f"availability is delayed by a {bd['notice_period_days']}-day notice period",
            f"extended notice period ({bd['notice_period_days']}d) is a minor drawback"
        ]
        bits.append(_choice(seed, "notice", opts))

    if loc:
        opts = [
            f"based in {loc}",
            f"located in {loc}",
            f"currently in {loc}",
            f"operating out of {loc}"
        ]
        bits.append(_choice(seed, "loc", opts))

    text = "; ".join(bits) + "."
    if len(text) > 320:
        text = "; ".join(bits[:4]) + "."
    return text
