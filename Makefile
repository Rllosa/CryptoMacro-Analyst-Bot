.PHONY: smoke

smoke:
	@echo "Running Phase 1 end-to-end smoke test..."
	@processor/.venv/bin/python scripts/smoke_test.py
	@echo "Smoke test complete."
