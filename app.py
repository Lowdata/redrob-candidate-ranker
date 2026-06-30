import gradio as gr
import subprocess
import os

def rank_candidates(candidates_file, jd_file):
    if candidates_file is None or jd_file is None:
        return "Please upload both candidates and JD files.", None
        
    cand_name = candidates_file.name.lower()
    jd_name = jd_file.name.lower()

    if not (cand_name.endswith('.json') or cand_name.endswith('.jsonl') or cand_name.endswith('.jsonl.gz')):
        return "Unsupported candidates file type.\nPlease upload:\n• .json\n• .jsonl\n• .jsonl.gz", None

    if not (jd_name.endswith('.md') or jd_name.endswith('.txt') or jd_name.endswith('.docx')):
        return "Unsupported JD file type.\nPlease upload:\n• .md\n• .txt\n• .docx", None
        
    out_file = "submission.csv"
    
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
        return "Job description loaded successfully.\nRanking completed successfully.", out_file
    except subprocess.CalledProcessError as e:
        error_file = "error.txt"
        with open(error_file, "w") as f:
            f.write(e.stderr)
        return f"Error occurred during ranking:\n{e.stderr}", error_file

with gr.Blocks(title="Redrob Candidate Ranker - Team Apex") as demo:
    gr.Markdown("# Redrob Candidate Ranker (Team Apex)")
    gr.Markdown("Upload the candidate data and job description to generate `submission.csv`.")
    
    with gr.Row():
        candidates_input = gr.File(
            label="Upload Candidates (.json, .jsonl, .jsonl.gz)",
            file_types=[".json", ".jsonl", ".gz"]
        )
        jd_input = gr.File(
            label="Upload Job Description (.md, .txt, .docx)",
            file_types=[".md", ".txt", ".docx"]
        )
        
    rank_btn = gr.Button("Generate Ranking")
    
    with gr.Row():
        status_output = gr.Textbox(label="Status", lines=3)
        output_file = gr.File(label="Download Output")
    
    rank_btn.click(
        fn=rank_candidates,
        inputs=[candidates_input, jd_input],
        outputs=[status_output, output_file]
    )

if __name__ == "__main__":
    demo.launch()
