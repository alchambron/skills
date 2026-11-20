import contextlib
from datetime import date, datetime
import io
import json
from pathlib import Path
import tempfile
import unittest

import run_dir


class RunDirTests(unittest.TestCase):
    def test_toronto_date_uses_iso_yyyy_mm_dd_across_midnight(self):
        self.assertEqual(run_dir.toronto_date(datetime.fromisoformat('2026-11-20T04:59:59+00:00')), date(2026, 11, 19))
        self.assertEqual(run_dir.toronto_date(datetime.fromisoformat('2026-11-20T05:00:00+00:00')), date(2026, 11, 20))
        with self.assertRaisesRegex(ValueError, 'timezone'):
            run_dir.toronto_date(datetime.fromisoformat('2026-11-20T05:00:00'))

    def test_prepare_run_uses_single_canonical_lock_and_unique_attempt_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out1, out2 = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out1):
                code1 = run_dir.main(['--runs-root', str(root), '--date', '2026-11-20'])
            with contextlib.redirect_stdout(out2):
                code2 = run_dir.main(['--runs-root', str(root), '--date', '2026-11-20'])
            self.assertEqual((code1, code2), (0, 0))
            info1, info2 = json.loads(out1.getvalue()), json.loads(out2.getvalue())
            self.assertEqual(info1['run_dir'], str(root.resolve() / 'update-from-work-2026-11-20'))
            self.assertEqual(info1['lock_path'], str(root.resolve() / 'update-from-work.lock'))
            self.assertNotEqual(info1['attempt_dir'], info2['attempt_dir'])
            self.assertTrue(Path(info1['attempt_dir']).is_dir())
            self.assertFalse((Path(info1['run_dir']) / 'update-from-work.lock').exists())

    def test_overlapping_invocation_exits_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with run_dir.acquire_run_lock(root):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = run_dir.main(['--runs-root', str(root), '--date', '2026-11-20'])
            self.assertEqual(code, run_dir.LOCK_BUSY)
            self.assertEqual(json.loads(out.getvalue())['status'], 'skipped')


if __name__ == '__main__':
    unittest.main()
