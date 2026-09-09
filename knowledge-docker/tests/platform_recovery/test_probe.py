import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).parent))


class JournalCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('recovery_probe',Path(__file__).with_name('probe.py'))
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)

    def test_total_partial_is_not_enough_exact_readable_and_denied_revisions_are_required(self):
        m=self.module
        good=[{'revision_id':'positive','outcome':'projected'},{'revision_id':'negative','outcome':'not_visible'}]
        self.assertTrue(m.validate_journal(good,{'positive'},{'negative'})['positive_complete'])
        for rows in (good[:1],good[1:],[dict(r,outcome='projected') for r in good],good+good[:1]):
            with self.subTest(rows=rows), self.assertRaises(m.RecoveryError):
                m.validate_journal(rows,{'positive'},{'negative'})
        with self.assertRaises(m.RecoveryError):m.validate_journal(good,set(),{'negative'})