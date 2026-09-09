"""Run against the private ingest DSN without displaying credentials."""
import json
import os
import subprocess
import sys
from pathlib import Path
repo = Path(__file__).resolve().parents[4]
if not os.environ.get('TEST_INGEST_DATABASE_URL'):
    private = repo / 'knowledge-docker/.local/test-env.json'
    if not private.exists(): raise SystemExit('Set TEST_INGEST_DATABASE_URL or initialize local Docker')
    os.environ['TEST_INGEST_DATABASE_URL'] = json.loads(private.read_text())['databases']['ingest']
os.environ['PYTHONPATH'] = str(repo / 'services/python/src')
raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q', str(Path(__file__).parent), *sys.argv[1:]], cwd=repo))