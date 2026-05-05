# Zeus tests

Smoke tests for the Zeus-specific layers (memory plugin, stack glue, content-pipeline lib). The vendored Hermes core has its own test suite under `core/tests/` and is not duplicated here.

## Run

```bash
pip install -e ".[dev]"
pytest
```

## What's covered

| Test file | Covers |
|---|---|
| `test_stack.py` | `stack/hermes_stack.py` Redis + pgvector connection logic |
| `test_content_types.py` | `skills/.../lib/content_types.py` — ContentPiece, platform mappings |

## What's NOT covered (and why)

- **fal.ai / fish.audio / Notion / Publer integrations** — these need real API keys and live external services. Run the pipeline end-to-end with `pipeline_test.py` instead.
- **Hermes core** — see `core/tests/`.
