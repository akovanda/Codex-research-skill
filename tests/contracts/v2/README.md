# V2 contract goldens

This directory freezes the RR2-001 Pydantic boundary without registering v2
tools or routes.

- `valid/packet/` copies every authoritative packet example.
- `valid/` fills the status, get, error, and minimal-deposit example gaps.
- `invalid/vectors.json` records the validation reason each rejected payload
  exercises. Its `repeat` directive expands a compact string fixture to a
  declared over-limit size before validation.
- `schemas/` contains deterministic `model_json_schema()` snapshots generated
  from `research_registry.contracts.v2`.

Regenerate schema files only from the checked-in models, then inspect the full
diff. Packet schemas remain the product authority; these snapshots record the
Pydantic representation used by future adapters.
