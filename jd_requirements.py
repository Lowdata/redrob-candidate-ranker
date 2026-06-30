"""
Structured representation of the Redrob "Senior AI Engineer — Founding Team" JD.

This file exists so the scoring rubric is a reviewable, editable spec rather than
logic buried inline in rank.py. Every constant here traces back to a specific
sentence in job_description.md — see the comment above each block.

v2 changes (post-feedback-review):
  - CORE_SKILL_FAMILIES is now derived from SKILL_TAXONOMY instead of being
    hand-duplicated, so there's one source of truth for "what counts as
    embeddings_retrieval / vector_db_hybrid_search / eval_frameworks / python".
    The flattened leaves are IDENTICAL to the original family lists -- this is
    a refactor, not a rubric change. The taxonomy's nesting is new and is used
    by features.skill_domain_depth_bonus() to reward candidates with credible
    evidence ACROSS a domain (retrieval + vector-db + eval), which is much
    harder for a keyword-stuffer to fake than matching one substring.
  - Added MANAGEMENT_TRACK_TITLE_KEYWORDS, used to broaden the JD's explicit
    "hasn't written production code in 18 months" disqualifier beyond just
    phrase-matching career_history text (see features.detect_disqualifiers).
  - WEIGHTS gained a `career_trajectory` component (see features.py) and
    `semantic_similarity` / `location_fit` were trimmed slightly to make room
    for it. Both were already documented as deliberately minor signals.
"""

# ---------------------------------------------------------------------------
# Experience band
# ---------------------------------------------------------------------------
# JD: "Experience Required: 5-9 years ... This is a range, not a requirement."
EXPERIENCE_BAND = (5, 9)
EXPERIENCE_SOFT_MARGIN = 2.5  # years outside the band before the penalty saturates

# ---------------------------------------------------------------------------
# Core required skills (JD: "Things you absolutely need")
# Organized as a two-level taxonomy: domain -> leaf-family -> matching substrings.
# A candidate gets credit for a leaf family if ANY listed skill matches it.
# The domain grouping (currently just "search_engineering", which has 3 leaves)
# is what lets features.skill_domain_depth_bonus() distinguish "claims one
# retrieval buzzword" from "shows credible evidence across retrieval, vector
# infra, AND eval" -- the JD explicitly asks for breadth ("embeddings,
# retrieval, ranking, LLMs" together), not one keyword.
# ---------------------------------------------------------------------------
SKILL_TAXONOMY = {
    "search_engineering": {
        "embeddings_retrieval": [
            "embedding", "sentence-transformer", "sentence transformer",
            "bge", "e5", "retrieval", "semantic search",
        ],
        "vector_db_hybrid_search": [
            "pinecone", "weaviate", "qdrant", "milvus", "opensearch",
            "elasticsearch", "faiss", "vector database", "hybrid search",
        ],
        "eval_frameworks": [
            "ndcg", "mrr", "map", "a/b test", "evaluation framework",
            "offline-online correlation", "offline to online",
        ],
    },
    "core_engineering": {
        "python": [
            "python",
        ],
    },
}

# Flattened view, kept for every consumer that scores a single family at a
# time (skill_credibility, build_reasoning's strong/weak family lists, the
# existing test suite). Identical keys/values to the pre-taxonomy version.
CORE_SKILL_FAMILIES = {
    leaf: needles
    for domain in SKILL_TAXONOMY.values()
    for leaf, needles in domain.items()
}

# JD: "Things we'd like you to have but won't reject you for" — small bonus only.
NICE_TO_HAVE_SKILLS = [
    "lora", "qlora", "peft", "fine-tun",
    "learning to rank", "learning-to-rank", "xgboost", "lightgbm",
    "distributed systems", "inference optimization",
    "open source", "open-source",
]

# ---------------------------------------------------------------------------
# Title / role signals
# ---------------------------------------------------------------------------
# Titles that plausibly indicate hands-on applied ML / ranking / retrieval work.
# Used to decide whether to trust a skills list at face value.
STRONG_TITLE_KEYWORDS = [
    "machine learning", "ml engineer", "applied ml", "applied scientist",
    "recommendation", "ranking", "search engineer", "nlp engineer",
    "ai engineer", "data scientist", "research engineer",
    "information retrieval",
]

# Titles the JD explicitly does NOT want as the primary background.
EXCLUDED_DOMAIN_TITLE_KEYWORDS = [
    "computer vision", "speech", "robotics",
]

# Generic engineer titles (backend/data/frontend/devops) — these candidates may
# have *some* ML exposure but title alone doesn't confirm it; skills/career_history
# text must do the work of proving fit.
ADJACENT_TITLE_KEYWORDS = [
    "backend", "data engineer", "frontend", "full stack", "fullstack",
    "devops", "cloud engineer", "software engineer", "qa engineer",
    "mobile developer", ".net", "java developer",
]

# Titles that are almost certainly not a fit regardless of skills list
# (the keyword-stuffer trap targets exactly these).
OFF_DOMAIN_TITLE_KEYWORDS = [
    "manager", "accountant", "analyst", "support", "designer",
    "civil engineer", "mechanical engineer", "hr ", "marketing",
    "operations", "developer" # generic "Developer" alone, unless ML-qualified above
]

# JD: "If you are a senior engineer who hasn't written production code in the
# last 18 months because you've moved into 'architecture' or 'tech lead'
# roles." Used as an additional, title-based trigger for tech_lead_no_code_18mo
# alongside the existing phrase-based one -- catches candidates whose CURRENT
# role is plainly management-track even if their profile text never uses one
# of the literal TECH_LEAD_NO_CODE_PHRASES.
MANAGEMENT_TRACK_TITLE_KEYWORDS = [
    "engineering manager", "director of engineering", "engineering director",
    "vp of engineering", "vp engineering", "head of engineering",
]

# ---------------------------------------------------------------------------
# Explicit disqualifiers (JD section: "Things we explicitly do NOT want")
# Each is a (weight, description) penalty applied multiplicatively to final score.
# Weight = fraction of score KEPT (so 0.5 means "cut score in half").
# ---------------------------------------------------------------------------
DISQUALIFIER_PENALTIES = {
    "pure_research_no_production": 0.35,   # academia/research-only, no deployment
    "recent_langchain_only": 0.55,          # <12mo "AI experience" = wrapper calls only
    "tech_lead_no_code_18mo": 0.55,         # senior but hasn't coded in 18+ months
    "title_chaser": 0.7,                    # company-hop every ~1.5y chasing titles
    "framework_tutorial_only": 0.75,        # tutorial-blog/demo-only signal, no systems thinking
    "consulting_only_no_product": 0.45,     # TCS/Infosys/Wipro/etc. career, no product co.
    "excluded_domain_no_nlp": 0.4,          # CV/speech/robotics primary, no NLP/IR exposure
    "closed_source_unvalidated": 0.65,      # 5+ yrs closed-source only, zero external validation
}

CONSULTING_FIRMS = [
    "tcs", "infosys", "wipro", "accenture", "cognizant", "capgemini",
    "tech mahindra", "mindtree", "hcl",
]

# ---------------------------------------------------------------------------
# Location (JD: Pune/Noida preferred, Tier-1 India cities welcome, no visa sponsorship)
# ---------------------------------------------------------------------------
PREFERRED_LOCATIONS = ["pune", "noida"]
TIER1_INDIA_LOCATIONS = [
    "hyderabad", "mumbai", "delhi", "bangalore", "bengaluru", "chennai",
    "gurgaon", "gurugram", "noida", "pune",
]

# ---------------------------------------------------------------------------
# Notice period (JD: "We'd love sub-30-day notice. We can buy out up to 30
# days. 30+ day notice candidates are still in scope but the bar gets
# higher." -- explicitly NOT a hard cutoff, just a soft preference curve.)
# ---------------------------------------------------------------------------
NOTICE_PERIOD_BREAKPOINTS = [
    (30, 1.0),
    (60, 0.7),
    (90, 0.5),
]
NOTICE_PERIOD_FLOOR = 0.3       # 90+ days: still in scope per the JD, not zero
NOTICE_PERIOD_UNKNOWN = 0.6     # signal missing: neutral, don't punish or reward

# ---------------------------------------------------------------------------
# Component weights for the final composite score
# ---------------------------------------------------------------------------
# semantic_similarity and location_fit were trimmed (0.10->0.05, 0.05->0.03)
# to make room for career_trajectory, since both were already documented as
# deliberately minor/tie-breaker-only signals and neither review nor a
# re-read of the JD argued for increasing them.
WEIGHTS = {
    "title_career_match": 0.30,    # the JD's actual ask: does career history show real shipped work
    "skill_credibility": 0.20,     # presence + duration + endorsements + assessment_score, not raw count
    "experience_fit": 0.12,
    "semantic_similarity": 0.05,   # JD-text vs profile-text, minor tie-breaker signal only
    "behavioral_availability": 0.13,
    "location_fit": 0.03,
    "consistency_penalty_weight": 0.10,  # honeypot / internal-inconsistency check
    "career_trajectory": 0.07,     # NEW: domain convergence + earned seniority progression
}
