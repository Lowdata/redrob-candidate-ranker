import subprocess
import time
import csv
import sys
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).parent
FULL_PATH = DATA_DIR / "candidates.jsonl"
JD_PATH = DATA_DIR / "job_description.md"
SUB_PATH = DATA_DIR / "submission.csv"

def run_audit():
    print("Running rank.py to gather metrics...")
    start_time = time.time()
    
    # Run rank.py. Note: For accurate peak memory on macOS we use /usr/bin/time -l
    cmd = [
        "/usr/bin/time", "-l", "python3", "rank.py",
        "--candidates", str(FULL_PATH),
        "--jd", str(JD_PATH),
        "--out", str(SUB_PATH)
    ]
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print("Error running rank.py:")
        print(e.stderr)
        sys.exit(1)
        
    runtime = time.time() - start_time
    
    # Parse Peak Memory
    peak_mem_mb = 0.0
    for line in res.stderr.splitlines():
        if "maximum resident set size" in line:
            bytes_val = int(line.strip().split()[0])
            peak_mem_mb = bytes_val / (1024 * 1024)
            break

    print("Gathering reasoning text for diversity metrics...")
    reasonings = []
    with open(SUB_PATH, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "reasoning" in row:
                reasonings.append(row["reasoning"])
            elif "Reasoning" in row:
                reasonings.append(row["Reasoning"])
                
    if not reasonings:
        print("Could not find reasoning column in submission.csv")
        sys.exit(1)
        
    unique_texts = set(reasonings)
    
    def get_phrases(text, n):
        words = text.split()
        return [" ".join(words[i:i+n]) for i in range(max(0, len(words)-n+1))]

    all_phrases = []
    for r in reasonings:
        all_phrases.extend(get_phrases(r.lower(), 4))
        
    # Exclude phrases that are just domain tags from the profile to find true structural repetition
    filtered_phrases = [
        p for p in all_phrases 
        if "embeddings" not in p and "retrieval" not in p and "vector db" not in p
    ]
            
    if filtered_phrases:
        most_common_phrase = Counter(filtered_phrases).most_common(1)[0]
    else:
        most_common_phrase = ("None", 0)
        
    print("Verifying determinism and hallucinations via test suite...")
    test_res = subprocess.run(["python3", "tests_reasoning.py"], capture_output=True, text=True)
    if test_res.returncode == 0:
        det_status = "PASS"
        hallucination_count = 0
        validation_status = "PASS"
    else:
        det_status = "FAIL"
        hallucination_count = "Unknown (Test Failed)"
        validation_status = "FAIL"
        print(test_res.stdout)
        print(test_res.stderr)

    print("\n" + "="*40)
    print("          FINAL REASONING AUDIT")
    print("="*40)
    print(f"Runtime:               {runtime:.2f} s")
    print(f"Peak Memory:           {peak_mem_mb:.2f} MB")
    print(f"Template Diversity:    {len(unique_texts)} unique reasonings / {len(reasonings)}")
    print(f"Phrase Repetition:     Largest non-domain phrase '{most_common_phrase[0]}' appeared {most_common_phrase[1]} times")
    print(f"Hallucination Count:   {hallucination_count}")
    print(f"Determinism:           {det_status}")
    print(f"Validation Status:     {validation_status}")
    print("="*40 + "\n")

if __name__ == "__main__":
    run_audit()
