.PHONY: test validate rank audit clean

test:
	pytest

validate:
	python validate_submission.py submission.csv

rank:
	python rank.py --candidates ./candidates.jsonl --jd ./job_description.md --out ./submission.csv

audit:
	python run_audit.py

clean:
	rm -f submission.csv sample_submission.csv debug_report.csv
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
