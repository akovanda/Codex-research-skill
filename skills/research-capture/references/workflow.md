# Workflow

Use this sequence for any research-shaped request.

## 1. Flush Before New Work

- If `research-registry-capture-queue` is available, run `flush` before starting new research.
- If queued items fail again, mention that explicitly in the final summary.

## 2. Search First

- Search the registry for exact terms, adjacent vocabulary, and likely source titles.
- Prefer reuse when an existing report or finding already answers the question with enough provenance.

## 3. Route By Topic

- If the topic is memory/retrieval related, delegate the domain workflow to `$research-memory-retrieval`.
- Otherwise stay in the general research-capture flow.

## 4. Store New Research

- Create a run for new research.
- Add source-anchored annotations first.
- Create findings from those annotations.
- Always create a report for the session and keep it private unless the user explicitly asks to publish.

## 5. Summarize Explicitly

- Tell the user what was reused.
- Tell the user what was stored.
- If storage failed, tell the user what was queued and that replay will happen on the next research request.
