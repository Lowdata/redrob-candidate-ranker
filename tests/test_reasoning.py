import json
import zlib
from pathlib import Path
import re
import sys
from collections import Counter

# Add parent to path if we are in tests/
sys.path.insert(0, str(Path(__file__).parent.parent))
import scorer
import features as feat
import rank as rank_module

DATA_DIR = Path(__file__).parent.parent
SAMPLE_PATH = DATA_DIR / "sample_candidates.json"
FULL_PATH_GZ = DATA_DIR / "candidates.jsonl.gz"
FULL_PATH = DATA_DIR / "candidates.jsonl"
JD_PATH = DATA_DIR / "job_description.md"

def get_candidates_stream():
    if FULL_PATH_GZ.exists():
        return rank_module.load_candidates(FULL_PATH_GZ)
    elif FULL_PATH.exists():
        return rank_module.load_candidates(FULL_PATH)
    else:
        return rank_module.load_candidates_from_json_array(SAMPLE_PATH)

def test_determinism():
    candidates = list(rank_module.load_candidates_from_json_array(SAMPLE_PATH))
    jd_tokens = rank_module.build_jd_tokens(rank_module.load_jd_text(JD_PATH))
    for c in candidates[:10]:
        r1 = scorer.score_candidate(c, jd_tokens)
        r2 = scorer.score_candidate(c, jd_tokens)
        reasoning1 = scorer.build_reasoning(c, r1)
        reasoning2 = scorer.build_reasoning(c, r2)
        assert reasoning1 == reasoning2, "Reasoning is not deterministic!"

def test_no_hallucinations():
    candidates = list(rank_module.load_candidates_from_json_array(SAMPLE_PATH))
    jd_tokens = rank_module.build_jd_tokens(rank_module.load_jd_text(JD_PATH))
    
    for c in candidates:
        r = scorer.score_candidate(c, jd_tokens)
        reasoning = scorer.build_reasoning(c, r)
        
        tech_words = ["TensorFlow", "PyTorch", "OpenAI", "React", "LangChain"]
        candidate_text = json.dumps(c).lower()
        for w in tech_words:
            if w.lower() in reasoning.lower():
                assert w.lower() in candidate_text, f"Hallucinated word: {w}"

def test_diversity_and_repetition():
    jd_tokens = rank_module.build_jd_tokens(rank_module.load_jd_text(JD_PATH))
    stream = get_candidates_stream()
    
    scored = []
    for c in stream:
        try:
            r = scorer.score_candidate(c, jd_tokens)
            scored.append((c, r))
        except Exception:
            continue
            
    top_100 = rank_module._ranked_top(scored, 100)
    
    reasonings = []
    for rank, (c, r, score) in enumerate(top_100):
        reasonings.append(scorer.build_reasoning(c, r))
        
    reasonings_text = " ".join(reasonings).lower()
    
    phrases = [
        "career history backs this up",
        "credible depth",
        "strong match",
        "matches jd"
    ]
    for p in phrases:
        count = reasonings_text.count(p)
        assert count <= 12, f"Phrase '{p}' appears too often: {count} times"
        
    words = re.findall(r'\b\w+\b', reasonings_text)
    unique_words = len(set(words))
    assert unique_words > 50, f"Lexical diversity too low, unique words: {unique_words}"

def test_regression_identical_scores():
    candidates = list(rank_module.load_candidates_from_json_array(SAMPLE_PATH))
    jd_tokens = rank_module.build_jd_tokens(rank_module.load_jd_text(JD_PATH))
    # Test CAND_0000031
    strong = [c for c in candidates if c["candidate_id"] == "CAND_0000031"][0]
    r = scorer.score_candidate(strong, jd_tokens)
    
    # Assert nothing in the fundamental structure changed 
    assert "final_score" in r
    assert "title_score" in r
    assert r["title_class"] == "strong_ml"

if __name__ == "__main__":
    test_determinism()
    test_no_hallucinations()
    test_diversity_and_repetition()
    test_regression_identical_scores()
    print("All reasoning tests passed.")
