"""
test_features.py — sanity checks for the scoring components. Run with:
    python -m pytest test_features.py -v
or just:
    python test_features.py

These aren't exhaustive, but they pin down the exact behaviors we reasoned
about while building this (keyword-stuffer demotion, true-fit promotion,
honeypot detection, title-chaser false-positive fix) so a regression doesn't
silently creep back in.

v2 additions: tests for the skill-domain depth bonus, career trajectory,
notice-period curve, the broadened tech-lead-no-code detection, evidence
coverage, and -- most importantly -- a direct regression test for the
templated-reasoning bug found in the real 100K-pool output (94/100 rows
sharing one identical clause verbatim).
"""

import json
from pathlib import Path

import features as feat
import jd_requirements as jd
import scorer

DATA = Path(__file__).parent / "data" / "sample_candidates.json"


def _load(cid):
    with open(DATA) as f:
        candidates = json.load(f)
    matches = [c for c in candidates if c["candidate_id"] == cid]
    assert matches, f"{cid} not found in sample data"
    return matches[0]


def test_title_classification():
    assert feat.classify_title("Recommendation Systems Engineer") == "strong_ml"
    assert feat.classify_title("Backend Engineer") == "adjacent_eng"
    assert feat.classify_title("Marketing Manager") == "off_domain"
    assert feat.classify_title("Computer Vision Engineer") == "excluded_domain"


def test_skill_credibility_penalizes_unevidenced_expert_claims():
    high_evidence = {"name": "Embeddings", "proficiency": "expert", "endorsements": 40, "duration_months": 36}
    low_evidence = {"name": "Embeddings", "proficiency": "expert", "endorsements": 0, "duration_months": 1}
    assert feat.skill_credibility(high_evidence, {}) > feat.skill_credibility(low_evidence, {})


def test_skill_credibility_uses_assessment_score_when_available():
    claimed_advanced_low_assessment = {"name": "NLP", "proficiency": "advanced", "endorsements": 30, "duration_months": 24}
    score_low = feat.skill_credibility(claimed_advanced_low_assessment, {"NLP": 38.0})
    score_high = feat.skill_credibility(claimed_advanced_low_assessment, {"NLP": 95.0})
    assert score_high > score_low


def test_strong_fit_outranks_keyword_stuffer():
    """The two cases this whole hackathon hinges on."""
    strong = _load("CAND_0000031")   # Recommendation Systems Engineer, real shipped work
    stuffer = _load("CAND_0000001")  # Backend Engineer with AI-keyword-stuffed skills list

    jd_text = (Path(__file__).parent / "data" / "job_description.md").read_text()
    jd_tokens = feat._tokenize(jd_text)

    r_strong = scorer.score_candidate(strong, jd_tokens)
    r_stuffer = scorer.score_candidate(stuffer, jd_tokens)

    assert r_strong["final_score"] > r_stuffer["final_score"], (
        f"strong={r_strong['final_score']} should beat stuffer={r_stuffer['final_score']}"
    )
    assert r_strong["title_class"] == "strong_ml"
    assert r_stuffer["title_class"] == "adjacent_eng"


def test_title_chaser_does_not_flag_steady_ic_progression():
    """Regression test: CAND_0000031 has 4 jobs averaging ~17.5mo each, but all
    are similar-seniority IC roles at real product companies — not an
    escalating-title pattern. Should NOT be flagged as title_chaser."""
    strong = _load("CAND_0000031")
    flags = feat.detect_disqualifiers(strong, "strong_ml")
    assert "title_chaser" not in flags


def test_consistency_flags_catch_grad_year_yoe_mismatch():
    candidate = {
        "profile": {"years_of_experience": 12.0},
        "education": [{"end_year": 2024, "institution": "X", "degree": "B.E.",
                        "field_of_study": "CS", "start_year": 2020}],
        "skills": [],
        "career_history": [],
        "redrob_signals": {},
    }
    flags = feat.consistency_flags(candidate)
    assert any("yoe_exceeds_time_since_graduation" in f for f in flags)


def test_consistency_penalty_is_bounded():
    assert feat.consistency_penalty([]) == 1.0
    # many flags should not drive the multiplier below the floor
    many_flags = ["x"] * 20
    assert feat.consistency_penalty(many_flags) == 0.3


def test_experience_fit_band():
    assert feat.experience_fit_score(6) == 1.0  # inside 5-9 band
    assert feat.experience_fit_score(5) == 1.0
    assert feat.experience_fit_score(9) == 1.0
    assert 0 < feat.experience_fit_score(3) < 1.0  # below band, soft penalty
    assert feat.experience_fit_score(20) == 0.0  # way below/above margin saturates


def test_location_fit_prefers_pune_noida():
    assert feat.location_fit_score({"location": "Pune, Maharashtra", "country": "India"}) == 1.0
    assert feat.location_fit_score({"location": "Hyderabad, Telangana", "country": "India"}) == 0.7
    assert feat.location_fit_score({"location": "Toronto", "country": "Canada"}) == 0.15


def test_csv_sort_matches_rounded_score_not_raw_float():
    """Regression test: found on the real 100K pool. CAND_0042100 (raw score
    0.71280001) and CAND_0030468 (raw score 0.71279996) both round to 0.7128
    but the validator requires ascending candidate_id for any score that TIES
    AS WRITTEN. Sorting on the raw float before rounding can put them in the
    wrong order. write_submission_csv must sort on the already-rounded value."""
    import rank as rank_module

    class FakeResult(dict):
        pass

    c1 = {"candidate_id": "CAND_0042100"}
    c2 = {"candidate_id": "CAND_0030468"}
    r1 = {"final_score": 0.71280001, "title_class": "strong_ml", "family_scores": {},
          "disqualifier_flags": [], "consistency_flags": [],
          "behavior_debug": {"days_inactive": 0, "response_rate": 1.0}, "shipped_phrase_hits": 0}
    r2 = {"final_score": 0.71279996, "title_class": "strong_ml", "family_scores": {},
          "disqualifier_flags": [], "consistency_flags": [],
          "behavior_debug": {"days_inactive": 0, "response_rate": 1.0}, "shipped_phrase_hits": 0}
    # populate the fields build_reasoning needs
    for c in (c1, c2):
        c["profile"] = {"years_of_experience": 5, "current_title": "x", "current_company": "y", "location": "z"}

    import tempfile, csv as csv_mod
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        out_path = Path(tmp.name)
    rank_module.write_submission_csv([(c1, r1), (c2, r2)], out_path, top_n=2)

    with open(out_path) as f:
        rows = list(csv_mod.DictReader(f))
    assert rows[0]["score"] == rows[1]["score"] == "0.7128"
    assert rows[0]["candidate_id"] < rows[1]["candidate_id"], (
        "tied (after rounding) scores must be in ascending candidate_id order"
    )
    out_path.unlink()


# ---------------------------------------------------------------------------
# v2 tests
# ---------------------------------------------------------------------------

def test_weights_sum_to_one():
    assert abs(sum(jd.WEIGHTS.values()) - 1.0) < 1e-9


def test_skill_domain_depth_bonus_rewards_breadth_over_single_keyword():
    """A candidate credible in retrieval, vector-db, AND eval (real breadth)
    should score a higher depth bonus than one credible in only one of the
    three -- breadth across a domain is much harder to fake than one keyword."""
    assessment = {}
    breadth_skills = [
        {"name": "embeddings", "proficiency": "advanced", "endorsements": 15, "duration_months": 24},
        {"name": "faiss", "proficiency": "advanced", "endorsements": 10, "duration_months": 20},
        {"name": "ndcg evaluation framework", "proficiency": "advanced", "endorsements": 8, "duration_months": 18},
    ]
    single_skill = [
        {"name": "embeddings", "proficiency": "advanced", "endorsements": 15, "duration_months": 24},
    ]
    bonus_breadth, _ = feat.skill_domain_depth_bonus(breadth_skills, assessment)
    bonus_single, _ = feat.skill_domain_depth_bonus(single_skill, assessment)
    assert bonus_breadth > bonus_single


def test_career_trajectory_rewards_earned_progression():
    history = [
        {"company": "Zomato", "title": "Software Engineer", "start_date": "2017-01-01",
         "end_date": "2018-06-01", "duration_months": 17, "is_current": False,
         "industry": "tech", "company_size": "1001-5000", "description": "x"},
        {"company": "Uber", "title": "Machine Learning Engineer", "start_date": "2018-07-01",
         "end_date": "2020-12-01", "duration_months": 29, "is_current": False,
         "industry": "tech", "company_size": "10001+", "description": "x"},
        {"company": "Swiggy", "title": "Senior Machine Learning Engineer", "start_date": "2021-01-01",
         "end_date": None, "duration_months": 65, "is_current": True,
         "industry": "tech", "company_size": "5001-10000", "description": "x"},
    ]
    erratic_history = [
        {"company": "A", "title": "Machine Learning Engineer", "start_date": "2017-01-01",
         "end_date": "2018-01-01", "duration_months": 12, "is_current": False,
         "industry": "tech", "company_size": "51-200", "description": "x"},
        {"company": "B", "title": "Frontend Developer", "start_date": "2018-02-01",
         "end_date": "2020-01-01", "duration_months": 23, "is_current": False,
         "industry": "tech", "company_size": "51-200", "description": "x"},
        {"company": "C", "title": "AI Consultant", "start_date": "2020-02-01",
         "end_date": None, "duration_months": 64, "is_current": True,
         "industry": "tech", "company_size": "51-200", "description": "x"},
    ]
    candidate_progression = {"career_history": history}
    candidate_erratic = {"career_history": erratic_history}

    score_progression, debug_progression = feat.career_trajectory_score(candidate_progression)
    score_erratic, debug_erratic = feat.career_trajectory_score(candidate_erratic)
    assert score_progression > score_erratic
    assert debug_progression["seniority_non_decreasing"] is True


def test_career_trajectory_neutral_with_insufficient_history():
    score, debug = feat.career_trajectory_score({"career_history": []})
    assert score == 0.5
    assert debug["reason"] == "insufficient_history_for_trajectory"


def test_notice_period_fit_curve():
    assert feat.notice_period_fit(15) == 1.0
    assert feat.notice_period_fit(45) == 0.7
    assert feat.notice_period_fit(75) == 0.5
    assert feat.notice_period_fit(120) == jd.NOTICE_PERIOD_FLOOR
    assert feat.notice_period_fit(None) == jd.NOTICE_PERIOD_UNKNOWN


def test_tech_lead_no_code_flagged_via_title_even_without_phrase():
    """JD: 'hasn't written production code in the last 18 months because
    you've moved into architecture or tech lead roles.' Should fire on a
    plain management title + long current tenure, not just on the literal
    phrase list."""
    candidate = {
        "profile": {
            "current_title": "Engineering Manager",
            "current_company": "Foo",
            "years_of_experience": 9,
        },
        "career_history": [
            {"company": "Foo", "title": "Engineering Manager", "start_date": "2023-01-01",
             "end_date": None, "duration_months": 24, "is_current": True,
             "industry": "tech", "company_size": "201-500",
             "description": "Leading a team of 8 engineers."},
        ],
        "skills": [],
        "education": [],
        "redrob_signals": {},
    }
    flags = feat.detect_disqualifiers(candidate, "off_domain")
    assert "tech_lead_no_code_18mo" in flags


def test_tech_lead_no_code_not_flagged_for_short_management_tenure():
    """Same management title, but <18mo in the role -- too soon to say they've
    stopped writing code; should not be flagged."""
    candidate = {
        "profile": {
            "current_title": "Engineering Manager",
            "current_company": "Foo",
            "years_of_experience": 9,
        },
        "career_history": [
            {"company": "Foo", "title": "Engineering Manager", "start_date": "2025-01-01",
             "end_date": None, "duration_months": 6, "is_current": True,
             "industry": "tech", "company_size": "201-500",
             "description": "Just moved into management."},
        ],
        "skills": [],
        "education": [],
        "redrob_signals": {},
    }
    flags = feat.detect_disqualifiers(candidate, "off_domain")
    assert "tech_lead_no_code_18mo" not in flags


def test_evidence_coverage_counts_independent_sources():
    strong = _load("CAND_0000031")
    family_scores = feat.core_skill_family_coverage(
        strong.get("skills", []),
        strong.get("redrob_signals", {}).get("skill_assessment_scores", {}),
    )
    _, title_debug = feat.title_career_match_score(strong)
    count, sources = feat.evidence_coverage(strong, family_scores, title_debug["shipped_phrase_hits"])
    assert count >= 2  # a genuinely strong profile should corroborate across >1 section
    assert isinstance(sources, list)


def test_shipped_evidence_snippet_quotes_real_text():
    strong = _load("CAND_0000031")
    snippet = feat.shipped_evidence_snippet(strong)
    assert snippet is not None
    # the snippet must be traceable to this candidate's own career_history text,
    # not a fixed sentence -- check it's not the old canned phrase verbatim.
    assert "matching the JD's core ask" not in snippet


def test_reasoning_is_not_templated_across_distinct_candidates():
    """Direct regression test for the bug found in the real submission: the
    fixed reasoning generator must not fall back to the old fixed clause
    verbatim. The 50-candidate bundled sample only has one strong_ml
    candidate, so this checks that one rigorously; the full-pool variation
    check (94/100 identical rows before the fix) was verified separately
    against the real 100K candidates.jsonl and is documented in ARCHITECTURE.md."""
    with open(DATA) as f:
        candidates = json.load(f)
    jd_text = (Path(__file__).parent / "data" / "job_description.md").read_text()
    jd_tokens = feat._tokenize(jd_text)

    strong_ml_candidates = []
    for c in candidates:
        r = scorer.score_candidate(c, jd_tokens)
        if r["title_class"] == "strong_ml":
            strong_ml_candidates.append((c, r))

    assert strong_ml_candidates, "need at least 1 strong_ml candidate in the sample to test this"

    canned_old_clause = "career history shows shipped ranking/retrieval work, matching the JD's core ask"
    reasonings = [scorer.build_reasoning(c, r) for c, r in strong_ml_candidates]
    assert not any(canned_old_clause in r for r in reasonings), (
        "reasoning still contains the old canned clause verbatim"
    )
    if len(reasonings) >= 2:
        assert reasonings[0] != reasonings[1]


if __name__ == "__main__":
    import sys
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
