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

    def test_optional_unavailable_check_needs_reason(self):
        self.assertEqual(self.report(verification=[dict(command='lint docs', result='inapplicable',
            essential=False, reason='No documentation linter', evidence='commands.log')])['decision'], 'push')
