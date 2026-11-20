from datetime import date, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from queue_digest import digest_date, export


DAY = date(2026, 11, 19)


def recorded(number, finished_at, status='completed', path='/run/report/scenario-guide.html', **result):
    return {
        'number': number,
        'pins': {'head': 'a' * 40, 'base': 'b' * 40},
        'phase': 'live',
        'status': 'finished',
        'run_dir': '/run',
        'thread': 't3://thread',
        'finished_at': finished_at,
        'result': {'status': status, 'html_path': path, 'verdict': 'clean', **result},
    }


class QueueDigestTests(unittest.TestCase):
    def test_toronto_dates_follow_daylight_saving(self):
        self.assertEqual(digest_date(datetime.fromisoformat('2026-11-19T04:59:59+00:00')), date(2026, 11, 18))
        self.assertEqual(digest_date(datetime.fromisoformat('2026-11-19T05:00:00+00:00')), DAY)
        self.assertEqual(digest_date(datetime.fromisoformat('2026-07-19T04:00:00+00:00')), date(2026, 7, 19))
        with self.assertRaisesRegex(ValueError, 'timezone'):
            digest_date(datetime.fromisoformat('2026-11-19T05:00:00'))

    def test_export_keeps_attempts_but_only_latest_ping_per_pr(self):
        state = {'jobs': {
            'prior_day': recorded(1, '2026-11-19T04:59:59+00:00'),
            'older': recorded(2, '2026-11-19T01:00:00-05:00'),
            'success': recorded(3, '2026-11-19T06:45:00+00:00', live='complete', publication='not_required'),
            'newer': recorded(2, '2026-11-19T06:15:00+00:00', 'blocked', reason='Prerequisite unavailable'),
            'failure': recorded(4, '2026-11-19T06:20:00+00:00', 'failed', path=None),
            'legacy': {'status': 'finished'},
        }}
        with TemporaryDirectory() as directory:
            target = export(Path(directory), state, DAY)
            self.assertEqual(target, Path(directory) / 'digest/2026-11-19.json')
            data = json.loads(target.read_text())
            self.assertEqual(data['schema_version'], 1)
            self.assertEqual(data['date'], '2026-11-19')
            self.assertEqual(data['repository'], 'EnerZam/JouleMV')
            reviews = data['reviews']
            self.assertEqual([row['token'] for row in reviews], ['success', 'failure', 'newer', 'older'])
            self.assertEqual(reviews[0]['head_sha'], 'a' * 40)
            self.assertEqual(reviews[0]['base_sha'], 'b' * 40)
            self.assertEqual(reviews[0]['live'], 'complete')
            self.assertEqual(reviews[0]['publication'], 'not_required')
            self.assertEqual(reviews[0]['html_path'], '/run/report/scenario-guide.html')
            self.assertEqual(reviews[2]['outcome'], 'blocked')
            self.assertEqual(reviews[2]['reason'], 'Prerequisite unavailable')
            markdown = target.with_suffix('.md').read_text()
            self.assertIn('#3 review ready, check before approving', markdown)
            self.assertIn('#2 review blocked, see report', markdown)
            self.assertNotIn('#2 review ready', markdown)
            self.assertIn('#4 review failed, report unavailable', markdown)
            self.assertLess(markdown.index('#2 review blocked'), markdown.index('## Recorded results'))

    def test_export_preserves_active_reservations_and_replaces_stale_files(self):
        state = {
            'jobs': {'running': {'number': 5, 'status': 'active', 'phase': 'waiting_live',
                                 'thread': 't3://run', 'defer_reason': 'Runtime busy'}},
            'skips': [
                {'at': '2026-11-19T04:59:59+00:00', 'reason': 'prior day'},
                {'at': '2026-11-19T06:00:00+00:00', 'number': 5, 'reason': 'Skipped\n<script> @someone'},
            ],
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = export(root, state, DAY)
            data = json.loads(target.read_text())
            self.assertEqual(data['reservations'][0]['token'], 'running')
            self.assertEqual(len(data['skips']), 1)
            markdown = target.with_suffix('.md').read_text()
            self.assertIn('Runtime busy', markdown)
            self.assertIn('Skipped &lt;script&gt; &#64;someone', markdown)
            self.assertNotIn('prior day', markdown)
            export(root, {'jobs': {}}, DAY)
            self.assertEqual(json.loads(target.read_text())['reservations'], [])
            self.assertNotIn('Runtime busy', target.with_suffix('.md').read_text())
            self.assertEqual(sorted(path.name for path in target.parent.iterdir()),
                             ['2026-11-19.json', '2026-11-19.md'])


if __name__ == '__main__':
    unittest.main()
