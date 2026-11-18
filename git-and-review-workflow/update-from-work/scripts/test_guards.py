import unittest
from guards import push_allowed, ci_state

class Guards(unittest.TestCase):
    def test_push_boundaries(self):
        pr = dict(author='alchambron', repo='EnerZam/JouleMV',
                  head_repo='EnerZam/JouleMV', state='OPEN', draft=False, branch='feature')
        self.assertTrue(push_allowed(pr, 'a', 'a', True))
        for key, value in [('author', 'other'), ('head_repo', 'fork'),
                           ('branch', 'work'), ('state', 'CLOSED'), ('draft', True)]:
            self.assertFalse(push_allowed(dict(pr, **{key: value}), 'a', 'a', True))
        self.assertFalse(push_allowed(pr, 'a', 'b', True))
        self.assertFalse(push_allowed(pr, 'a', 'a', False))

    def test_scheduled_maintenance_does_not_block_push_ci(self):
        checks = {f'push-{i}': 'green' for i in range(8)}
        events = {name: 'push' for name in checks}
        required = list(checks)
        for event in ('schedule', 'workflow_dispatch', 'repository_dispatch'):
            with self.subTest(event=event):
                self.assertEqual(ci_state(dict(checks, prune='red'), required,
                                         dict(events, prune=event)), 'green')
        for state in ('red', 'pending'):
            with self.subTest(state=state):
                self.assertEqual(ci_state(dict(checks, **{'push-0': state}, prune='red'),
                                         required, dict(events, prune='schedule')), state)
        self.assertEqual(ci_state({'prune': 'red'}, [], {'prune': 'schedule'}), 'unknown')
        self.assertEqual(ci_state({'test': 'red'}, [], {}), 'red')

    def test_ci_fails_closed(self):
        for checks, required, expected in [({}, [], 'unknown'),
                ({'test': 'green'}, ['build'], 'unknown'),
                ({'test': 'pending'}, ['test'], 'pending'),
                ({'test': 'red'}, ['test'], 'red'),
                ({'test': 'skipped'}, [], 'pending'),
                ({'test': 'green'}, ['test'], 'green')]:
            self.assertEqual(ci_state(checks, required), expected)

if __name__ == '__main__':
    unittest.main()
