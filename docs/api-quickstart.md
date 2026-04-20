# API Quickstart

This is the shortest end-to-end API flow for the localhost preview.

Prerequisites:

- run `make up`
- keep the app at `http://127.0.0.1:8010`
- copy the admin token from `make token`
- these examples assume `jq` is available for shell variable extraction

## 1. Health

```bash
export BASE_URL="http://127.0.0.1:8010"
export ADMIN_TOKEN="<paste from make token>"

curl "$BASE_URL/healthz"
curl "$BASE_URL/readyz"
curl "$BASE_URL/openapi.json" | jq '.info'
```

## 2. Bootstrap an org and issue an API key

```bash
curl -sS -X POST \
  -H "x-admin-token: $ADMIN_TOKEN" \
  -H "content-type: application/json" \
  "$BASE_URL/api/admin/organizations" \
  -d '{"org_id":"acme","display_name":"Acme"}'
```

```bash
export API_KEY="$(
  curl -sS -X POST \
    -H "x-admin-token: $ADMIN_TOKEN" \
    -H "content-type: application/json" \
    "$BASE_URL/api/admin/api-keys" \
    -d '{"label":"acme-writer","actor_user_id":"owner","actor_org_id":"acme","namespace_kind":"org","namespace_id":"acme","scopes":["ingest","publish","read_private"]}' \
  | jq -r '.token'
)"
```

## 3. Create a question and research session

```bash
export QUESTION_ID="$(
  curl -sS -X POST \
    -H "x-api-key: $API_KEY" \
    -H "content-type: application/json" \
    "$BASE_URL/api/questions" \
    -d '{"prompt":"How should LLM long-term memory retrieval be structured?","focus":{"domain":"memory-retrieval","object":"llm long-term memory structure","context":"api-quickstart"},"namespace_kind":"org","namespace_id":"acme"}' \
  | jq -r '.id'
)"
```

```bash
export SESSION_ID="$(
  curl -sS -X POST \
    -H "x-api-key: $API_KEY" \
    -H "content-type: application/json" \
    "$BASE_URL/api/sessions" \
    -d "{\"question_id\":\"$QUESTION_ID\",\"prompt\":\"How should LLM long-term memory retrieval be structured?\",\"model_name\":\"gpt-5.4\",\"model_version\":\"2026-04-10\",\"mode\":\"live_research\",\"namespace_kind\":\"org\",\"namespace_id\":\"acme\",\"source_signals\":[\"api-quickstart\"]}" \
  | jq -r '.id'
)"
```

## 4. Create a source, excerpt, claim, and report

```bash
export SOURCE_ID="$(
  curl -sS -X POST \
    -H "x-api-key: $API_KEY" \
    -H "content-type: application/json" \
    "$BASE_URL/api/sources" \
    -d '{"locator":"https://example.com/memory-structure","title":"Memory structure note","snippet":"Typed memories plus reranking improve recall quality.","snapshot_present":true,"namespace_kind":"org","namespace_id":"acme"}' \
  | jq -r '.id'
)"
```

```bash
export EXCERPT_ID="$(
  curl -sS -X POST \
    -H "x-api-key: $API_KEY" \
    -H "content-type: application/json" \
    "$BASE_URL/api/excerpts" \
    -d "{\"source_id\":\"$SOURCE_ID\",\"question_id\":\"$QUESTION_ID\",\"session_id\":\"$SESSION_ID\",\"focal_label\":\"llm long-term memory structure\",\"note\":\"Use typed evidence with deep links and reranking.\",\"selector\":{\"exact\":\"Typed memories plus reranking improve recall quality.\",\"deep_link\":\"https://example.com/memory-structure#typed-memories\"},\"quote_text\":\"Typed memories plus reranking improve recall quality.\",\"namespace_kind\":\"org\",\"namespace_id\":\"acme\"}" \
  | jq -r '.id'
)"
```

```bash
export CLAIM_ID="$(
  curl -sS -X POST \
    -H "x-api-key: $API_KEY" \
    -H "content-type: application/json" \
    "$BASE_URL/api/claims" \
    -d "{\"question_id\":\"$QUESTION_ID\",\"session_id\":\"$SESSION_ID\",\"title\":\"Typed memory records improve retrieval structure\",\"focal_label\":\"llm long-term memory structure\",\"statement\":\"Typed memory records plus reranking make long-term retrieval easier to reason about and improve.\",\"excerpt_ids\":[\"$EXCERPT_ID\"],\"namespace_kind\":\"org\",\"namespace_id\":\"acme\"}" \
  | jq -r '.id'
)"
```

```bash
export REPORT_ID="$(
  curl -sS -X POST \
    -H "x-api-key: $API_KEY" \
    -H "content-type: application/json" \
    "$BASE_URL/api/reports" \
    -d "{\"question_id\":\"$QUESTION_ID\",\"session_id\":\"$SESSION_ID\",\"title\":\"LLM long-term memory structure guidance\",\"focal_label\":\"llm long-term memory structure\",\"summary_md\":\"# Guidance\\n\\nStart with typed records, provenance-rich excerpts, and reranking before adding more storage complexity.\",\"claim_ids\":[\"$CLAIM_ID\"],\"namespace_kind\":\"org\",\"namespace_id\":\"acme\"}" \
  | jq -r '.id'
)"
```

## 5. Publish and search

```bash
curl -sS -X POST \
  -H "x-api-key: $API_KEY" \
  -H "content-type: application/json" \
  "$BASE_URL/api/publish" \
  -d "{\"kind\":\"report\",\"record_id\":\"$REPORT_ID\",\"include_in_global_index\":true}"
```

```bash
curl -sS \
  "$BASE_URL/api/search?q=typed%20memory"
```

For private verification before publishing, query with the API key and `include_private=true`:

```bash
curl -sS \
  -H "x-api-key: $API_KEY" \
  "$BASE_URL/api/search?q=typed%20memory&include_private=true"
```

## 6. Import an external source into the question

Use BibTeX for a deterministic local example:

```bash
curl -sS -X POST \
  -H "x-api-key: $API_KEY" \
  -H "content-type: application/json" \
  "$BASE_URL/api/import/bibtex" \
  -d "{\"bibtex\":\"@article{typed_memory_2026,title={Typed memory structures},author={Doe, Alex},journal={Memory Notes},year={2026},doi={10.1000/typed-memory},abstract={Typed records, deep links, and reranking improve reusable research retrieval.}}\",\"question_id\":\"$QUESTION_ID\",\"focal_label\":\"llm long-term memory structure\",\"namespace_kind\":\"org\",\"namespace_id\":\"acme\"}"
```

## 7. Resolve a reuse-first brief before researching again

```bash
curl -sS -X POST \
  -H "x-api-key: $API_KEY" \
  -H "content-type: application/json" \
  "$BASE_URL/api/briefs/resolve" \
  -d '{"prompt":"How should LLM long-term memory retrieval be structured?","include_private":true,"limit":5}' \
  | jq '{reports: [.reports[].id], claims: [.claims[].id], follow_ups: [.suggested_follow_ups[].id]}'
```

## 8. Refresh a report when your local source roots have newer evidence

The refresh flow reruns local research for the report's question prompt. It is most useful when your repo or connected local source roots contain relevant material.

```bash
curl -sS -X POST \
  -H "x-api-key: $API_KEY" \
  "$BASE_URL/api/reports/$REPORT_ID/refresh" \
  | jq '{id, refresh_of_report_id, claim_ids}'
```

If refresh creates follow-up questions, you can mark one complete:

```bash
export FOLLOW_UP_ID="<from /api/briefs/resolve or the report detail page>"

curl -sS -X POST \
  -H "x-api-key: $API_KEY" \
  -H "content-type: application/json" \
  "$BASE_URL/api/follow-ups/$FOLLOW_UP_ID/status" \
  -d '{"follow_up_status":"done"}'
```
