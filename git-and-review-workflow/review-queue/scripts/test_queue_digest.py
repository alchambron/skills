from datetime import date, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from queue_digest import digest_date, export


DAY = date(2026, 11, 19)
JOULE = 'EnerZam/JouleMV'
FRONT = 'EnerZam/WebSynco_React_Front'


def recorded(number, finished_at, status='completed', path='/run/report/scenario-guide.html',
             repository=JOULE, **result):
    return {
        'repository': repository,
        'pr_key': f'{repository}#{number}',
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

    def test_export_keeps_attempts_and_distinct_repo_pings_for_colliding_pr_numbers(self):
        state = {'jobs': {
            'prior_day': recorded(1, '2026-11-19T04:59:59+00:00'),
            'older': recorded(2, '2026-11-19T01:00:00-05:00'),
            'success': recorded(3, '2026-11-19T06:45:00+00:00', live='complete', publication='not_required'),
            'front_same_num': recorded(3, '2026-11-19T06:46:00+00:00', repository=FRONT,
                                       verdict='issues', live='not_required', publication='verified',
                                       review_url=f'https://github.com/{FRONT}/pull/3#pullrequestreview-9',
                                       summary_path='/run/report/summary.md'),
            'issues': recorded(6, '2026-11-19T06:50:00+00:00', verdict='issues', live='not_required', publication='verified'),
            'rereq_ping': recorded(7, '2026-11-19T06:55:00+00:00', verdict='issues', live='not_required',
                                   publication='reused', ping_alexis=True, reason='No new commits'),
            'newer': recorded(2, '2026-11-19T06:15:00+00:00', 'blocked', reason='Prerequisite unavailable'),
            'failure': recorded(4, '2026-11-19T06:20:00+00:00', 'failed', path=None),
            'legacy': {'status': 'finished'},
        }}
        with TemporaryDirectory() as directory:
            target = export(Path(directory), state, DAY)
            self.assertEqual(target, Path(directory) / 'digest/2026-11-19.json')
            data = json.loads(target.read_text())
            self.assertEqual(data['schema_version'], 2)
            self.assertEqual(data['date'], '2026-11-19')
            self.assertEqual(data['repositories'], [JOULE, FRONT])
            reviews = data['reviews']
            self.assertEqual([row['token'] for row in reviews],
                             ['rereq_ping', 'issues', 'front_same_num', 'success', 'failure', 'newer', 'older'])
            self.assertEqual(reviews[0]['ping'], f'{JOULE}#7 re-requested at reviewed head, needs Alexis decision')
            self.assertEqual(reviews[1]['ping'], f'{JOULE}#6 changes requested, see report')
            self.assertEqual(reviews[2]['repository'], FRONT)
            self.assertEqual(reviews[2]['pr_key'], f'{FRONT}#3')
            self.assertEqual(reviews[2]['ping'], f'{FRONT}#3 changes requested, see report')
            self.assertEqual(reviews[3]['ping'], f'{JOULE}#3 review ready, check before approving')
            markdown = target.with_suffix('.md').read_text()
            self.assertIn(f'{JOULE}#7 re-requested at reviewed head, needs Alexis decision', markdown)
            self.assertIn(f'{FRONT}#3 changes requested, see report', markdown)
            self.assertIn(f'{JOULE}#3 review ready, check before approving', markdown)
            self.assertIn(f'{JOULE}#2 review blocked, see report', markdown)
            self.assertNotIn(f'{JOULE}#2 review ready', markdown)
            self.assertIn(f'{JOULE}#4 review failed, report unavailable', markdown)
            self.assertIn(f'https://github.com/{FRONT}/pull/3#pullrequestreview-9', markdown)
            self.assertIn('/run/report/summary.md', markdown)
            self.assertLess(markdown.index(f'{JOULE}#2 review blocked'), markdown.index('## Recorded results'))

    def test_export_preserves_active_reservations_dedupes_skips_and_replaces_stale_files(self):
        state = {
            'jobs': {'running': {'repository': FRONT, 'number': 132, 'pr_key': f'{FRONT}#132',
                                 'status': 'active', 'phase': 'waiting_live',
                                 'thread': 't3://run', 'defer_reason': 'Runtime busy'}},
            'skips': [
                {'at': '2026-11-19T04:59:59+00:00', 'reason': 'prior day'},
                {'at': '2026-11-19T06:00:00+00:00', 'command': 'plan', 'repository': FRONT, 'number': 132,
                 'pr_key': f'{FRONT}#132', 'reason': 'Skipped\n<script> @someone'},
                {'at': '2026-11-19T07:00:00+00:00', 'command': 'plan', 'repository': FRONT, 'number': 132,
                 'pr_key': f'{FRONT}#132', 'reason': 'Skipped\n<script> @someone'},
            ],
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = export(root, state, DAY)
            data = json.loads(target.read_text())
            self.assertEqual(data['reservations'][0]['token'], 'running')
            self.assertEqual(data['reservations'][0]['pr_key'], f'{FRONT}#132')
            self.assertEqual(len(data['skips']), 1)
            markdown = target.with_suffix('.md').read_text()
            self.assertIn('Runtime busy', markdown)
            self.assertIn(r'EnerZam/WebSynco\_React\_Front#132', markdown)
            self.assertIn('Skipped &lt;script&gt; &#64;someone', markdown)
            self.assertNotIn('prior day', markdown)
            export(root, {'jobs': {}}, DAY)
            self.assertEqual(json.loads(target.read_text())['reservations'], [])
            self.assertNotIn('Runtime busy', target.with_suffix('.md').read_text())
            self.assertEqual(sorted(path.name for path in target.parent.iterdir()),
                             ['2026-11-19.json', '2026-11-19.md'])

    def test_checkout_failure_exports_actionable_ping_above_results(self):
        repo = 'EnerZam/WebSynco_React_Native'
        url = f'https://github.com/{repo}/pull/372'
        reason = 'configured checkout at /work/WebSynco_React_Native is missing'
        ping = (f"{url} review-queue can't review this pr because there's no local checkout at "
                '/work/WebSynco_React_Native. restore the checkout or update its mapping.')
        blocked = {
            'at': '2026-11-19T07:00:00+00:00',
            'command': 'plan',
            'repository': repo,
            'number': 372,
            'url': url,
            'status': 'needs_attention',
            'reason': reason,
            'ping': ping,
        }
        earlier = {**blocked, 'at': '2026-11-19T06:59:00+00:00', 'reason': 'unmapped',
                   'ping': f'{url} add an unmapped checkout to the repository map.'}
        ordinary = {'at': '2026-11-19T07:01:00+00:00', 'reason': 'draft PR'}
        state = {'jobs': {}, 'skips': [earlier, blocked, ordinary]}
        with TemporaryDirectory() as directory:
            target = export(Path(directory), state, DAY)
            data = json.loads(target.read_text())
            self.assertEqual({**blocked, 'pr_key': f'{repo}#372'}, data['skips'][1])
            self.assertEqual([], data['reviews'])
            markdown = target.with_suffix('.md').read_text()
            banner, details = markdown.split('## Recorded results')
            self.assertIn('## Needs attention', banner)
            self.assertIn(f"<{url}> review-queue can't review this pr", banner)
            self.assertIn('restore the checkout or update its mapping.', banner)
            self.assertNotIn('add an unmapped checkout to the repository map.', banner)
            self.assertNotIn('draft PR', banner)
            self.assertIn('draft PR', details)


if __name__ == '__main__':
    unittest.main()
