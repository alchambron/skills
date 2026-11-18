import datetime as dt
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('review_queue', Path(__file__).with_name('queue.py'))
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)


class QueueTests(unittest.TestCase):
    def test_direct_request_required(self):
        pr = {'state': 'open', 'base': {'repo': {'full_name': queue.REPOSITORY}}, 'requested_reviewers': [{'login': 'alchambron'}]}
        self.assertTrue(queue.eligible(pr))
        pr['requested_reviewers'] = []
        self.assertFalse(queue.eligible(pr))

    def test_caps_and_duplicate_pr(self):
        state = {'jobs': {}}
        queue.reserve(state, 'static', 1, {}, '/tmp/a', 'one')
        queue.reserve(state, 'static', 2, {}, '/tmp/b', 'two')
        with self.assertRaises(ValueError):
            queue.reserve(state, 'static', 3, {}, '/tmp/c', 'three')
        queue.reserve(state, 'live', 3, {}, '/tmp/c', 'three')
        with self.assertRaises(ValueError):
            queue.reserve(state, 'live', 4, {}, '/tmp/d', 'four')
        with self.assertRaises(ValueError):
            queue.reserve(state, 'live', 1, {}, '/tmp/a', 'one')

    def test_same_night_predecessor(self):
        now = dt.datetime.fromisoformat('2026-11-19T01:00:00-05:00')
        receipt = {'repository': queue.REPOSITORY, 'status': 'completed', 'started_at': '2026-11-18T22:00:00-05:00', 'completed_at': '2026-11-18T23:00:00-05:00'}
        self.assertIn('09:00', queue.overnight(now, receipt))
        receipt['started_at'] = '2026-11-17T22:00:00-05:00'
        with self.assertRaises(ValueError):
            queue.overnight(now, receipt)
        with self.assertRaises(ValueError):
            queue.overnight(dt.datetime.fromisoformat('2026-11-19T10:00:00-05:00'), receipt)


if __name__ == '__main__':
    unittest.main()
