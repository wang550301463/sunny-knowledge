"""Run knowledge tests against PostgreSQL without printing private DSNs."""

import json
import os
import subprocess
import sys
from pathlib import Path

repository = Path(__file__).resolve().parents[4]
if not os.environ.get('TEST_DATABASE_URL'):
    private_config = repository / 'knowledge-docker/.local/test-env.json'
    if not private_config.exists():
        raise SystemExit('Set TEST_DATABASE_URL or initialize the local Docker test environment.')
    os.environ['TEST_DATABASE_URL'] = json.loads(private_config.read_text())['databases']['knowledge']
os.environ['TEST_DATABASE_URL'] = os.environ['TEST_DATABASE_URL'].replace(
    'postgresql://', 'postgresql+psycopg://', 1
)
os.environ['PYTHONPATH'] = str(repository / 'services/python/src')
raise SystemExit(
    subprocess.call(
        [sys.executable, '-m', 'pytest', '-q', str(Path(__file__).parent), *sys.argv[1:]],
        cwd=repository,
    )
)