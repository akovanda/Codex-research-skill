# Topic Taxonomy

Use this file to keep research scoped to LLM memory and retrieval rather than drifting into generic search or broad knowledge-management work.

## Agent Memory

- long-term memory
- cross-session memory
- episodic memory
- semantic memory
- memory write policies
- memory retrieval policies
- memory decay
- memory freshness

## Retrieval Systems

- dense retrieval
- sparse retrieval
- hybrid retrieval
- reranking
- chunking
- indexing
- embedding drift
- recall and precision
- retrieval latency

## RAG and Context Assembly

- retrieval-augmented generation
- context packing
- context relevance
- document selection
- top-k selection
- source grounding
- provenance

## Failure Modes

- stale indexes
- missing provenance
- duplicate memories
- retrieval drift
- low recall
- irrelevant chunks
- hallucinated memory reuse
- outdated context

## Query Expansion Hints

- Expand by pairing the core topic with one metric term and one failure-mode term.
- For memory topics, try combinations like `agent memory recall`, `cross-session memory freshness`, or `episodic memory provenance`.
- For retrieval topics, try combinations like `reranking precision`, `dense retrieval recall`, or `stale index retrieval`.
- Search both concept words and likely source phrases before deciding the registry has a gap.
