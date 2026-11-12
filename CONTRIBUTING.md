# Contributing

Install with `uv sync --extra dev`, then run `uv run pytest -q` and
`uv run ruff check src tests`. Keep real FFmpeg integration coverage for changes
to timeline composition. Avoid tests that merely repeat implementation details.

Keep all project mutations revision-checked. Register new functionality as an
MCP tool only when the operation actually works; unavailable dependencies should
produce capability information and clear errors. Use the shared service layer
so the studio and AI clients do not diverge.

Format Python with `uv run ruff format src tests`. The frontend is plain HTML,
CSS, and JavaScript. Verify desktop and narrow layouts and keyboard operation.

For a proposed feature, describe the user's workflow, the exact media inputs and
outputs, resource needs, and any new external service or model dependency.
