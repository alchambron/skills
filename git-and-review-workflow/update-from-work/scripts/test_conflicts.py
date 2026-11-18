import unittest
from conflicts import resolution_report


class ConflictReports(unittest.TestCase):
    def report(self, **kwargs):
        args = dict(number=1508, conflicted_files=['docs/feature-map/index.json'],
                    resolutions=[dict(file='docs/feature-map/index.json', method='regenerated',
                                      reason='Both source additions retained', coherent=True,
                                      command='npm run generate')],
                    verification=[dict(command='npm test', result='passed', evidence='tests.log')])
        args.update(kwargs)
        return resolution_report(**args)

    def test_regeneration_report_preserves_audit_evidence(self):
        report = self.report()
        self.assertEqual(report['decision'], 'push')
        self.assertEqual(report['number'], 1508)
        self.assertEqual(report['resolutions'][0]['command'], 'npm run generate')
        self.assertEqual(report['verification'][0]['result'], 'passed')
        self.assertEqual(report['abort_reason'], '')
        self.assertEqual(report['conflicted_files'], ['docs/feature-map/index.json'])

    def test_all_supported_methods(self):
        for method in ('kept ours', 'kept theirs', 'combined'):
            self.assertEqual(self.report(resolutions=[dict(file='docs/feature-map/index.json',
                method=method, reason='Intent verified', coherent=True)])['decision'], 'push')

    def test_incomplete_or_incoherent_resolution_aborts(self):
        for changes in (dict(resolutions=[]), dict(verification=[]),
                        dict(abort_reason='Contradictory requirements'),
                        dict(resolutions=[dict(file='docs/feature-map/index.json',
                             method='regenerated', reason='Generated', coherent=True)])):
            report = self.report(**changes)
            self.assertEqual(report['decision'], 'abort')
            self.assertTrue(report['abort_reason'])

    def test_failed_or_unavailable_essential_verification_aborts(self):
        for result in ('failed', 'unavailable'):
            report = self.report(verification=[dict(command='npm test', result=result,
                reason='Dependency missing', evidence='tests.log')])
            self.assertEqual(report['decision'], 'abort')
            self.assertIn('npm test', report['abort_reason'])
            self.assertEqual(report['verification'][0]['reason'], 'Dependency missing')

    def test_optional_skipped_check_needs_reason(self):
        passed = dict(command='npm test', result='passed', evidence='tests.log')
        optional = dict(command='lint docs', result='inapplicable', essential=False,
                        reason='No documentation linter', evidence='commands.log')
        self.assertEqual(self.report(verification=[passed, optional])['decision'], 'push')
        self.assertEqual(self.report(verification=[passed, dict(optional, reason='')])['decision'],
                         'abort')

    def test_only_nonessential_skipped_checks_abort(self):
        report = self.report(verification=[dict(command='lint docs', result='inapplicable',
            essential=False, reason='No documentation linter', evidence='commands.log')])
        self.assertEqual(report['decision'], 'abort')
        self.assertIn('Missing passed essential verification', report['abort_reason'])

    def test_passed_nonessential_check_alone_aborts(self):
        report = self.report(verification=[dict(command='lint docs', result='passed',
            essential=False, evidence='commands.log')])
        self.assertEqual(report['decision'], 'abort')

    def test_report_keeps_original_conflicts_on_early_abort(self):
        files = ['db/migrate/2026_add.rb', 'package.json']
        report = resolution_report(1600, files, [], [],
                                   abort_reason='Incompatible migration ordering')
        self.assertEqual(report['decision'], 'abort')
        self.assertEqual(report['conflicted_files'], files)
        self.assertEqual(report['resolutions'], [])
        self.assertEqual(report['verification'], [])
        self.assertTrue(report['abort_reason'].startswith('Incompatible migration ordering'))
        self.assertEqual(set(report), {'number', 'decision', 'conflicted_files', 'resolutions',
                                       'verification', 'abort_reason'})
        files.append('mutated')
        self.assertEqual(len(report['conflicted_files']), 2)


if __name__ == '__main__':
    unittest.main()
