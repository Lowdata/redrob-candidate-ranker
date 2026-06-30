import gradio as gr
import subprocess
import os
import shutil

def rank_candidates(candidates_file, jd_file):
    if candidates_file is None or jd_file is None:
        return "Please upload both candidates and JD files."
        
    out_file = "submission.csv"
    
    # Ensure previous output is removed
    if os.path.exists(out_file):
        os.remove(out_file)
        
    cmd = [
        "python", "rank.py",
        "--candidates", candidates_file.name,
        "--jd", jd_file.name,
        "--out", out_file
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return out_file
    except subprocess.CalledProcessError as e:
        error_file = "error.txt"
        with open(error_file, "w") as f:
            f.write(e.stderr)
        return error_file

with gr.Blocks(title="Redrob Candidate Ranker - Team Apex") as demo:
    gr.Markdown("# Redrob Candidate Ranker (Team Apex)")
    gr.Markdown("Upload the `sample_candidates.json` and a job description to generate `submission.csv`.")
    
    with gr.Row():
        candidates_input = gr.File(label="Upload Candidates (JSON/JSONL)")
        jd_input = gr.File(label="Upload Job Description (MD/TXT)")
        
    rank_btn = gr.Button("Generate Ranking")
    
    output_file = gr.File(label="Download submission.csv")
    
    rank_btn.click(
        fn=rank_candidates,
        inputs=[candidates_input, jd_input],
        outputs=output_file
    )

if __name__ == "__main__":
    demo.launch()
