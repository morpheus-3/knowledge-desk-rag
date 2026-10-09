# Verification · 9 October 2026

## Backend

`python -m pytest -q`: **36 passed in 3.02 seconds**.

Regression coverage adds fused OCR keywords (`VedasorSamhita`, `HinyanaBuddhism`), normal greeting behavior, and quality warnings for watermark-only extraction. Read-only checks against the uploaded library now retrieve the Vedic Age PDF first for “name vedas” and the Buddhism PDF first for “tell me about buddhism.” User PDFs and chat history were preserved. Live generation still requires the API key to be re-entered after the service restart.

Checked 20-PDF uploads, 21st-document rejection, 21-file batch rejection, duplicate detection, atomic validation, unreadable/scanned/encrypted PDFs, file size limits, PDF retrieval, correct page citations, document filtering, index invalidation on deletion, multi-document summary retrieval, empty questions, questions outside the documents, missing Groq keys, saved conversation context, permanent chat deletion, and key configuration without key disclosure.

Provider tests verify Groq JSON request formatting, normalize supported citation formats, and reject nonexistent source IDs. They cover one automatic retry for malformed JSON, truncated output, invented citations, and Groq JSON validation failures, plus distinct key, quota, context-size, and model errors. Provider calls are mocked, so no live Groq success or answer-quality claim is implied.

## Browser

`python -m tests.browser_check`: **passed all eight workflow checks** with no JavaScript errors. Desktop: 1440 × 1000. Mobile: 390 × 844; no horizontal overflow.

- Real browser upload and indexing of 20 actual PDF fixtures.
- Missing-key handling preserves input without saving partial messages.
- Source-cited response, correct page, source trail and original-PDF link.
- Unsupported question refusal.
- Saved chat reload through SQLite history.
- Mobile chat deletion with confirmation and persistence after reload.
- PDF removal updates library capacity.
- Empty state and responsive layouts.

Screenshots are in `screenshots/`. The browser test server uses a deterministic mocked Groq answer and a disposable library; it is separate from the production app.

Static JavaScript syntax and Python compilation checks passed. Runtime: Python 3.10, FastAPI 0.115.0, Scikit-learn 1.5.2, pypdf 5.0.1 and Chromium.
