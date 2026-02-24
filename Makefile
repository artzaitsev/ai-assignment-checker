test:
	uv run pytest

typecheck:
	uv run basedpyright

test-unit:
	uv run pytest -m unit

test-integration:
	uv run pytest -m integration

smoke-local:
	bash scripts/smoke_local_uv.sh
