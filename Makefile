.PHONY: smoke

smoke:
	@echo "Running Phase 1 end-to-end smoke test..."
	@processor/.venv/bin/python scripts/smoke_test.py
	@echo "Running Phase 2 end-to-end smoke test..."
	@processor/.venv/bin/python scripts/smoke_test_phase2.py
	@echo "Smoke tests complete."
