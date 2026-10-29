# AI disclosure

## 1. AI used *in the product*

* Text from OCR / PDFs / spreadsheets is sent to a Groq-hosted LLM (`GROQ_MODEL`, default `openai/gpt-oss-20b`)
  to extract invoice fields. This means **document contents leave the server** - only use documents you
  are authorised to process with that provider.
* Model output can be wrong. The system mitigates this with a strict "use null, never invent" prompt,
  per-field confidence, deterministic validation rules and a human review/correction workflow, but these are
  aids, not guarantees. Review extracted data before relying on it for financial decisions.
* Embeddings for semantic search are computed locally (`sentence-transformers/all-MiniLM-L6-v2`).

## 2. AI-assisted development (required by the assessment brief)

> **EDIT THIS SECTION so it is true for you before submitting.** The statements below are a template.

* AI tools used: **[list each tool, e.g. Claude, ChatGPT, GitHub Copilot]**.
* Used for: **[e.g. initial scaffolding, code review, writing tests, fixing validation rules, drafting docs]**.
* Claude (Anthropic) was used to review this project against the assessment brief and to help write the
  validation rules, search UI, adaptive-OCR change, tests, evaluation harness and documentation.
* What I did myself: **[e.g. chose the architecture, ran the evaluation, verified results, recorded the walkthrough]**.
* All AI-generated code was run and tested (`python -m pytest`); the evaluation metrics in
  `evaluation/REPORT.md` come from an actual run of `evaluation/run_eval.py`, not from an AI's estimate.
* The prompts used during development are listed in `PROMPTS.md`.
