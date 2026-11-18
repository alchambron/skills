import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
import json
import contextlib
import io

spec = importlib.util.spec_from_file_location('respond_queue', Path(__file__).with_name('queue.py'))
q = importlib.util.module_from_spec(spec); spec.loader.exec_module(q)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.pr = {'state': 'open', 'user': {'login': 'alchambron'},
                   'base': {'repo': {'full_name': q.REPOSITORY}}}
        self.review = {'state': 'CHANGES_REQUESTED', 'user': {'login': 'alice', 'type': 'User'}}
        self.state = {'jobs': {}, 'batches': {}, 'skips': []}

    def claim(self, phase='analysis', number=1, batch='batch'):
        return q.reserve(self.state, phase, number, 'head', batch, '/tmp/run', 'thread', True)

    def test_human_only(self):
        q.eligible(self.pr, [self.review], [self.pr])
        for login, kind in [('robot[bot]', 'User'), ('CodeRabbit', 'User'), ('copilot', 'User'), ('alice', 'Bot')]:
            review = copy.deepcopy(self.review); review['user'] = {'login': login, 'type': kind}
            with self.assertRaises(ValueError): q.eligible(self.pr, [review], [self.pr])

    def test_author_and_stack(self):
        other = copy.deepcopy(self.pr); other['user']['login'] = 'bob'
        for pr, stack in [(other, [other]), (self.pr, [self.pr, other]), (self.pr, [])]:
            with self.assertRaises(ValueError): q.eligible(pr, [self.review], stack)

    def test_caps_and_duplicates(self):
        self.claim(number=1); self.claim(number=2)
        with self.assertRaises(ValueError): self.claim(number=3)
        with self.assertRaises(ValueError): self.claim(number=1)
        self.claim('fix', 4)
        with self.assertRaises(ValueError): self.claim('fix', 5)

    def test_host_unknown(self):
        with self.assertRaises(ValueError):
            q.reserve(self.state, 'fix', 1, 'h', 'b', '/tmp', 't', False)
        self.assertEqual(self.state['jobs'], {})

    def test_rounds_ci_and_handoff(self):
        token = self.claim('fix')
        q.finish(self.state, token, {'status': 'waiting_ci', 'ci': {'state': 'pending'}}, True)
        with self.assertRaises(ValueError): self.claim('fix')
        record = self.state['batches']['1:batch']; record['ci'] = {'state': 'failed'}
        token = self.claim('fix')
        q.finish(self.state, token, {'status': 'handoff', 'reason': 'CI still fails', 'ci': {'state': 'failed'}}, True)
        self.assertEqual(record['fix_rounds'], 2)
        self.assertEqual(q.digest(self.state)['prs'][0]['handoff_reason'], 'CI still fails')
        with self.assertRaises(ValueError): self.claim('fix')

    def test_round_limit_independent_of_status(self):
        for _ in range(2):
            token = self.claim('fix')
            q.finish(self.state, token, {'status': 'analyzed'}, True)
        with self.assertRaises(ValueError): self.claim('fix')

    def test_unchanged_terminal_batch(self):
        token = self.claim('fix')
        q.finish(self.state, token, {'status': 'completed', 'ci': {'state': 'not_required'}}, True)
        with self.assertRaises(ValueError): self.claim('analysis')
        self.claim('analysis', batch='new_feedback')

    def test_feedback_identity_edits(self):
        a = [{'url': 'u', 'date': '1'}, {'url': 'v', 'date': '2'}]
        self.assertEqual(q.batch_id(a), q.batch_id(list(reversed(a))))
        self.assertNotEqual(q.batch_id(a), q.batch_id([{'url': 'u', 'date': '3'}]))

    def test_cleanup_and_pending_completion(self):
        token = self.claim('fix')
        with self.assertRaises(ValueError): q.finish(self.state, token, {'status': 'analyzed'}, False)
        with self.assertRaises(ValueError): q.finish(self.state, token, {'status': 'completed'}, True)
        self.assertEqual(self.state['jobs'][token]['status'], 'active')

    def test_cli_ci_resume_and_terminal_handoff(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            root = Path(folder)
            with q.locked(root) as state:
                token = q.reserve(state, 'fix', 1, 'head', 'batch', '/tmp', 't', True)
                q.finish(state, token, {'status': 'waiting_ci', 'head': 'head',
                                     'ci': {'state': 'pending', 'head': 'head'}}, True)
            evidence = root/'ci.json'
            evidence.write_text(json.dumps({'state': 'failed', 'head': 'head', 'observed_at': 'now'}))
            self.assertEqual(q.main(['--state', folder, 'observe-ci', '--number', '1',
                '--batch', 'batch', '--head', 'head', '--ci', str(evidence)]), 0)
            with q.locked(root) as state:
                token = q.reserve(state, 'fix', 1, 'head', 'batch', '/tmp', 't', True)
                q.finish(state, token, {'status': 'waiting_ci', 'ci': {'state': 'pending'}}, True)
            self.assertEqual(q.main(['--state', folder, 'handoff', '--number', '1',
                '--batch', 'batch', '--reason', 'two rounds used']), 0)
            self.assertEqual(q.main(['--state', folder, 'digest']), 0)
            data = json.loads((root/'digest-index.json').read_text())
            self.assertEqual(data['prs'][0]['fix_rounds'], 2)
            self.assertEqual(data['prs'][0]['handoff_reason'], 'two rounds used')
            self.assertTrue((root/'digest.md').exists())

    def test_durable_reservation(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            with q.locked(root) as state:
                q.reserve(state, 'fix', 1, 'h', 'b', '/tmp', 't', True)
            with q.locked(root) as state:
                with self.assertRaises(ValueError): q.reserve(state, 'fix', 2, 'h', 'b', '/tmp', 't', True)
            self.assertEqual(len(q.digest(state)['reservations']), 1)


if __name__ == '__main__': unittest.main()
