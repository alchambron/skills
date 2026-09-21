"""Regression checks for fresh collection and semantic cache invalidation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import review_queue as queue
import typesafe_triage as jev


def case():
    return {'id': '1:feedback', 'kind': 'feedback', 'focal_id': 'c1', 'subject': 'Fix requested by reviewer',
            'head_sha': 'old', 'context_complete': True, 'pr': {'number': 1, 'author': 'author'},
            'events': [{'id': 'c1', 'actor': 'reviewer', 'actor_type': 'User', 'at': '2026-09-21T10:00:00Z',
                        'body': 'Please fix this.', 'url': 'https://example.com/1', 'commit': 'old', 'resolved': False}]}


class SemanticCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        self.request = Mock(side_effect=lambda payload, key: {'model': payload['model'], 'answers': {
            q: {'type': 'noul', 'noul': 0.95} for q in payload['questions']}})
        self.patch = patch.object(jev, 'request', self.request)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def evaluate(self, evidence, model='jev-1.13.0'):
        return jev.evaluate([jev.build_payload(evidence, model)], 'test', cache_dir=self.cache)

    def test_push_reuses_judgment_but_reports_current_head(self):
        evidence = case()
        self.evaluate(evidence)
        evidence['head_sha'] = 'new'
        evidence['events'][0]['commit'] = 'new'
        result = self.evaluate(evidence)
        self.assertEqual(result['metrics']['requests'], 0)
        self.assertEqual(result['results'][0]['head_sha'], 'new')
        self.assertEqual(result['results'][0]['suggestion'], 'yes')

    def test_semantic_changes_invalidate(self):
        self.evaluate(case())
        for key, value in [('body', 'Never mind.'), ('resolved', True), ('actor', 'author')]:
            with self.subTest(key=key):
                evidence = case()
                evidence['events'][0][key] = value
                self.assertEqual(self.evaluate(evidence)['metrics']['requests'], 1)

    def test_new_reply_in_another_thread_invalidates(self):
        evidence = case()
        self.evaluate(evidence)
        evidence['events'].append(dict(evidence['events'][0], id='c2', actor='author',
                                      body='Fixed, please re-review.', thread='other'))
        self.assertEqual(self.evaluate(evidence)['metrics']['requests'], 1)

    def test_model_and_question_changes_invalidate(self):
        self.evaluate(case())
        self.assertEqual(self.evaluate(case(), 'jev-1.13.1')['metrics']['requests'], 1)
        with patch.dict(jev.PREDICATES, {'feedback': ('Changed question?', 'Yes', 'No')}):
            self.assertEqual(self.evaluate(case())['metrics']['requests'], 1)

    def test_alias_and_failed_answers_are_not_cached(self):
        self.evaluate(case(), 'jev-latest')
        self.assertEqual(self.evaluate(case(), 'jev-latest')['metrics']['requests'], 1)
        self.request.side_effect = lambda *args: {}
        self.assertEqual(self.evaluate(case())['results'][0]['suggestion'], 'unavailable')
        self.assertEqual(self.evaluate(case())['metrics']['requests'], 1)


class BodyCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / 'bodies.json'
        self.pr = {'number': 1, 'reviews': [], 'comments': [{'id': 'c1', 'updatedAt': 'v1'}],
                   'reviewThreads': []}
        self.gh = Mock()
        self.gh.graphql.return_value = {'nodes': [{'id': 'c1', 'updatedAt': 'v1', 'body': 'Original'}]}

    def refresh(self, pr=None):
        return queue.refresh_bodies(self.gh, [copy.deepcopy(pr or self.pr)], self.cache, 1)

    def test_first_full_collection_seeds_cache_without_extra_fetch(self):
        self.pr['comments'][0]['body'] = 'Already collected'
        prs, errors, metrics = self.refresh()
        self.assertEqual(prs[0]['comments'][0]['body'], 'Already collected')
        self.assertEqual(errors, [])
        self.assertEqual(metrics['bodies_requested'], 0)
        self.gh.graphql.assert_not_called()
        del self.pr['comments'][0]['body']
        self.assertEqual(self.refresh()[2]['body_cache_hits'], 1)

    def test_warm_refresh_reuses_body(self):
        self.refresh()
        self.gh.reset_mock()
        prs, errors, metrics = self.refresh()
        self.assertEqual(errors, [])
        self.assertEqual(prs[0]['comments'][0]['body'], 'Original')
        self.assertEqual(metrics, {'body_cache_hits': 1, 'bodies_requested': 0})
        self.gh.graphql.assert_not_called()

    def test_edit_refreshes_and_deletion_removes_body(self):
        self.refresh()
        self.pr['comments'][0]['updatedAt'] = 'v2'
        self.gh.graphql.return_value = {'nodes': [{'id': 'c1', 'updatedAt': 'v2', 'body': 'Edited'}]}
        self.assertEqual(self.refresh()[0][0]['comments'][0]['body'], 'Edited')
        self.pr['comments'] = []
        self.assertEqual(self.refresh()[0][0]['comments'], [])
        self.assertEqual(json.loads(self.cache.read_text())['bodies'], {})

    def test_changed_or_unavailable_body_omits_pr(self):
        for result in [{'nodes': [None]}, {'nodes': [{'id': 'c1', 'updatedAt': 'v2', 'body': 'Racing edit'}]}]:
            self.gh.graphql.return_value = result
            prs, errors, _ = self.refresh()
            self.assertEqual(prs, [])
            self.assertTrue(errors)

    def test_fetch_failure_never_uses_stale_text(self):
        self.refresh()
        self.pr['comments'][0]['updatedAt'] = 'v2'
        self.gh.graphql.side_effect = RuntimeError('Unavailable')
        prs, errors, _ = self.refresh()
        self.assertEqual(prs, [])
        self.assertTrue(errors)

    def test_corrupt_cache_refetches(self):
        self.cache.write_text('{broken')
        self.assertEqual(self.refresh()[0][0]['comments'][0]['body'], 'Original')

    def test_metadata_query_keeps_freshness_fields(self):
        fields = queue.GitHub(incremental=True).pr_fields()
        self.assertNotIn(' body ', fields)
        for expected in ('updatedAt', 'isResolved', 'reviewRequests', 'commits', 'pageInfo'):
            self.assertIn(expected, fields)


if __name__ == '__main__':
    unittest.main()
