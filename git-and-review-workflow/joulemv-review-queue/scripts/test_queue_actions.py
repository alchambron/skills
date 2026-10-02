#!/usr/bin/env python3
"""Independent, offline queue action lifecycle checks; no network or shared-file writes."""
import unittest
import review_queue as q

USER = lambda login: {'login': login, '__typename': 'User'}
BOT = lambda login: {'login': login, '__typename': 'Bot'}
def pr(**changes):
    value = {'number': 1, 'title': 'Synthetic change', 'author': USER('author'), 'reviews': [], 'comments': [], 'reviewThreads': [],
             'timelineItems': [], 'reviewRequests': [], 'commits': [], 'headRefOid': 'h',
             'baseRefName': 'work', 'isDraft': False, 'updatedAt': '2026-01-05T00:00:00Z',
             'url': 'https://github.com/example/repo/pull/1', 'mergeable': 'MERGEABLE', 'reviewDecision': None}
    value.update(changes)
    return value

def snapshot(p, required=1, stale=False):
    return {'prs': [p], 'collected_at': '2026-01-05T00:00:00-05:00', 'errors': [], 'open_count': 1, 'rules': {'work': {'protection': {'required_pull_request_reviews': {
        'required_approving_review_count': required, 'dismiss_stale_reviews': stale}}, 'rules': []}}}

def review(state='COMMENTED', at='2026-01-03T00:00:00Z', body='Thanks, confirmed.', commit='h', login='reviewer', rid='r'):
    return {'id': rid, 'author': USER(login), 'state': state, 'submittedAt': at,
            'updatedAt': at, 'body': body, 'url': 'https://github.com/example/repo/pull/1#'+rid,
            'commit': {'oid': commit}}

def request(login='reviewer', at='2026-01-02T00:00:00Z', typename='ReviewRequestedEvent'):
    return {'__typename': typename, 'createdAt': at, 'requestedReviewer': USER(login)}

def obligation(formal=False):
    return {'id': '1:f', 'pr': 1, 'reviewer': 'reviewer', 'at': '2026-01-01T00:00:00Z',
            'url': 'feedback', 'formal': formal, 'classification': None if formal else 'f',
            'handoffs': [], 'delegations': []}

def members(actions):
    return {(a['owner'], a['action']) for a in actions}

class Lifecycle(unittest.TestCase):
    def test_uncertain_feedback_does_not_fabricate_unnamed_review(self):
        actions, uncertain = q.compose(snapshot(pr()), [obligation()], [{'id':'f','suggestion':'uncertain'}])
        self.assertEqual(members(actions), set())
        self.assertTrue(uncertain)

    def test_unavailable_feedback_discloses_failure_without_fabricating_review(self):
        actions, uncertain = q.compose(snapshot(pr()), [obligation()], [
            {'id': 'f', 'suggestion': 'unavailable', 'error_type': 'max_tokens_exceeded'}])
        self.assertEqual(members(actions), set())
        self.assertIn('max_tokens_exceeded', uncertain[0]['reason'])

    def test_uncertain_feedback_blocks_its_reviewer_but_keeps_independent_request(self):
        p = pr(reviewRequests=[{'requestedReviewer': USER('reviewer')},
                               {'requestedReviewer': USER('independent')}])
        actions, _ = q.compose(snapshot(p), [obligation()], [{'id': 'f', 'suggestion': 'uncertain'}])
        self.assertEqual(members(actions), {('independent', 'Review')})

    def test_unresolved_second_finding_blocks_inferred_rereview(self):
        reply = {'id': 'reply', 'author': USER('author'), 'createdAt': '2026-01-02T00:00:00Z',
                 'body': 'Fixed the first issue, please review.', 'url': 'reply-url'}
        first = dict(obligation(), handoffs=['h'])
        second = dict(obligation(), id='1:second', classification='second')
        actions, _ = q.compose(snapshot(pr(comments=[reply])), [first, second], [
            {'id': 'f', 'suggestion': 'no'}, {'id': 'h', 'suggestion': 'yes'},
            {'id': 'second', 'suggestion': 'uncertain'}])
        self.assertEqual(members(actions), set())

    def test_uncertain_handoff_does_not_guess_next_owner(self):
        reply = {'id': 'reply', 'author': USER('author'), 'createdAt': '2026-01-02T00:00:00Z',
                 'body': 'Changes are ready.', 'url': 'reply-url'}
        for suggestion in ('uncertain', 'unavailable'):
            actions, uncertain = q.compose(snapshot(pr(comments=[reply])),
                [dict(obligation(), handoffs=['h'])], [{'id': 'f', 'suggestion': 'yes'},
                                                     {'id': 'h', 'suggestion': suggestion}])
            self.assertEqual(members(actions), set())
            self.assertTrue(uncertain)

    def test_uncertain_or_multiple_fix_owners_are_not_assigned_to_author(self):
        for judgments in ([{'id': 'd1', 'suggestion': 'uncertain'}],
                          [{'id': 'd1', 'suggestion': 'unavailable'}],
                          [{'id': 'd1', 'suggestion': 'yes'}, {'id': 'd2', 'suggestion': 'yes'}]):
            obs = dict(obligation(), delegations=[('d1', 'fixer')])
            if len(judgments) > 1:
                obs['delegations'].append(('d2', 'other-fixer'))
            actions, uncertain = q.compose(snapshot(pr()), [obs],
                [{'id': 'f', 'suggestion': 'yes'}, *judgments])
            self.assertEqual(members(actions), set())
            self.assertTrue(uncertain)

    def test_known_bot_is_not_a_fix_owner_even_with_positive_judgment(self):
        bot = dict(review(login='macroscopeapp'), author=BOT('macroscopeapp'))
        obs = dict(obligation(), delegations=[('d', 'macroscopeapp')])
        actions, _ = q.compose(snapshot(pr(reviews=[bot])), [obs],
            [{'id': 'f', 'suggestion': 'yes'}, {'id': 'd', 'suggestion': 'yes'}])
        self.assertEqual(members(actions), {('author', 'Respond to human review')})

    def test_annotations_code_and_bot_commands_are_not_delegation_candidates(self):
        reply = {'id': 'reply', 'author': USER('author'), 'createdAt': '2026-01-02T00:00:00Z',
                 'body': 'Already had @ResponseStatus(BAD_REQUEST). `@Nullable` is a type.\n'
                         '```java\n@Override\nvoid run() {}\n```\n'
                         '@coderabbitai full review\n@fixer please address this.', 'url': 'reply-url'}
        p = pr(comments=[reply], reviews=[review(state='CHANGES_REQUESTED', at='2026-01-01T00:00:00Z')])
        _, obs = q.prepare(snapshot(p))
        self.assertEqual({person for ob in obs for _, person in ob['delegations']}, {'fixer'})

    def test_inconsistent_current_request_requires_verification(self):
        p = pr(timelineItems=[request()], reviews=[review()],
               reviewRequests=[{'requestedReviewer': USER('reviewer')}])
        actions, uncertain = q.compose(snapshot(p), [], [])
        self.assertEqual(members(actions), set())
        self.assertTrue(uncertain)

    def test_inferred_handoff_is_consumed_by_later_submitted_review(self):
        reply = {'id': 'reply', 'author': USER('author'), 'createdAt': '2026-01-02T00:00:00Z',
                 'body': 'Fixed, please re-review.', 'url': 'reply-url'}
        p = pr(comments=[reply], reviews=[review()])
        ob = dict(obligation(), handoffs=['h'])
        actions, _ = q.compose(snapshot(p), [ob], [{'id': 'f', 'suggestion': 'no'},
                                                {'id': 'h', 'suggestion': 'yes'}])
        self.assertEqual(members(actions), {('Reviewer needed', 'Review')})

    def test_pending_review_does_not_consume_active_request(self):
        p = pr(timelineItems=[request()], reviews=[review(state='PENDING')],
               reviewRequests=[{'requestedReviewer': USER('reviewer')}])
        actions, _ = q.compose(snapshot(p), [obligation(True)], [])
        self.assertEqual(members(actions), {('reviewer', 'Review')})

    def test_partial_interpretation_is_visible_with_complete_github_collection(self):
        s = snapshot(pr())
        s['interpretation_metrics'] = {'cases': 10, 'unavailable_cases': 3}
        actions, uncertain = q.compose(s, [obligation()], [{'id': 'f', 'suggestion': 'unavailable'}])
        rendered = q.render(s, actions, uncertain)
        self.assertIn('**Partial interpretation:** 3/10', rendered)
        self.assertNotIn('No pending human review actions found.', rendered)

    def test_conflict_caps_later_review_stage(self):
        p = pr(mergeable='CONFLICTING', reviews=[review(state='APPROVED')],
               reviewRequests=[{'requestedReviewer': USER('independent')}])
        actions, _ = q.compose(snapshot(p, required=2), [], [])
        self.assertEqual(actions[0]['stage_score'], 80)
        self.assertEqual(actions[0]['score'], 60)

    def test_required_failed_check_caps_later_review_stage(self):
        p = pr(reviews=[review(state='APPROVED')], required_checks=[{'bucket': 'fail'}],
               reviewRequests=[{'requestedReviewer': USER('independent')}])
        actions, _ = q.compose(snapshot(p, required=2), [], [])
        self.assertEqual(actions[0]['score'], 60)

    def test_average_deduplicates_prs_and_excludes_drafts(self):
        p = pr(reviewRequests=[{'requestedReviewer': USER('reviewer')},
                               {'requestedReviewer': USER('independent')}])
        s = snapshot(p)
        draft = pr(number=2, isDraft=True, reviewRequests=[{'requestedReviewer': USER('reviewer')}])
        s['prs'].append(draft)
        actions, uncertain = q.compose(s, [], [])
        self.assertIn('Average progress: 10% (1 non-draft PRs)', q.render(s, actions, uncertain))

    def test_completed_comment_review_consumes_old_request(self):
        p = pr(timelineItems=[request()], reviews=[review()])
        actions, _ = q.compose(snapshot(p), [obligation()], [{'id':'f','suggestion':'no'}])
        self.assertNotIn(('reviewer','Review'), members(actions))
        # Required approval is still unsatisfied; a new generic request is allowed.
        self.assertEqual(members(actions), {('Reviewer needed','Review')})

    def test_removed_request_is_not_a_handoff(self):
        p = pr(timelineItems=[request(), request(at='2026-01-03T00:00:00Z', typename='ReviewRequestRemovedEvent')])
        actions, _ = q.compose(snapshot(p), [obligation()], [{'id':'f','suggestion':'yes'}])
        self.assertEqual(members(actions), {('author','Respond to human review')})

    def test_old_request_cannot_clear_newer_feedback(self):
        p = pr(timelineItems=[request()], reviews=[review(body='Fix another regression.')])
        actions, _ = q.compose(snapshot(p), [obligation()], [{'id':'f','suggestion':'yes'}])
        self.assertEqual(members(actions), {('author','Respond to human review')})

    def test_current_renewed_request_assigns_rereview(self):
        p = pr(timelineItems=[request()], reviewRequests=[{'requestedReviewer':USER('reviewer')}])
        actions, _ = q.compose(snapshot(p), [obligation(True)], [])
        self.assertEqual(members(actions), {('reviewer','Review')})

    def test_independent_review_survives_author_fix(self):
        p = pr(reviewRequests=[{'requestedReviewer':USER('reviewer')}, {'requestedReviewer':USER('independent')}])
        actions, _ = q.compose(snapshot(p), [obligation(True)], [])
        self.assertEqual(members(actions), {('author','Respond to human review'), ('independent','Review')})

    def test_formal_request_not_cleared_by_commented_review(self):
        p = pr(reviews=[review(state='CHANGES_REQUESTED', at='2026-01-01T00:00:00Z', rid='first'), review(rid='later')])
        self.assertEqual(q.effective_reviews(p)['reviewer']['state'], 'CHANGES_REQUESTED')
        cases, obs = q.prepare(snapshot(p))
        self.assertTrue(any(ob['formal'] for ob in obs))

    def test_approval_supersedes_changes_request(self):
        p = pr(reviews=[review(state='CHANGES_REQUESTED', at='2026-01-01T00:00:00Z', rid='first'), review(state='APPROVED', rid='later')])
        _, obs = q.prepare(snapshot(p))
        actions, _ = q.compose(snapshot(p), obs, [])
        self.assertEqual(members(actions), set())

    def test_dismissed_review_no_longer_formal_fix(self):
        p = pr(reviews=[review(state='CHANGES_REQUESTED', at='2026-01-01T00:00:00Z', rid='first'), review(state='DISMISSED', rid='later')])
        _, obs = q.prepare(snapshot(p))
        self.assertFalse(any(ob['formal'] for ob in obs))

    def test_stale_approval_reopens_required_review(self):
        p = pr(reviews=[review(state='APPROVED', commit='old')])
        actions, _ = q.compose(snapshot(p, stale=True), [], [])
        self.assertEqual(members(actions), {('Reviewer needed','Review')})
        actions, _ = q.compose(snapshot(p, stale=False), [], [])
        self.assertEqual(members(actions), set())

    def test_bot_only_without_required_human_review_is_omitted(self):
        r = review(state='CHANGES_REQUESTED'); r['author'] = BOT('macroscopeapp')
        p = pr(reviews=[r])
        _, obs = q.prepare(snapshot(p, required=0))
        actions, _ = q.compose(snapshot(p, required=0), obs, [])
        self.assertEqual(members(actions), set())

    def test_bot_approval_does_not_satisfy_skill_human_requirement(self):
        r = review(state='APPROVED'); r['author'] = BOT('macroscopeapp')
        actions, _ = q.compose(snapshot(pr(reviews=[r])), [], [])
        self.assertEqual(members(actions), {('Reviewer needed','Review')})

    def test_draft_without_human_action_omitted(self):
        actions, _ = q.compose(snapshot(pr(isDraft=True)), [], [])
        self.assertEqual(members(actions), set())

    def test_draft_explicit_review_is_zero(self):
        p = pr(isDraft=True, reviewRequests=[{'requestedReviewer':USER('reviewer')}])
        actions, _ = q.compose(snapshot(p), [], [])
        self.assertEqual(members(actions), {('reviewer','Review')})
        self.assertEqual(actions[0]['score'], 0)

if __name__ == '__main__': unittest.main()
