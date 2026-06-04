.PHONY: test sample-report ops-run daily-ops

test:
	PYTHONPATH=src python3 -m unittest discover -s tests

sample-report:
	PYTHONPATH=src python3 -m tw_day_trading_lab.cli candidates build --date 2026-05-28 --input examples/candidates.sample.json --output reports/2026-05-28-candidates.json
	PYTHONPATH=src python3 -m tw_day_trading_lab.cli report daily --date 2026-05-28 --input reports/2026-05-28-candidates.json --format md --output reports/2026-05-28-daily.md

ops-run:
	PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate ops-run $(ARGS)

daily-ops:
	PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate daily-ops $(if $(DATE),--date $(DATE),) $(ARGS)
