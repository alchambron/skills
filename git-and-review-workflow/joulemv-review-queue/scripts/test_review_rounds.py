#!/usr/bin/env python3
"""Offline checks for review rounds, re-review passes, and entry merging; no network."""
import unittest
import review_queue as q
from test_queue_actions import USER, BOT, pr, snapshot, review, request


def commit(oid, at, parents=1):
    return {'commit': {'oid': oid, 'committedDate': at, 'parents': {'totalCount': parents}}}


def obligation(oid, reviewer, at, formal=False, handoffs=()):
    return {'id': oid, 'pr': 1, 'reviewer': reviewer, 'at': at, 'url': oid, 'formal': formal,
            'classification': None if formal else oid + ':f', 'handoffs': list(handoffs), 'delegations': []}


def entries(actions):
    return {(a['owner'], a['action'], a['score'], a['pass_label'], a['detail']) for a in actions}


class ReviewRounds(unittest.TestCase):
    def test_second_reviewers_open_feedback_blocks_their_rereview_and_both_are_named(self):
        reply = {'id': 'rep', 'author': USER('author'), 'createdAt': '2026-01-04T00:00:00Z',
                 'body': 'Fixed, please re-review', 'url': 'x'}
        obs = [obligation('A', 'A', '2026-01-01T00:00:00Z', formal=True),
               obligation('B1', 'B', '2026-01-02T00:00:00Z', handoffs=['h1']),
               obligation('B2', 'B', '2026-01-05T00:00:00Z')]
        actions, _ = q.compose(snapshot(pr(comments=[reply])), obs, [
            {'id': 'B1:f', 'suggestion': 'yes'}, {'id': 'h1', 'suggestion': 'yes'}, {'id': 'B2:f', 'suggestion': 'yes'}])
        self.assertEqual(entries(actions), {('author', 'Respond to human review', 30, 'pass 1', 'Address feedback from A and B.')})

    def test_rereview_of_an_already_reviewed_revision_stays_in_that_pass(self):
        p = pr(headRefOid='c2', commits=[commit('c1', '2026-01-01T00:00:00Z'), commit('c2', '2026-01-03T00:00:00Z')],
               reviews=[review('CHANGES_REQUESTED', '2026-01-02T00:00:00Z', 'Fix it.', 'c1', 'A', 'a'),
                        review('COMMENTED', '2026-01-05T00:00:00Z', 'Looks better.', 'c2', 'B', 'b')],
               reviewRequests=[{'requestedReviewer': USER('A')}], timelineItems=[request('A', '2026-01-04T00:00:00Z')])
        cases, obs = q.prepare(snapshot(p))
        actions, _ = q.compose(snapshot(p), obs, [{'id': c['id'], 'suggestion': 'no'} for c in cases])
        self.assertEqual({(a['owner'], a['score'], a['pass_label']) for a in actions},
                         {('A', 50, 'at least pass 2; conservative')})

    def test_rerequest_after_resolved_inline_feedback_is_a_rereview(self):
        thread = {'id': 't', 'isResolved': True, 'isOutdated': True, 'comments': [
            {'id': 'tc', 'author': USER('A'), 'body': 'Rename this.', 'createdAt': '2026-01-02T00:00:00Z',
             'updatedAt': '2026-01-02T00:00:00Z', 'url': 't', 'pullRequestReview': {'id': 'a'}}]}
        p = pr(headRefOid='c2', commits=[commit('c1', '2026-01-01T00:00:00Z'), commit('c2', '2026-01-03T00:00:00Z')],
               reviewThreads=[thread], reviews=[review('COMMENTED', '2026-01-02T00:00:00Z', '', 'c1', 'A', 'a')],
               reviewRequests=[{'requestedReviewer': USER('A')}], timelineItems=[request('A', '2026-01-04T00:00:00Z')])
        cases, obs = q.prepare(snapshot(p))
        actions, _ = q.compose(snapshot(p), obs, [])
        self.assertEqual(entries(actions), {('A', 'Review', 50, 'at least pass 2; conservative', 'Re-review requested.')})

    def test_authors_reply_to_a_bot_is_not_human_feedback(self):
        thread = {'id': 't', 'isResolved': False, 'isOutdated': False, 'comments': [
            {'id': 'bot', 'author': BOT('coderabbitai'), 'body': 'Possible NPE', 'createdAt': '2026-01-02T00:00:00Z', 'updatedAt': 'x', 'url': 'b'},
            {'id': 'mine', 'author': USER('author'), 'body': 'Good catch, will fix.', 'createdAt': '2026-01-03T00:00:00Z', 'updatedAt': 'x', 'url': 'm'}]}
        cases, obs = q.prepare(snapshot(pr(reviewThreads=[thread])))
        self.assertEqual((cases, obs), ([], []))

    def test_branch_update_and_thread_reply_do_not_start_a_pass(self):
        p = pr(headRefOid='merge', commits=[commit('c1', '2026-01-01T00:00:00Z'), commit('merge', '2026-01-03T00:00:00Z', parents=2)],
               reviews=[review('CHANGES_REQUESTED', '2026-01-02T00:00:00Z', 'Fix it.', 'c1', 'A', 'a'),
                        review('CHANGES_REQUESTED', '2026-01-04T00:00:00Z', 'Still broken.', 'merge', 'A', 'a2')])
        self.assertEqual(q.review_pass(p)[0], 1)
        p['reviews'][1] = review('COMMENTED', '2026-01-04T00:00:00Z', '', 'merge', 'A', 'a2')
        p['commits'][1] = commit('merge', '2026-01-03T00:00:00Z')
        self.assertEqual(q.review_pass(p)[0], 1)

    def test_satisfied_required_approval_scores_ninety(self):
        p = pr(reviews=[review('APPROVED', '2026-01-02T00:00:00Z', '', 'h', 'A', 'a')],
               reviewRequests=[{'requestedReviewer': USER('C')}], timelineItems=[request('C', '2026-01-03T00:00:00Z')])
        actions, _ = q.compose(snapshot(p), [], [])
        self.assertEqual({(a['owner'], a['score']) for a in actions}, {('C', 90)})

    def test_draft_with_only_an_inferred_handoff_is_omitted(self):
        reply = {'id': 'rep', 'author': USER('author'), 'createdAt': '2026-01-04T00:00:00Z',
                 'body': 'Addressed, please re-review', 'url': 'x'}
        actions, _ = q.compose(snapshot(pr(isDraft=True, comments=[reply])),
                               [obligation('X', 'A', '2026-01-02T00:00:00Z', handoffs=['h'])],
                               [{'id': 'X:f', 'suggestion': 'yes'}, {'id': 'h', 'suggestion': 'yes'}])
        self.assertEqual(actions, [])

    def test_team_handle_is_not_a_person(self):
        self.assertEqual(q.mentioned_people('Ping @EnerZam/backend and @alice'), {'alice'})


if __name__ == '__main__':
    unittest.main()
