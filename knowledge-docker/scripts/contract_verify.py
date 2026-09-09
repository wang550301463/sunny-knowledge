"""Run contract drift, serializer/transport tests, and three-language client checks."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--go', default='go', help='Go executable (CI pins 1.24.7)')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]

    def run(command, cwd=root):
        subprocess.run([str(item) for item in command], cwd=cwd, check=True)

    run([sys.executable, root/'knowledge-docker/scripts/contract_generate.py', '--check'])
    run([sys.executable, '-m', 'pytest', root/'knowledge-docker/tests/contracts', '-q'])
    run([args.go, 'test', '-race', './internal/generatedcontracts/...', '-count=1'], root/'services/go')
    with tempfile.TemporaryDirectory(prefix='knowledge-contract-typescript-') as directory:
        run([root/'web/node_modules/.bin/tsc', '-p', root/'contracts/tsconfig.json', '--noEmit', 'false', '--outDir', directory])
        (Path(directory)/'package.json').write_text(json.dumps({'type':'module'}))
        run(['node',root/'knowledge-docker/tests/contracts/client_typescript.mjs',directory])
    print('Contract drift and Python/Go/TypeScript boundary checks passed')


if __name__ == '__main__':
    main()