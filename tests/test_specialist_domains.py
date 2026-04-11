from __future__ import annotations

from research_registry.research_capture import specialized_domain_for_prompt


def test_specialist_domain_routing_covers_memory_inference_and_evals() -> None:
    assert specialized_domain_for_prompt("Research long-term memory retrieval provenance.") == "memory-retrieval"
    assert specialized_domain_for_prompt("Research inference latency throughput tradeoffs.") == "inference-optimization"
    assert specialized_domain_for_prompt("Research judge model calibration and benchmark drift.") == "llm-evals"
