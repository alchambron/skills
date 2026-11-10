import unittest
from queue import build_queue, select_top


def pr(n, day, base='work', decision='APPROVED', kind='User'):
    return dict(number=n, author='alchambron', repo='EnerZam/JouleMV',
                head_repo='EnerZam/JouleMV', state='OPEN', draft=False,
                branch=f'p{n}', base=base, reviewDecision=decision,
                reviews=[dict(login='reviewer', type=kind, state='APPROVED',
                              submittedAt=f'2026-11-{day:02d}T12:00:00Z')])


class QueueTests(unittest.TestCase):
    def test_whole_stack_gating(self):
        self.assertEqual(build_queue([pr(1, 1), pr(2, 2, 'p1', 'REVIEW_REQUIRED')]), [])
        self.assertEqual(build_queue([pr(1, 1, decision='REVIEW_REQUIRED'), pr(2, 2, 'p1')]), [])
        self.assertEqual(build_queue([pr(2, 2, 'p1'), pr(1, 1)])[0]['prs'], [1, 2])

    def test_last_stack_approval_orders_entry(self):
        queue = build_queue([pr(1, 1), pr(2, 5, 'p1'), pr(3, 3)])
        self.assertEqual([e['prs'] for e in queue], [[3], [1, 2]])

    def test_bot_and_dismissed_approvals_excluded(self):
        self.assertEqual(build_queue([pr(1, 1, kind='Bot')]), [])
        item = pr(1, 1)
        item['reviews'][0]['state'] = 'DISMISSED'
        self.assertEqual(build_queue([item]), [])

    def test_top_n_does_not_backfill(self):
        queue = build_queue([pr(i, i) for i in range(1, 6)])
        self.assertEqual([e['prs'] for e in select_top(queue)], [[1], [2]])
        self.assertEqual(len(select_top(queue, 3)), 3)
        with self.assertRaises(ValueError):
            select_top(queue, 4)

    def test_missing_parent_and_cycle(self):
        self.assertEqual(build_queue([pr(1, 1, 'missing')]), [])
        with self.assertRaises(ValueError):
            build_queue([pr(1, 1, 'p2'), pr(2, 2, 'p1')])
