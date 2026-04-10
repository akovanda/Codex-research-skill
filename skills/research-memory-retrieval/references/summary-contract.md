# Summary Contract

Use this output shape when the specialist skill returns a summary, whether the result was pure reuse, synthesis across stored findings, or new gap-filling research.

## Required Sections

- `## Answer`
- `## Knowledge To Reuse`
- `## Context To Carry Forward`
- `## Evidence`
- `## Registry State`

## Knowledge Goals

- The `Answer` section should state the current best conclusion in direct language.
- `Knowledge To Reuse` should preserve the actual supported claims, not just a generic recap.
- `Context To Carry Forward` should tell the next researcher what tradeoffs, metrics, or failure modes still matter.
- `Evidence` should keep source URLs visible so the summary remains grounded.
- `Registry State` should make reuse and new storage explicit by id whenever possible.

## Guardrails

- Do not replace supported claims with vague abstractions.
- Do not omit the operational context, because that is what helps future sessions ask better follow-up questions.
- Do not hide the difference between reused artifacts and newly created ones.
