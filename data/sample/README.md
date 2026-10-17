# Sample data — SYNTHETIC

Everything in this folder is **synthetic demonstration data** generated
deterministically by `backend/app/demo/sample_data.py`
(`python scripts/generate_sample_data.py`). `manifest.json` lists every file,
its checksum and — for `retail_transactions_v2.csv` — each intentionally
injected issue (ISS-01 … ISS-09) with the detection expected from DataSentinel.
