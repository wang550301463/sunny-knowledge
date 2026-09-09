import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'init.py'

class InitTests(unittest.TestCase):
    def test_creates_isolated_credentials_once_without_printing_them(self):
        with tempfile.TemporaryDirectory() as target:
            result = subprocess.run(['python3', str(SCRIPT), '--directory', target], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            root = Path(target)
            before = (root / '.env').read_bytes()
            self.assertEqual(stat.S_IMODE((root / '.env').stat().st_mode), 0o600)
            self.assertNotIn(before.decode().split('POSTGRES_PASSWORD=')[1].splitlines()[0], result.stdout)
            keys = json.loads((root / '.local' / 'service-public-keys.json').read_text())
            self.assertEqual(set(keys), {'gateway','iam','auth','channel','knowledge','ingest','retrieval','llm','graphiti','agent','mcp'})
            self.assertEqual(len(set(keys.values())), len(keys))
            rerun = subprocess.run(['python3', str(SCRIPT), '--directory', target], capture_output=True, text=True)
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            self.assertEqual((root / '.env').read_bytes(), before)
            realm = json.loads((root / '.local' / 'knowledge-realm.json').read_text())
            web = next(c for c in realm['clients'] if c['clientId'] == 'knowledge-web')
            self.assertEqual(web['attributes']['pkce.code.challenge.method'], 'S256')
            self.assertFalse(web['directAccessGrantsEnabled'])
            sql = (root / '.local' / 'init-databases.sql').read_text()
            self.assertIn('REVOKE CONNECT', sql)
            self.assertIn('knowledge_knowledge', sql)

if __name__ == '__main__': unittest.main()