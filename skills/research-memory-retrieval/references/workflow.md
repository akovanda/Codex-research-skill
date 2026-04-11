# Workflow

Use this sequence unless the user explicitly wants retrieval-only behavior.

## 1. Search Existing Registry Content

- search the exact query first
- search nearby variants from the topic taxonomy
- inspect the top relevant claims or reports before adding anything new
- fetch underlying excerpts or sources when stored guidance seems close but incomplete

## 2. Decide Whether to Reuse or Extend

- reuse existing content when the registry already has fresh, well-supported guidance
- extend the registry when the stored artifacts are stale, shallow, or aimed at a different subtopic
- stop entirely when no source-backed research can be gathered

## 3. Create A Session For New Work

- keep one session per user question or tightly related batch
- carry the session id into every new excerpt, claim, and report

## 4. Add Excerpts

- anchor every excerpt to a specific source passage
- prefer `selector.exact` plus `selector.deep_link` when available
- keep the note tied to the cited passage

## 5. Create Claims

- build claims only after the excerpts exist
- keep claims narrow and falsifiable
- avoid duplicating an existing claim unless new evidence materially changes the guidance

## 6. Create A Guidance Report

- create a report when the topic is mature enough to carry forward
- treat reports as reusable guidance artifacts, not final truth
- include gaps, needs, wants, and follow-up questions
