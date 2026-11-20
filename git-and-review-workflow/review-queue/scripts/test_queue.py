from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('review_queue', Path(__file__).with_name('queue.py'))
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)

sys.path.insert(0, str(queue.ROOT.parent / 'review-all/scripts'))
import history
import render_results


HEAD, BASE = 'a' * 40, 'b' * 40


def pull(number, reviewer='alchambron', head=HEAD, draft=False):
    return {'number': number, 'state': 'open', 'draft': draft, 'head': {'sha': head},
            'base': {'sha': BASE, 'repo': {'full_name': queue.REPOSITORY}},
            'requested_reviewers': [{'login': reviewer}],
            'html_url': f'https://github.com/{queue.REPOSITORY}/pull/{number}', 'title': 'Example change'}


class GitHub:
    def __init__(self, prs):
        self.prs = {pr['number']: pr for pr in prs}
        self.requests = []

    def get(self, endpoint, parameters=None):
        self.requests.append((endpoint, parameters))
        if endpoint == 'user':
            return {'login': 'alchambron'}
        if endpoint.endswith('/pulls'):
            start = (parameters['page'] - 1) * parameters['per_page']
            return list(self.prs.values())[start:start + parameters['per_page']]
        return self.prs[int(endpoint.rsplit('/', 1)[-1])]


class QueueTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.state = self.root / 'review-queue'
        self.selection = self.root / 'selection.json'
        self.run = self.root / 'run'
        self.api = GitHub([pull(12)])
        self.start_patch(patch.object(queue, 'load_review', return_value=history))
        self.start_patch(patch.object(history, 'GitHub', return_value=self.api))
        self.start_patch(patch.dict('os.environ', {'REVIEW_STATE_DIR': str(self.root / 'review-all')}))

    def start_patch(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def cli(self, *args, expected=0):
        output = StringIO()
        with redirect_stdout(output):
            status = queue.main(['--state', str(self.state), *args])
        self.assertEqual(status, expected, output.getvalue())
        return output.getvalue()

    def plan(self, *args):
        self.cli('plan', '--output', str(self.selection), *args)
        return json.loads(self.selection.read_text())

    def claim(self, expected=0):
        output = self.cli('claim', '--selection', str(self.selection), '--run-dir', str(self.run),
                          '--thread', 'thread-link', '--host-ready', expected=expected)
        return json.loads(output)

    def test_event_reads_one_pr_directly_and_holds_other_reviewers_and_drafts(self):
        self.api.prs.update({13: pull(13, 'Kamyar-R'), 14: pull(14, draft=True)})
        selection = self.plan('--number', '12')
        self.assertEqual([12], [row['number'] for row in selection['selected']])
        self.assertEqual(['user', f'repos/{queue.REPOSITORY}/pulls/12'], [call[0] for call in self.api.requests])
        for number in ('13', '14'):
            result = self.plan('--number', number)
            self.assertFalse(result['selected'])
            self.assertIn('no current direct request', result['skipped'][0]['reason'])

    def test_sweep_pages_and_queues_missed_changed_and_failed_runs(self):
        self.api.prs = {number: pull(number, 'Kamyar-R') for number in range(1, 101)}
        self.api.prs.update({101: pull(101), 102: pull(102), 103: pull(103, head='c' * 40), 104: pull(104)})
        original = self.root / 'review-all-run'
        queue.save_json(original / queue.REPORT, {'example': True})
        prior = {'reviewedHead': HEAD, 'reviewedBase': BASE, 'complete': True, 'runDir': str(original)}
        history.save_json(history.state_dir() / 'state.json', {'prs': {
            history.key_of(queue.REPOSITORY, number): prior for number in (102, 103, 104)}})
        queue.save_state(self.state, {'version': 1, 'jobs': {'failed': {
            'number': 104, 'status': 'finished', 'pins': {'head': HEAD, 'base': BASE}, 'result': {'status': 'failed'}}}})
        with patch.object(history, 'carry_basis', side_effect=lambda api, name, entry, head, basis, warnings: basis):
            result = self.plan()
        self.assertEqual({101: 'new', 103: 'moved', 104: 'awaiting_decision'},
                         {row['number']: row['kind'] for row in result['selected']})
        self.assertEqual(102, result['skipped'][0]['number'])
        self.assertEqual([1, 2], [parameters['page'] for _, parameters in self.api.requests if parameters])

    def test_park_releases_static_capacity_and_waits_for_shared_fixer(self):
        self.plan('--number', '12')
        token = self.claim()['token']
        self.assertFalse(self.plan('--number', '12')['selected'])
        self.cli('park', '--token', token)
        respond = self.root / 'respond-queue/state.json'
        queue.save_json(respond, {'jobs': {'fixer': {'status': 'active', 'phase': 'fix', 'number': 99}}})
        self.assertIn('shared live resource cap', self.cli('claim-live', '--token', token, '--host-ready', expected=2))
        job = queue.read_state(self.state)['jobs'][token]
        self.assertEqual('waiting_live', job['phase'])
        self.assertIn('shared live resource cap', job['defer_reason'])
        self.api.prs[13] = pull(13)
        self.plan('--number', '13')
        self.run = self.root / 'other-run'
        self.assertIn('token', self.claim())
        respond.unlink()
        self.cli('claim-live', '--token', token, '--host-ready')
        self.api.prs[12]['requested_reviewers'] = [{'login': 'Kamyar-R'}]
        self.assertIn('no longer directly requested', self.cli('guard', '--token', token, expected=2))

    def test_changes_in_pins_and_stale_attempts_require_replanning(self):
        self.plan('--number', '12')
        self.api.prs[12]['head']['sha'] = 'c' * 40
        self.assertIn('head or base moved', self.claim(expected=2)['reason'])
        self.plan('--number', '12')
        token = self.claim()['token']
        self.cli('finish', '--token', token, '--status', 'failed', '--reason', 'worker exited',
                 '--cleanup-verified')
        self.assertIn('selection predates a newer attempt', self.claim(expected=2)['reason'])
        self.assertEqual('c' * 40, self.plan('--number', '12')['selected'][0]['head'])

    def test_finish_requires_cleaned_up_worker_and_renderer_validated_evidence(self):
        self.plan('--number', '12')
        token = self.claim()['token']
        args = ['finish', '--token', token, '--status', 'completed']
        self.cli(*args, expected=2)
        self.cli(*args, '--cleanup-verified', expected=2)
        pr = {'number': 12, 'repository': queue.REPOSITORY, 'title': 'Example change',
              'url': self.api.prs[12]['html_url'], 'head_sha': HEAD, 'base_sha': BASE,
              'current_head_sha': HEAD, 'current_base_sha': BASE, 'review': {'status': 'clean'},
              'publication': {'status': 'not_required'}, 'live': {'status': 'complete'}, 'scenarios': []}
        data = {'version': 1, 'prs': [pr], 'login': 'alchambron', 'collected_at': queue.now().isoformat()}
        ledger = self.run / 'results.json'
        queue.save_json(ledger, data)
        render_results.render(ledger, self.run / 'report')
        self.cli(*args, '--cleanup-verified', expected=2)
        pr['live'] = {'status': 'not_required', 'reason': 'Documentation-only change'}
        queue.save_json(ledger, data)
        render_results.render(ledger, self.run / 'report')
        result = json.loads(self.cli(*args, '--cleanup-verified'))
        written = json.loads(Path(result['digest']).read_text())['reviews'][0]
        self.assertEqual((12, HEAD, 'completed', str(self.run / queue.REPORT)),
                         (written['pr'], written['head_sha'], written['outcome'], written['html_path']))
        self.assertEqual('#12 review ready, check before approving', written['ping'])
        repeat = json.loads(self.cli(*args, '--cleanup-verified'))
        self.assertEqual(written['finished_at'], repeat['finished_at'])


if __name__ == '__main__':
    unittest.main()
