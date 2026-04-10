# Workflow

Use this sequence unless the user explicitly asks for retrieval-only behavior.

## 1. Search Existing Registry Content

- Search the exact query first.
- Search at least two nearby variants from the topic taxonomy.
- Inspect the top relevant findings or reports before deciding to add new content.
- Fetch underlying annotations or sources when the existing artifacts seem close but incomplete.

## 2. Decide Whether to Reuse or Extend

- Reuse existing content when the registry already has a well-supported answer.
- Extend the registry when the existing artifacts are stale, missing anchors, too shallow, or aimed at a different subtopic.
- Stop entirely when no source-backed research can be gathered.

## 3. Create a Run for New Work

- Create a run before the first new annotation.
- Keep one run per user question or tightly related batch of questions.
- Carry the run id forward into every new annotation, finding, and report.

## 4. Add Annotations

- Anchor every annotation to a specific source passage.
- Prefer `selector.exact` plus `selector.deep_link` whenever available.
- Keep the note tied to the cited passage. Do not hide synthesis inside the annotation note.
- Use tags that help future searchers: topic, failure mode, metric, or method.

## 5. Create Findings

- Build findings only after the annotations exist.
- Prefer at least two annotations for broad claims.
- Keep the title short and the claim falsifiable.
- Avoid duplicating an existing finding unless the new evidence materially changes the conclusion.

## 6. Compile a Report When Mature

- Compile a report only when the question needs synthesis across findings or when the user explicitly wants a report.
- Leave work at the finding level when the topic is exploratory or when evidence is still thin.
- Treat reports as derived artifacts, not the canonical storage unit.

## 7. Publish Only on Request

- Default to private artifacts.
- Do not publish or mark human-reviewed unless the user asks for that workflow.
