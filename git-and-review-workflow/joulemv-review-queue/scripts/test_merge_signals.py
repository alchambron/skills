#!/usr/bin/env python3
"""Offline checks for merge priority and review attention; no network."""
import unittest
import merge_signals as signals
import review_queue as q
import typesafe_triage as jev


def pr(paths=(), title='feat: Add a thing', labels=(), lines=10, number=1, **changes):
    value = {'number': number, 'title': title, 'description': 'Body text.', 'createdAt': '2026-01-01T00:00:00Z',
             'url': f'https://github.com/example/repo/pull/{number}', 'headRefOid': 'h', 'baseRefName': 'work',
             'author': {'login': 'author', '__typename': 'User'}, 'isDraft': False, 'mergeable': 'MERGEABLE',
             'labels': {'nodes': [{'name': name} for name in labels]}, 'changedFiles': len(paths),
             'files': [{'path': path, 'additions': lines, 'deletions': 0, 'changeType': 'MODIFIED'} for path in paths],
             'reviews': [], 'reviewRequests': [], 'commits': []}
    value.update(changes)
    return value


def judged(number=1, fix='no', exposed='no'):
    return {f'{number}:priority:security_fix': {'suggestion': fix},
            f'{number}:priority:security_exposure': {'suggestion': exposed}}


class Attention(unittest.TestCase):
    def level(self, *paths, lines=10):
        return signals.attention(pr(paths, lines=lines))

    def test_sql_migration_is_critical(self):
        result = self.level('src/main/sql/update-tenant.sql', 'docs/notes.md')
        self.assertEqual((result['level'], result['reasons']), ('Critical', ['SQL migration']))

    def test_skills_docs_and_tests_are_light(self):
        result = self.level('.agents/skills/x/SKILL.md', '.claude/skills/x', 'AGENT.md',
                            'src/test/java/org/SecurityConfigTest.java', 'frontend/src/locales/en.json')
        self.assertEqual(result['level'], 'Light')

    def test_backend_and_dependencies_are_careful(self):
        self.assertEqual(self.level('src/main/java/org/ixeee/service/InvoiceService.java')['level'], 'Careful')
        self.assertEqual(self.level('frontend/package.json')['level'], 'Careful')

    def test_auth_paths_rise_above_their_area(self):
        self.assertEqual(self.level('src/main/java/org/ixeee/spring/config/SecSecurityConfig.java')['level'], 'Critical')
        self.assertEqual(self.level('frontend/src/helpers/Query/endpointPermissions.ts')['level'], 'Careful')
        self.assertEqual(self.level('frontend/src/screens/Chart/Chart.tsx')['level'], 'Standard')

    def test_production_deploy_is_critical(self):
        self.assertEqual(self.level('.github/workflows/deploy.yml')['level'], 'Critical')
        self.assertEqual(self.level('cicd/production/deploy.sh')['level'], 'Critical')
        self.assertEqual(self.level('.github/workflows/frontend-checks.yml')['level'], 'Standard')

    def test_large_runtime_diff_rises_one_tier_but_docs_stay_light(self):
        self.assertEqual(self.level('frontend/src/a.ts', lines=2500)['level'], 'Careful')
        self.assertEqual(self.level('docs/huge.md', lines=50000)['level'], 'Light')

    def test_truncated_file_list_is_provisional_and_missing_list_is_unknown(self):
        self.assertTrue(signals.attention(pr(['docs/a.md'], changedFiles=3000))['provisional'])
        self.assertIsNone(signals.attention(pr(files=None))['level'])


class Priority(unittest.TestCase):
    def rate(self, value, judgments=None):
        return signals.assess({'prs': [value]}, [{'id': k, **v} for k, v in (judged() if judgments is None else judgments).items()])[1]['priority']

    def test_live_security_fix_is_urgent(self):
        self.assertEqual(self.rate(pr(['src/main/java/A.java']), judged(fix='yes', exposed='yes'))['level'], 'Urgent')

    def test_security_fix_without_confirmed_exposure_is_high_and_asks_verification(self):
        result = self.rate(pr(['src/main/java/A.java']), judged(fix='yes', exposed='uncertain'))
        self.assertEqual(result['level'], 'High')
        self.assertIn('verify whether the weakness is live', result['notes'])

    def test_urgent_ticket_label_is_urgent_without_security_claim(self):
        result = self.rate(pr(['src/main/java/A.java'], labels=['Urgent Ticket']))
        self.assertEqual((result['level'], result['reason']), ('Urgent', 'urgent ticket'))
        self.assertFalse(signals.needs_verification({'priority': result}))
        self.assertEqual(self.rate(pr(['docs/a.md'], labels=['urgent ticket']))['level'], 'Urgent')
        both = self.rate(pr(['src/main/java/A.java'], labels=['Urgent Ticket']), judged(fix='yes'))
        self.assertEqual((both['level'], both['reason']), ('Urgent', 'urgent ticket, security fix'))
        self.assertEqual(self.rate(pr(['src/main/java/A.java'], labels=['Not urgent']))['level'], 'Low')

    def test_runtime_bug_fix_is_medium_and_docs_fix_is_low(self):
        self.assertEqual(self.rate(pr(['frontend/src/a.ts'], title='fix(charts): Repair axis'))['level'], 'Medium')
        self.assertEqual(self.rate(pr(['frontend/src/a.ts'], title='JMV-B-202: Remove row'))['level'], 'Medium')
        self.assertEqual(self.rate(pr(['docs/a.md'], title='fix(docs): Typo'))['level'], 'Low')

    def test_uncertain_security_is_medium_and_flagged(self):
        result = self.rate(pr(['frontend/src/a.ts']), judged(fix='uncertain'))
        self.assertEqual(result['level'], 'Medium')
        self.assertTrue(signals.needs_verification({'priority': result}))

    def test_low_probability_uncertainty_ranks_as_no(self):
        result = self.rate(pr(['frontend/src/a.ts']), {'1:priority:security_fix': {'suggestion': 'uncertain', 'probability_yes': 0.2},
                                                        '1:priority:security_exposure': {'suggestion': 'no'}})
        self.assertEqual((result['level'], result['notes']), ('Low', []))

    def test_missing_judgment_is_disclosed(self):
        self.assertIn('security not assessed', self.rate(pr(['frontend/src/a.ts']), {})['notes'])


class JevCases(unittest.TestCase):
    def test_cases_build_valid_batched_payloads_with_capped_paths(self):
        paths = [f'frontend/src/f{i}.ts' for i in range(300)] + ['src/main/sql/update-tenant.sql']
        cases = signals.priority_cases(pr(paths))
        payloads = [jev.build_payload(c, 'jev-1.13.0') for c in cases]
        batched = list(jev.batches(payloads))
        self.assertEqual(len(batched), 1)
        state = batched[0][0]['state']['pr']
        self.assertEqual((state['changed_files'], state['paths_shown']), (301, signals.MAX_JEV_PATHS))
        self.assertEqual(state['changed_paths'][0], 'src/main/sql/update-tenant.sql')

    def test_uncollected_files_skip_jev(self):
        self.assertEqual(signals.priority_cases(pr(files=None)), [])


class Report(unittest.TestCase):
    def snapshot(self, *prs):
        return {'prs': list(prs), 'collected_at': '2026-01-05T00:00:00-05:00', 'errors': [], 'open_count': len(prs),
                'rules': {'work': {'protection': {'required_pull_request_reviews': {'required_approving_review_count': 1}}, 'rules': []}}}

    def test_priority_section_lists_urgent_and_high_including_prs_without_actions(self):
        urgent = pr(['src/main/java/A.java'], title='fix: Close auth bypass', number=7)
        low = pr(['docs/a.md'], number=8)
        draft = pr(['src/main/java/B.java'], labels=['Urgent Ticket'], number=9, isDraft=True)
        snap = self.snapshot(urgent, low, draft)
        merge = signals.assess(snap, [{'id': k, **v} for k, v in {**judged(7, 'yes', 'yes'), **judged(8), **judged(9)}.items()])
        report = q.render(snap, [], [], 'checkbox', merge)
        self.assertIn('**Merge priority · security first**', report)
        self.assertIn('- **Urgent · #7**', report)
        self.assertIn('No pending human review action; 0/1 required approvals.', report)
        self.assertNotIn('#9', report)
        self.assertIn('Urgent 1 · High 0 · Medium 0 · Low 1 (non-draft PRs)', report)
        self.assertIn('  Security fix, weakness live now · Attention **Careful** (backend code).', report)

    def test_unclear_security_is_listed_for_verification(self):
        value = pr(['frontend/src/a.ts'], number=4)
        snap = self.snapshot(value)
        merge = signals.assess(snap, [{'id': '4:priority:security_fix', 'suggestion': 'uncertain', 'probability_yes': 0.7}])
        self.assertIn('Security effect unclear, verify: #4.', q.render(snap, [], [], 'checkbox', merge))

    def test_action_entries_carry_priority_and_attention(self):
        value = pr(['src/main/sql/update-tenant.sql'], title='fix(charts): Migrate results', number=3)
        snap = self.snapshot(value)
        merge = signals.assess(snap, [{'id': k, **v} for k, v in judged(3).items()])
        action = {'pr': 3, 'owner': 'reviewer', 'action': 'Review', 'at': '', 'detail': 'Review requested.',
                  'score': 10, 'stage_score': 10, 'pass_label': 'pass 1', 'draft': False}
        report = q.render(snap, [action], [], 'checkbox', merge)
        self.assertIn('Priority **Medium** (bug fix) · Attention **Critical** (SQL migration).', report)


if __name__ == '__main__':
    unittest.main()
