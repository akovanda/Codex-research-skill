# Research Pass Suite

This suite is for judging whether the Research Registry is useful on a real long-memory project rather than on toy prompts.

The passes are derived from:

- the `dnd2` screen session history around overnight benchmark runs
- `continuity-benchmarks`
- `continuity-core`
- `choose-game`

The canonical pass list lives in [src/research_registry/research_pass_suite.py](/home/akovanda/dev/llmresearch/src/research_registry/research_pass_suite.py).

## What It Gives You

- 27 grounded research passes instead of invented demo prompts
- four waves that move from benchmark fit to retrieval mechanics to productization
- expected specialist-domain routing for each prompt
- a simple way to check whether implicit capture will actually classify these prompts the way you expect

## Commands

Summary:

```bash
. .venv/bin/activate
research-registry-pass-suite
```

Check routing:

```bash
. .venv/bin/activate
research-registry-pass-suite --check-routing
```

Render the full pass suite as markdown:

```bash
. .venv/bin/activate
research-registry-pass-suite --format markdown
```

Render only one wave:

```bash
. .venv/bin/activate
research-registry-pass-suite --wave 1 --format markdown
```

Run the full suite against a seeded local registry and write reports:

```bash
. .venv/bin/activate
research-registry-pass-runner --db-path /tmp/research-pass-runner.sqlite3 --reset --rounds 2 --json-out /tmp/research-pass-runner.json --markdown-out /tmp/research-pass-runner.md
```

## How To Use It

Run the passes in wave order.

- Wave 1 tells you whether your benchmark story is coherent.
- Wave 2 tells you whether the retrieval and memory mechanics are grounded.
- Wave 3 tells you whether context assembly and typed-memory decisions are defensible.
- Wave 4 tells you whether the system can survive public-tool constraints like latency, validation, and API design.

For evaluation, the first pass on a topic should usually be a gap-fill capture. Follow-up passes on the same topic should start turning into reuse or synthesis. If that does not happen, the registry is storing text without buying you working memory.

## What The Runner Measures

- round 1: whether a prompt reuses the seeded registry or needs a first-pass gap fill
- round 2: whether the same prompt now shifts toward reuse or synthesis
- routing correctness for the current implicit classifier
- summary-contract pass rate for every execution

This is not live web research. It is a local execution harness for the current registry and specialist-domain behavior.
