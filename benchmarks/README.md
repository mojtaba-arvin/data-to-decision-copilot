# Evaluation and benchmark

Run the deterministic semantic benchmark with:

```bash
uv run pytest tests/test_benchmark.py -q
uv run pytest --cov=d2d --cov-report=term-missing
```

The release-branch baseline compares exact typed QueryPlans for the supported
English count, aggregation/grouping, and projection forms, and verifies fail-closed
rejection of unsupported SQL-like input. Persian/English plan equivalence is covered
by the dedicated Phase 14 semantic-planner regression tests. Its accuracy is a regression metric for this
small supported grammar; it is not a measure of general NL-to-SQL capability.

Runtime resource behavior is covered separately by bounded row/byte, timeout,
cancellation, and SQL-safety tests. Model inference is not run in ordinary CI;
local Ollama smoke and model-quality evaluation must be recorded separately with
the model tag, hardware, prompt version, dataset version, and raw outputs.


The Phase 14 PR adds a separate bilingual regression suite for equivalent Persian and English requests; keep that suite in the full integration gate.
