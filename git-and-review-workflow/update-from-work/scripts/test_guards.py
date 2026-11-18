import unittest
from guards import push_allowed, ci_state, normalize_checks

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
        self.assertEqual(ci_state(dict(checks, prune='red'), required,
                                 dict(events, prune='schedule')), 'green')
        for event in ('workflow_dispatch', 'repository_dispatch'):
            with self.subTest(event=event):
                self.assertEqual(ci_state(dict(checks, prune='red'), required,
                                         dict(events, prune=event), {'prune'}), 'green')
                self.assertEqual(ci_state(dict(checks, prune='red'), required,
                                         dict(events, prune=event)), 'red')
        for state in ('red', 'pending'):
            with self.subTest(state=state):
                self.assertEqual(ci_state(dict(checks, **{'push-0': state}, prune='red'),
                                         required, dict(events, prune='schedule')), state)
        self.assertEqual(ci_state({'prune': 'red'}, [], {'prune': 'schedule'}), 'unknown')
        self.assertEqual(ci_state({'test': 'red'}, [], {}), 'red')
        self.assertEqual(ci_state({'test': 'green'}, ['test'], {'test': 'schedule'}), 'unknown')

    def test_ci_fails_closed(self):
        for checks, required, expected in [({}, [], 'unknown'),
                ({'test': 'green'}, ['build'], 'unknown'),
                ({'test': 'pending'}, ['test'], 'pending'),
                ({'test': 'red'}, ['test'], 'red'),
                ({'test': 'skipped'}, [], 'pending'),
                ({'test': 'green'}, ['test'], 'green')]:
            self.assertEqual(ci_state(checks, required), expected)

def run(identity, order, conclusion='success', event='push', **extra):
    return dict(identity=identity, kind='check', event=event, status='completed',
                conclusion=conclusion, order=order, **extra)


class Normalization(unittest.TestCase):
    def test_newer_schedule_red_keeps_push_green(self):
        records = [run('ci/test', 1), run('ci/test', 2, 'failure', 'schedule')]
        checks = normalize_checks(records)
        self.assertEqual(checks, {'ci/test': 'green'})
        self.assertEqual(ci_state(checks, ['ci/test']), 'green')

    def test_newest_eligible_rerun_wins(self):
        self.assertEqual(normalize_checks([run('ci/test', 1, 'failure'), run('ci/test', 2)]),
                         {'ci/test': 'green'})
        self.assertEqual(normalize_checks([run('ci/test', 2), run('ci/test', 1, 'failure')]),
                         {'ci/test': 'green'})
        self.assertEqual(normalize_checks([run('ci/test', 1), run('ci/test', 2, 'failure')]),
                         {'ci/test': 'red'})

    def test_red_conclusions(self):
        for conclusion in ('failure', 'cancelled', 'timed_out', 'action_required'):
            with self.subTest(conclusion=conclusion):
                checks = normalize_checks([run('ci/test', 1, conclusion)])
                self.assertEqual(checks, {'ci/test': 'red'})
                self.assertEqual(ci_state(checks, ['ci/test']), 'red')

    def test_combined_status_pending_does_not_gate(self):
        records = [run('ci/test', 1),
                   dict(identity='combined', kind='combined', state='pending', order=2),
                   dict(identity='legacy/deploy', kind='status', state='success', order=1)]
        checks = normalize_checks(records)
        self.assertEqual(checks, {'ci/test': 'green', 'legacy/deploy': 'green'})
        self.assertEqual(ci_state(checks, ['ci/test']), 'green')

    def test_legacy_status_contexts_still_gate(self):
        for value, expected in (('pending', 'pending'), ('failure', 'red'), ('error', 'red')):
            with self.subTest(value=value):
                checks = normalize_checks([run('ci/test', 1), dict(
                    identity='legacy', kind='status', state=value, order=1)])
                self.assertEqual(ci_state(checks, ['ci/test']), expected)

    def test_in_progress_push_is_pending(self):
        checks = normalize_checks([dict(run('ci/test', 1), status='in_progress',
                                        conclusion=None)])
        self.assertEqual(ci_state(checks, ['ci/test']), 'pending')

    def test_dispatch_needs_verified_maintenance(self):
        base = [run('ci/test', 1)]
        for event in ('workflow_dispatch', 'repository_dispatch'):
            with self.subTest(event=event):
                manual = run('ci/manual', 2, 'failure', event)
                self.assertEqual(ci_state(normalize_checks(base + [manual]), ['ci/test']), 'red')
                maintenance = dict(manual, maintenance=True)
                self.assertEqual(ci_state(normalize_checks(base + [maintenance]), ['ci/test']),
                                 'green')
                newer = run('ci/test', 2, 'failure', event)
                self.assertEqual(normalize_checks(base + [newer]), {'ci/test': 'red'})

    def test_unknown_provenance_retained(self):
        checks = normalize_checks([run('ci/test', 1), run('other', 1, 'failure', None)])
        self.assertEqual(ci_state(checks, ['ci/test']), 'red')

    def test_excluded_maintenance_cannot_satisfy_required(self):
        checks = normalize_checks([run('ci/test', 1, event='schedule')])
        self.assertEqual(ci_state(checks, ['ci/test']), 'unknown')

    def test_skipped_needs_verified_optional_path_gate(self):
        self.assertEqual(ci_state(normalize_checks([run('ci/test', 1),
                         run('docs', 1, 'skipped')]), ['ci/test']), 'pending')
        self.assertEqual(ci_state(normalize_checks([run('ci/test', 1),
                         run('docs', 1, 'skipped', optional_path_gated=True)]), ['ci/test']),
                         'green')
        self.assertEqual(ci_state(normalize_checks([
            run('ci/test', 1, 'neutral', optional_path_gated=True)]), ['ci/test']), 'unknown')


if __name__ == '__main__':
    unittest.main()
