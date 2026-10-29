# Prompts

## 1. Production extraction prompt

Maintained as `SYSTEM_PROMPT` in `llm.py` (version in `PROMPT_VERSION`, currently `v1.2`). It asks the model to:

* return **JSON only**;
* extract: invoice number, invoice date, supplier, customer, subtotal, tax, total, currency, line items
  (description, quantity, unit price, amount);
* **use `null` rather than inventing** absent data;
* give a 0-1 confidence per field;
* use ISO dates (`YYYY-MM-DD`) and ISO currency codes where identifiable;
* answer `notes: "Not an invoice"` with all-null fields for non-invoices.

The response is parsed as JSON and checked against a Pydantic schema (`InvoiceSchema`); results are cached by
SHA-256 of `PROMPT_VERSION + text`. Any prompt change must bump `PROMPT_VERSION` and be re-evaluated with
`python evaluation/run_eval.py`.

## 2. Development prompts (AI-assisted development log)

> **Paste your real development prompts here** (the actual messages you sent to your AI tools), most
> important first. Example format:
>
> 1. "Design a FastAPI app that accepts PDF/PNG/XLSX/CSV invoices, OCRs them and extracts ..."
> 2. "Review my project against this assessment brief and tell me what is missing ..."
> 3. "Add subtotal-vs-line-items validation and a date check, with tests ..."
