"""
features.py — turns one raw candidate JSON record into a flat dict of
interpretable features. No ML model here on purpose: every value should be
traceable back to a field in candidate_schema.json, so the scorer (and the
reasoning generator) can point at exactly why a number is what it is.

v2 additions (post-feedback-review), each tied to a specific gap that was
found either in the JD text or by inspecting real output on the full 100K
pool:
  - skill_domain_depth_bonus(): rewards credible evidence ACROSS a skill
    domain (retrieval + vector-db + eval), not just one matched keyword.
  - career_trajectory_score(): scores domain convergence + earned seniority
    progression over time -- independent of (and complementary to) the
    existing title_chaser disqualifier, which only fires on SHORT-tenure
    escalation. A long, earned climb should score WELL here.
  - notice_period_fit(): the JD has an explicit notice-period preference
    curve that nothing previously used.
  - evidence_coverage(): counts how many independent profile sections
    corroborate a candidate's claimed fit. Diagnostic only (feeds the debug
    report and an occasional "honest concern" line in reasoning) -- it does
    NOT feed final_score, to avoid double-counting signal already captured
    by skill_credibility / title_career_match.
  - shipped_evidence_snippet(): pulls the actual sentence fragment from a
    candidate's OWN career_history description that matched a "shipped a
    real system" phrase, so build_reasoning() can quote real evidence
    instead of reusing one fixed sentence for every strong_ml candidate.
    This directly targets the Stage-4 "Variation" / "no hallucination"
    checks in submission_spec.md, and was added after finding that 94/100
    rows in the real submission shared one identical clause verbatim.
  - detect_disqualifiers() now also catches tech_lead_no_code_18mo via the
    candidate's CURRENT title (management-track keyword + >=18mo tenure in
    that role), not just via phrase-matching free text.
"""

from __future__ import annotations
from datetime import date, datetime
import re
from typing import Any

import jd_requirements as jd

TODAY = date(2026, 6, 30)  # fixed "now" for reproducibility; override via --as-of if needed


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _text_contains_any(text: str, needles: list) -> bool:
    text = text.lower()
    return any(n.lower() in text for n in needles)


def _count_matches(text: str, needles: list) -> int:
    text = text.lower()
    return sum(1 for n in needles if n.lower() in text)


# ---------------------------------------------------------------------------
# Title classification
# ---------------------------------------------------------------------------

def classify_title(title: str) -> str:
    """Return one of: strong_ml, adjacent_eng, excluded_domain, off_domain."""
    t = title.lower()
    if _text_contains_any(t, jd.EXCLUDED_DOMAIN_TITLE_KEYWORDS):
        return "excluded_domain"
    if _text_contains_any(t, jd.STRONG_TITLE_KEYWORDS):
        return "strong_ml"
    if _text_contains_any(t, jd.ADJACENT_TITLE_KEYWORDS):
        return "adjacent_eng"
    return "off_domain"


# ---------------------------------------------------------------------------
# Seniority ladder — module-level so both detect_disqualifiers (title_chaser)
# and career_trajectory_score (earned progression) share one definition.
# ---------------------------------------------------------------------------

SENIORITY_LADDER = [
    "intern", "associate", "junior", "engineer", "senior",
    "lead", "staff", "principal", "director", "vp", "head",
]


def _seniority_rank(title: str) -> int:
    t = title.lower()
    for i, rung in enumerate(SENIORITY_LADDER):
        if rung in t:
            return i
    return -1


def _sorted_history(history: list) -> list:
    """Oldest-first. Falls back to input order for entries with no parseable
    start_date rather than dropping them."""
    def keyfn(h):
        d = _parse_date(h.get("start_date"))
        return d or date.min
    return sorted(history, key=keyfn)


# ---------------------------------------------------------------------------
# Skill credibility
# ---------------------------------------------------------------------------

PROFICIENCY_SCORE = {"beginner": 0.25, "intermediate": 0.5, "advanced": 0.75, "expert": 1.0}


def skill_credibility(skill: dict, assessment_scores: dict) -> float:
    """
    0-1 credibility for a single claimed skill. A skill is only as credible as
    its corroborating evidence: time spent, endorsements, and (if present) the
    candidate's own Redrob assessment score for that exact skill.
    A high "proficiency" label with near-zero duration and no assessment score
    is exactly the keyword-stuffing pattern the JD warns about.
    """
    prof = PROFICIENCY_SCORE.get(skill.get("proficiency", "beginner"), 0.25)
    months = skill.get("duration_months", 0) or 0
    endorsements = skill.get("endorsements", 0) or 0

    duration_factor = min(months / 24.0, 1.0)  # 2 years -> full credit
    endorsement_factor = min(endorsements / 20.0, 1.0)

    name = skill.get("name", "")
    assessed = assessment_scores.get(name)
    if assessed is not None:
        assess_factor = assessed / 100.0
        # If the candidate claims advanced/expert but assessment is mediocre,
        # this is the strongest single stuffing signal available — let it dominate.
        evidence = 0.5 * assess_factor + 0.25 * duration_factor + 0.25 * endorsement_factor
    else:
        evidence = 0.6 * duration_factor + 0.4 * endorsement_factor

    # Credibility = how much the evidence actually supports the claimed level.
    # A claimed "expert" with low evidence is penalized harder than a claimed
    # "beginner" with low evidence (nothing to disprove there).
    return prof * (0.3 + 0.7 * evidence)


def core_skill_family_coverage(skills: list, assessment_scores: dict) -> dict:
    """For each required family, return the best credibility found among matching skills."""
    out = {}
    for family, needles in jd.CORE_SKILL_FAMILIES.items():
        best = 0.0
        for s in skills:
            if _text_contains_any(s.get("name", ""), needles):
                best = max(best, skill_credibility(s, assessment_scores))
        out[family] = best
    return out


def skill_domain_depth_bonus(skills: list, assessment_scores: dict, threshold: float = 0.35):
    """
    Reward credible evidence ACROSS a skill domain, not just one matched
    keyword. SKILL_TAXONOMY groups embeddings_retrieval + vector_db_hybrid_search
    + eval_frameworks under "search_engineering" because the JD explicitly
    asks for breadth ("embeddings, retrieval, ranking, LLMs, fine-tuning"
    together) -- a candidate credible in all three is a much harder pattern
    to fake than stuffing one buzzword into a skills list.

    Single-leaf domains (e.g. "core_engineering": just python) are skipped --
    there's no "breadth" to reward within one leaf.

    Returns (bonus in [0,1], debug dict of domain -> {credible_subdomains, of}).
    """
    debug = {}
    bonus_total = 0.0
    n_domains = 0
    for domain, subdomains in jd.SKILL_TAXONOMY.items():
        if len(subdomains) < 2:
            continue
        n_domains += 1
        credible = 0
        for leaf, needles in subdomains.items():
            best = 0.0
            for s in skills:
                if _text_contains_any(s.get("name", ""), needles):
                    best = max(best, skill_credibility(s, assessment_scores))
            if best >= threshold:
                credible += 1
        debug[domain] = {"credible_subdomains": credible, "of": len(subdomains)}
        bonus_total += credible / len(subdomains)
    bonus = bonus_total / n_domains if n_domains else 0.0
    return bonus, debug


def nice_to_have_bonus(skills: list) -> float:
    names = " | ".join(s.get("name", "") for s in skills)
    hits = _count_matches(names, jd.NICE_TO_HAVE_SKILLS)
    return min(hits / 4.0, 1.0)  # saturate after ~4 distinct nice-to-have hits


# ---------------------------------------------------------------------------
# Career-history / title-match scoring (the JD's actual core ask)
# ---------------------------------------------------------------------------

def career_history_text(candidate: dict) -> str:
    parts = []
    for h in candidate.get("career_history", []):
        parts.append(h.get("title", ""))
        parts.append(h.get("description", ""))
    parts.append(candidate["profile"].get("summary", ""))
    parts.append(candidate["profile"].get("headline", ""))
    return " \n".join(parts)


SHIPPED_SYSTEM_PHRASES = [
    "shipped", "in production", "to real users", "at scale", "live system",
    "deployed", "owned the ranking", "owned the retrieval", "migrated",
    "a/b test", "click-through", "ctr", "conversion", "revenue-per-search",
    "offline-online correlation", "offline to online",
]

RESEARCH_ONLY_PHRASES = [
    "research lab", "academic lab", "phd research", "published a paper",
    "research scientist (no deployment)", "purely research",
]

LANGCHAIN_WRAPPER_PHRASES = [
    "langchain", "called openai", "openai api", "wrapper around gpt",
    "prompt engineering only",
]

TECH_LEAD_NO_CODE_PHRASES = [
    "stopped writing code", "moved into architecture", "purely architecture",
    "no longer write production code", "tech lead role with no coding",
]

FRAMEWORK_TUTORIAL_PHRASES = [
    "my blog posts are how i used", "github is full of langchain tutorials",
    "built a demo for my blog", "wrote a tutorial series on",
]


def title_career_match_score(candidate: dict):
    """
    Core JD-fit signal. Combines:
      - title classification (strong_ml > adjacent_eng > off_domain/excluded)
      - presence of "shipped a real system" language in career_history
      - explicit penalty signals (research-only / langchain-only / etc.) feed
        into disqualifier detection elsewhere, not here.
    Returns (score 0-1, debug info dict).
    """
    title = candidate["profile"]["current_title"]
    title_class = classify_title(title)
    text = career_history_text(candidate).lower()

    shipped_hits = _count_matches(text, SHIPPED_SYSTEM_PHRASES)
    shipped_score = min(shipped_hits / 3.0, 1.0)

    base = {
        "strong_ml": 0.75,
        "adjacent_eng": 0.35,
        "excluded_domain": 0.15,
        "off_domain": 0.05,
    }[title_class]

    score = base + 0.25 * shipped_score
    score = min(score, 1.0)

    return score, {
        "title_class": title_class,
        "shipped_phrase_hits": shipped_hits,
    }


def shipped_evidence_snippet(candidate: dict, max_words: int = 18):
    """
    Find the actual fragment of the candidate's OWN career_history
    description containing a "shipped a real system" phrase, so the
    reasoning generator can quote THIS candidate's evidence instead of a
    fixed sentence. Returns None if nothing matched (caller falls back to a
    more conservative, honest framing).

    This is the direct fix for the templated-reasoning problem found in the
    real submission: 94/100 top rows previously shared one identical clause.
    """
    for h in candidate.get("career_history", []):
        desc = h.get("description", "") or ""
        low = desc.lower()
        hit_idx = None
        for phrase in SHIPPED_SYSTEM_PHRASES:
            idx = low.find(phrase.lower())
            if idx != -1:
                hit_idx = idx
                break
        if hit_idx is None:
            continue

        words = desc.split()
        char_count = 0
        center_word_idx = 0
        for wi, w in enumerate(words):
            if char_count >= hit_idx:
                center_word_idx = wi
                break
            char_count += len(w) + 1

        start = max(0, center_word_idx - max_words // 2)
        end = min(len(words), start + max_words)
        snippet = " ".join(words[start:end]).strip(" ,.;")
        if not snippet:
            continue

        role = h.get("title", "")
        company = h.get("company", "")
        if role or company:
            return f'{role} at {company}: "{snippet}"'.strip()
        return f'"{snippet}"'
    return None


# ---------------------------------------------------------------------------
# Career trajectory (NEW)
# ---------------------------------------------------------------------------

_DOMAIN_TRACK_SCORE = {
    "strong_ml": 1.0,
    "adjacent_eng": 0.5,
    "excluded_domain": 0.2,
    "off_domain": 0.0,
}


def career_trajectory_score(candidate: dict):
    """
    Two things the base title/career-match score doesn't capture on its own:
      1. Domain convergence -- is the career trending TOWARD the JD's actual
         domain over time (recent-weighted), not just currently sitting there?
      2. Earned seniority progression -- non-decreasing rank across REAL
         tenures is a normal, positive signal.
    Deliberately independent of detect_disqualifiers' title_chaser flag:
    title_chaser only fires on SHORT-tenure escalation (the JD's literal
    complaint). A long, earned climb through several companies -- exactly
    CAND_0000031's pattern in the sample data (Zomato -> Uber -> Mad Street
    Den -> Swiggy) -- should score WELL here, not be penalized twice.

    Returns (score 0-1, debug dict). Candidates with <2 career_history
    entries get a neutral 0.5 -- not enough signal to reward or punish.
    """
    history = candidate.get("career_history", [])
    if len(history) < 2:
        return 0.5, {"reason": "insufficient_history_for_trajectory"}

    ordered = _sorted_history(history)
    classes = [classify_title(h.get("title", "")) for h in ordered]
    domain_track = [_DOMAIN_TRACK_SCORE[c] for c in classes]

    # Recent roles count more toward "where this career is heading".
    weights = list(range(1, len(domain_track) + 1))
    convergence = sum(w * d for w, d in zip(weights, domain_track)) / sum(weights)

    ranks = [_seniority_rank(h.get("title", "")) for h in ordered]
    valid_ranks = [r for r in ranks if r >= 0]
    if len(valid_ranks) >= 2:
        non_decreasing = all(b >= a for a, b in zip(valid_ranks, valid_ranks[1:]))
    else:
        non_decreasing = True  # not enough title-ladder signal either way

    score = 0.7 * convergence + 0.3 * (1.0 if non_decreasing else 0.4)
    return min(1.0, score), {
        "domain_track": classes,
        "convergence": round(convergence, 3),
        "seniority_non_decreasing": non_decreasing,
    }


# ---------------------------------------------------------------------------
# Disqualifier detection
# ---------------------------------------------------------------------------

def detect_disqualifiers(candidate: dict, title_class: str) -> list:
    flags = []
    text = career_history_text(candidate).lower()
    profile = candidate["profile"]
    yoe = profile.get("years_of_experience", 0)
    history = candidate.get("career_history", [])

    if _text_contains_any(text, RESEARCH_ONLY_PHRASES):
        flags.append("pure_research_no_production")

    if _text_contains_any(text, LANGCHAIN_WRAPPER_PHRASES):
        # only a disqualifier if it looks recent/sole experience
        recent = history and history[0].get("duration_months", 999) <= 12
        if recent or yoe <= 1.5:
            flags.append("recent_langchain_only")

    if _text_contains_any(text, TECH_LEAD_NO_CODE_PHRASES):
        flags.append("tech_lead_no_code_18mo")
    elif _text_contains_any(profile.get("current_title", ""), jd.MANAGEMENT_TRACK_TITLE_KEYWORDS):
        # Title-based fallback for the same JD disqualifier: catches a
        # candidate whose CURRENT role is plainly management-track even when
        # their profile text never uses one of the literal phrases above.
        current_role = next((h for h in history if h.get("is_current")), None)
        if current_role and (current_role.get("duration_months") or 0) >= 18:
            flags.append("tech_lead_no_code_18mo")

    # title-chaser: the JD's actual complaint is escalating titles (Senior -> Staff
    # -> Principal) via short hops, NOT just short tenure on its own — steady IC
    # progression through several companies in a fast-moving field (e.g. several
    # ~14-18mo ML roles at different product companies) is normal and should NOT
    # be penalized. Require BOTH short average tenure AND a seniority-escalation
    # pattern in the title strings before flagging.
    if len(history) >= 3:
        avg_tenure = sum(h.get("duration_months", 0) for h in history) / len(history)
        ranks = [_seniority_rank(h.get("title", "")) for h in history]
        ranks = [r for r in ranks if r >= 0]
        escalating = len(ranks) >= 2 and ranks == sorted(ranks) and ranks[-1] > ranks[0]
        if avg_tenure <= 15 and escalating:
            flags.append("title_chaser")

    if _text_contains_any(text, FRAMEWORK_TUTORIAL_PHRASES) and title_class != "strong_ml":
        flags.append("framework_tutorial_only")

    companies = [h.get("company", "").lower() for h in history] + [profile.get("current_company", "").lower()]
    if companies and all(any(cf in c for cf in jd.CONSULTING_FIRMS) for c in companies if c):
        flags.append("consulting_only_no_product")

    if title_class == "excluded_domain" and not _text_contains_any(text, ["nlp", "information retrieval", "language model"]):
        flags.append("excluded_domain_no_nlp")

    return flags


# ---------------------------------------------------------------------------
# Experience-band fit
# ---------------------------------------------------------------------------

def experience_fit_score(yoe: float) -> float:
    lo, hi = jd.EXPERIENCE_BAND
    if lo <= yoe <= hi:
        return 1.0
    dist = (lo - yoe) if yoe < lo else (yoe - hi)
    return max(0.0, 1.0 - dist / jd.EXPERIENCE_SOFT_MARGIN)


# ---------------------------------------------------------------------------
# Notice period (NEW) — JD: sub-30-day preferred, 30+ still in scope, "bar
# gets higher" (soft curve, not a cliff).
# ---------------------------------------------------------------------------

def notice_period_fit(days):
    if days is None:
        return jd.NOTICE_PERIOD_UNKNOWN
    for breakpoint_days, value in jd.NOTICE_PERIOD_BREAKPOINTS:
        if days <= breakpoint_days:
            return value
    return jd.NOTICE_PERIOD_FLOOR


# ---------------------------------------------------------------------------
# Behavioral / availability signals
# ---------------------------------------------------------------------------

def behavioral_score(signals: dict):
    last_active = _parse_date(signals.get("last_active_date"))
    days_inactive = (TODAY - last_active).days if last_active else 9999
    recency = max(0.0, 1.0 - days_inactive / 120.0)  # fully decayed by ~4 months idle

    open_to_work = 1.0 if signals.get("open_to_work_flag") else 0.4
    response_rate = signals.get("recruiter_response_rate", 0.0) or 0.0
    interview_completion = signals.get("interview_completion_rate", 0.0) or 0.0
    notice_days = signals.get("notice_period_days")
    notice_fit = notice_period_fit(notice_days)

    score = (
        0.30 * recency
        + 0.15 * open_to_work
        + 0.25 * response_rate
        + 0.15 * interview_completion
        + 0.15 * notice_fit
    )
    return min(score, 1.0), {
        "days_inactive": days_inactive,
        "response_rate": response_rate,
        "notice_period_days": notice_days,
        "notice_fit": notice_fit,
    }


# ---------------------------------------------------------------------------
# Location fit
# ---------------------------------------------------------------------------

def location_fit_score(profile: dict) -> float:
    loc = (profile.get("location", "") + " " + profile.get("country", "")).lower()
    if profile.get("country", "").lower() != "india":
        return 0.15  # JD: no visa sponsorship, case-by-case outside India
    if _text_contains_any(loc, jd.PREFERRED_LOCATIONS):
        return 1.0
    if _text_contains_any(loc, jd.TIER1_INDIA_LOCATIONS):
        return 0.7
    return 0.45  # other India city — possible but not called out as welcome


# ---------------------------------------------------------------------------
# Consistency / honeypot checks
# ---------------------------------------------------------------------------

def consistency_flags(candidate: dict) -> list:
    """
    Internal-inconsistency checks computable from the schema we actually have.
    NOTE: the spec's example honeypot ("8 yrs at a company founded 3 yrs ago")
    needs a company_founded_year field that isn't in candidate_schema.json —
    so we can't check that exact pattern. We check the inconsistencies that
    *are* computable from the real fields.
    """
    flags = []
    profile = candidate["profile"]
    yoe = profile.get("years_of_experience", 0)

    for edu in candidate.get("education", []):
        end_year = edu.get("end_year")
        if end_year:
            years_since_grad = TODAY.year - end_year
            # allow 1 year slack for overlap (internship before graduation, etc.)
            if years_since_grad < yoe - 1:
                flags.append(f"yoe_exceeds_time_since_graduation({yoe}yoe_vs_{years_since_grad}y_since_grad)")

    for s in candidate.get("skills", []):
        months = s.get("duration_months", 0) or 0
        if s.get("proficiency") == "expert" and months <= 3:
            flags.append(f"expert_claim_with_{months}mo_duration({s.get('name')})")

    assessment_scores = candidate.get("redrob_signals", {}).get("skill_assessment_scores", {})
    for s in candidate.get("skills", []):
        name = s.get("name", "")
        if name in assessment_scores and s.get("proficiency") in ("advanced", "expert"):
            if assessment_scores[name] < 45:
                flags.append(f"claimed_{s['proficiency']}_but_assessment_{assessment_scores[name]}({name})")

    # career_history total duration vs years_of_experience sanity check
    total_months = sum(h.get("duration_months", 0) or 0 for h in candidate.get("career_history", []))
    implied_years = total_months / 12.0
    if implied_years > 0 and abs(implied_years - yoe) > max(2.5, yoe * 0.4):
        flags.append(f"career_history_span_mismatch({implied_years:.1f}y_history_vs_{yoe}yoe)")

    return flags


def consistency_penalty(flags: list) -> float:
    """Returns a multiplier in (0,1]. More flags -> lower multiplier, capped."""
    if not flags:
        return 1.0
    return max(0.3, 1.0 - 0.25 * len(flags))


# ---------------------------------------------------------------------------
# Evidence coverage (NEW) — diagnostic only, does not feed final_score.
# ---------------------------------------------------------------------------

def evidence_coverage(candidate: dict, family_scores: dict, shipped_hits: int):
    """
    Counts how many INDEPENDENT profile sections corroborate the candidate's
    claimed ML/search fit. A candidate who looks good from exactly one
    section (e.g. only the skills list) is a much easier pattern to fake than
    one corroborated across skills, career history, assessments, and
    activity. Purely diagnostic: feeds the debug/explainability report and an
    occasional "thin evidence" honest-concern line in reasoning -- it does
    NOT adjust final_score, since most of this signal is already captured by
    skill_credibility (which already uses assessment scores) and
    title_career_match (which already uses career_history text).

    Returns (count, list of source names).
    """
    sources = []
    if any(v >= 0.4 for v in family_scores.values()):
        sources.append("skills")
    if shipped_hits >= 1:
        sources.append("career_history")
    signals = candidate.get("redrob_signals", {})
    if signals.get("skill_assessment_scores"):
        sources.append("redrob_assessment")
    if (signals.get("github_activity_score") if signals.get("github_activity_score") is not None else -1) > 30:
        sources.append("github_activity")
    profile = candidate.get("profile", {})
    summary_text = (profile.get("summary", "") + " " + profile.get("headline", "")).lower()
    if _text_contains_any(summary_text, ["retrieval", "ranking", "embedding", "search", "recommendation"]):
        sources.append("summary")
    return len(sources), sources


# ---------------------------------------------------------------------------
# Lightweight semantic similarity (no embeddings needed — token overlap)
# Kept deliberately as a MINOR signal: this is exactly the channel keyword-
# stuffers exploit, so it gets low weight in jd_requirements.WEIGHTS.
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z]{3,}")


def _tokenize(text: str):
    return set(_WORD_RE.findall(text.lower()))


def jaccard_similarity(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0
