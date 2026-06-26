.PHONY: api test test-all clean

api:
	python -m uvicorn gateway.main:app --app-dir apps/api/src --host 0.0.0.0 --port 8010

start-api:
	python scripts/start_api.py

web:
	python -m http.server 5173 -d apps/web

db:
	python scripts/inspect_db.py

ssh-check:
	python scripts/check_remote_codex.py

test:
	python -m unittest discover -s apps/api/tests -p "test_*.py"

test-all:
	python scripts/run_all_tests.py

clean:
	python scripts/clean_old_workspaces.py
