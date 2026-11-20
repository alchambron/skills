import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import state as ustate


class UpdateStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.state_dir = self.root / 'update-from-work'

    def cli(self, *args, expected=0):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ustate.main(['--state', str(self.state_dir), *args])
        self.assertEqual(code, expected, out.getvalue())
        return out.getvalue()

    def test_claim_shares_live_slot_and_pr_exclusivity_with_other_queues(self):
        review_state = self.root / 'review-queue' / 'state.json'
        ustate.save_json(review_state, {'jobs': {'r1': {'number': 10, 'phase': 'live', 'status': 'active'}}})
        res = json.loads(self.cli('claim', '--number', '20', '--run-dir', str(self.root / 'r'), '--thread', 't', '--host-ready', expected=2))
        self.assertIn('shared live resource cap', res['reason'])

        # Static review on another PR does not block update-from-work, but same PR does.
        ustate.save_json(review_state, {'jobs': {'r1': {'number': 10, 'phase': 'static', 'status': 'active'}}})
        res_same = json.loads(self.cli('claim', '--number', '10', '--run-dir', str(self.root / 'r'), '--thread', 't', '--host-ready', expected=2))
        self.assertIn('reserved by review-queue', res_same['reason'])

        claimed = json.loads(self.cli('claim', '--number', '20', '--run-dir', str(self.root / 'r'), '--thread', 't', '--host-ready'))
        token = claimed['token']
        self.cli('finish', '--token', token, expected=2)  # requires --cleanup-verified
        finished = json.loads(self.cli('finish', '--token', token, '--cleanup-verified', '--status', 'waiting_ci'))
        self.assertEqual(finished['status'], 'finished')

    def test_export_digest_and_retry_pending(self):
        report = self.root / 'report.json'
        report.write_text(json.dumps({
            'status': 'complete_no_eligible_entries',
            'finished_at': '2026-11-19T22:23:57-05:00',
            'pinned_work': 'a' * 40,
            'work_ci': 'green',
            'queue': [],
            'updated': [],
            'skipped_conflicts': [],
            'skipped_other': [],
            'ci_still_running': [],
            'resolutions': [],
            'queue_evidence': {'stack_exclusions': [{'stack': 1885, 'size': 11, 'approved_members': list(range(10))}]},
        }))
        with patch.object(ustate, 'export_digest', side_effect=OSError('disk full')):
            self.cli('export-digest', '--report-json', str(report), '--report-path', '/runs/report.md', expected=2)
        saved_state = ustate.read_state(self.state_dir)
        self.assertEqual(saved_state['pending_digest_dates'], ['2026-11-19'])

        retried = json.loads(self.cli('digest', '--retry-pending'))
        self.assertEqual(retried['failures'], {})
        self.assertEqual(len(retried['exports']), 1)
        exported = json.loads(Path(retried['exports'][0]).read_text())
        self.assertEqual(exported['date'], '2026-11-19')
        self.assertEqual(exported['run_status'], 'complete_no_eligible_entries')
        self.assertEqual(exported['report_path'], '/runs/report.md')
        self.assertEqual(exported['queue_evidence']['stack_exclusions'][0]['stack'], 1885)
        self.assertTrue((self.state_dir / 'digest/2026-11-19.md').is_file())
        self.assertEqual(ustate.read_state(self.state_dir)['pending_digest_dates'], [])


if __name__ == '__main__':
    unittest.main()
