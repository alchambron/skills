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
