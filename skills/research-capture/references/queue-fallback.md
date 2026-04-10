# Queue Fallback

Use this fallback when research capture cannot reach the registry backend.

## When To Queue

- The registry MCP tools are unavailable.
- The backend is down or write operations fail.
- The research is still source-backed and worth preserving.

## What To Queue

Queue a JSON bundle that includes:

- `queue_id`
- `prompt`
- `normalized_topic`
- `model_name`
- `model_version`
- `run`
- `annotations`
- `findings`
- `report`

## CLI Usage

- Enqueue from a JSON file:
  `research-registry-capture-queue enqueue --file bundle.json`
- Enqueue from stdin:
  `research-registry-capture-queue enqueue --stdin`
- Replay pending items:
  `research-registry-capture-queue flush`
- Inspect pending items:
  `research-registry-capture-queue list`

## Guarantees

- Queue items are private-by-default research captures.
- Replay should be attempted before the next research request begins.
- Replaying the same queued bundle should not duplicate annotations, findings, or reports.
