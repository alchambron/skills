"""Offline safety checks: all gh calls are mocked at the subprocess boundary."""
import copy
import hashlib
import io
import json
import subprocess
import unittest
from unittest.mock import mock_open, patch

import risk_labels as labels

SHA, BASE = 'a' * 40, 'b' * 40
LIMITED, DISRUPTIVE, CRITICAL = 'risk:limited', 'risk:disruptive', 'risk:critical'


def state(names=()):
    return dict(state='open', head={'sha': SHA}, base={'sha': BASE}, title='Pinned change',
                body=None, changed_files=2, labels=[{'name': name} for name in names])


def plan(*numbers, risk=LIMITED):
    return dict(repository='EnerZam/JouleMV', prs=[dict(
        number=number, head_sha=SHA, base_sha=BASE, metadata_sha256=labels.metadata_digest(state()),
        risk=risk, reason='Reviewed diff', failure='Page could fail', recovery='Revert deployment',
        confidence='supported', production_migration=False, file_count=2, evidence=['src/example.py'])
        for number in (numbers or (1724,))])


class GitHub:
    def __init__(self, test, states=None, missing=(), hook=None, write_hook=None):
        self.test = test
        self.states = copy.deepcopy(states if states is not None else {1724: state()})
        self.definitions = {name: {'name': name, 'color': '123456', 'description': 'Keep me'}
                            for name in labels.MANAGED - set(missing)}
        self.calls, self.writes, self.reads = [], [], {}
        self.hook, self.write_hook = hook, write_hook

    def process(self, args, **kwargs):
        self.calls.append(args)
        self.test.assertIsInstance(args, list)
        self.test.assertEqual(kwargs, dict(capture_output=True, text=True, timeout=30, check=False))
        self.test.assertEqual(args[0], 'gh')
        if args[1:3] == ['pr', 'edit'] or '--method' in args:
            self.writes.append(args)
            if self.write_hook:
                result = self.write_hook(self, args)
                if result is not None:
                    return result
            if args[1:3] == ['pr', 'edit']:
                number = int(args[3])
                names = {item['name'] for item in self.states[number]['labels']}
                if '--add-label' in args:
                    names.add(args[args.index('--add-label') + 1])
                if '--remove-label' in args:
                    names -= set(args[args.index('--remove-label') + 1].split(','))
                self.states[number]['labels'] = [{'name': name} for name in sorted(names)]
                return subprocess.CompletedProcess(args, 0, 'PR edited', '')
            self.test.assertEqual(args[args.index('--method') + 1], 'POST')
            fields = dict(args[i + 1].split('=', 1) for i, arg in enumerate(args) if arg == '-f')
            self.test.assertNotIn(fields['name'], self.definitions)
            if len(fields['description']) > 100:
                return subprocess.CompletedProcess(args, 1, '', 'Validation Failed (HTTP 422)')
            self.definitions[fields['name']] = fields
            result = fields
        elif '/pulls/' in args[-1]:
            number = int(args[-1].rsplit('/', 1)[1])
            self.reads[number] = self.reads.get(number, 0) + 1
            if self.hook:
                self.hook(self, number, self.reads[number])
            result = self.states[number]
        else:
            name = args[-1].rsplit('/', 1)[1]
            if name not in self.definitions:
                return subprocess.CompletedProcess(args, 1, '', 'Not Found (HTTP 404)')
            result = self.definitions[name]
        return subprocess.CompletedProcess(args, 0, json.dumps(result), '')


class RiskLabelsTest(unittest.TestCase):
    def run_plan(self, data=None, apply=True, **kwargs):
        self.remote = GitHub(self, **kwargs)
        with patch.object(labels.subprocess, 'run', side_effect=self.remote.process):
            return labels.run_plan(plan() if data is None else data, apply)

    def test_dry_run_never_writes_and_urgent_never_overrides(self):
        for risk in labels.MANAGED:
            result = self.run_plan(plan(risk=risk), apply=False, missing=labels.MANAGED,
                                   states={1724: state(['Urgent Ticket', 'high'])})
            self.assertEqual(result['mode'], 'dry-run')
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['prs'][0]['target'], risk)
            self.assertEqual(result['prs'][0]['before'], ['Urgent Ticket', 'high'])
            self.assertEqual(result['prs'][0]['status'], 'planned')
            self.assertEqual(self.remote.writes, [])

    def test_metadata_digest_only_title_body_and_none_normalization(self):
        expected = hashlib.sha256(b'{"body":"","title":"Pinned change"}').hexdigest()
        data = state(['high', CRITICAL, 'Urgent Ticket'])
        self.assertEqual(labels.metadata_digest(data), expected)
        data.update(body='', changed_files=999, head={'sha': 'c' * 40})
        self.assertEqual(labels.metadata_digest(data), expected)

    def test_whole_schema_validation_precedes_remote_reads(self):
        variants = [None, {}, dict(plan(), extra=True), dict(plan(), repository='../bad'),
                    dict(plan(), prs={}), dict(plan(), prs=plan()['prs'] * 501),
                    dict(plan(), prs=plan()['prs'] * 2)]
        invalid = [('number', True), ('number', 0), ('base_sha', 'abc'), ('head_sha', 'abc'),
                   ('metadata_sha256', 'abc'), ('risk', 'limited'), ('risk', []),
                   ('confidence', 'certain'), ('reason', ' '), ('failure', None), ('recovery', ''),
                   ('production_migration', 1), ('file_count', True), ('file_count', 0),
                   ('evidence', []), ('evidence', [' ']), ('evidence', [1]), ('evidence', ['x'] * 21)]
        for key, value in invalid:
            data = plan(1724, 1725)
            data['prs'][1][key] = value
            variants.append(data)
        missing_field = plan()
        del missing_field['prs'][0]['failure']
        variants += [missing_field]
        for data in variants:
            with self.subTest(data=data), patch.object(labels.subprocess, 'run') as process:
                result = labels.run_plan(data, True)
                self.assertEqual(result['status'], 'error')
                self.assertTrue(result['failures'])
                process.assert_not_called()

    def test_migrations_require_critical_and_provisional_is_supported(self):
        for risk in [LIMITED, DISRUPTIVE, CRITICAL]:
            data = plan(risk=risk)
            data['prs'][0].update(production_migration=True, confidence='provisional')
            result = self.run_plan(data)
            self.assertEqual(result['status'], 'ok' if risk == CRITICAL else 'error')
            if risk != CRITICAL:
                self.assertEqual(self.remote.calls, [])

    def test_full_preflight_before_first_write(self):
        def write(remote, args):
            self.assertTrue({1724, 1725, 1726} <= remote.reads.keys())
        result = self.run_plan(plan(1724, 1725, 1726), write_hook=write,
                               states={n: state() for n in [1724, 1725, 1726]})
        self.assertEqual([pr['status'] for pr in result['prs']], ['applied'] * 3)

    def test_last_stale_or_closed_pr_blocks_entire_batch(self):
        mutations = [dict(head={'sha': 'c' * 40}), dict(base={'sha': 'c' * 40}),
                     dict(body='Changed'), dict(title='Changed'), dict(state='closed'),
                     dict(changed_files=3)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                stale = dict(state(), **mutation)
                result = self.run_plan(plan(1724, 1725), states={1724: state(), 1725: stale})
                self.assertEqual(result['status'], 'error')
                self.assertEqual(result['failures'][0]['number'], 1725)
                self.assertEqual(self.remote.writes, [])
                self.assertEqual(result['prs'][0]['status'], 'not_attempted')

    def test_malformed_remote_response_refused(self):
        for mutation in [dict(labels=None), dict(labels=['high']), dict(labels=[{'name': ''}]),
                         dict(labels=[{'name': 'high'}, {'name': 'high'}]), dict(title=None),
                         dict(body=False), dict(changed_files=True), dict(base=None)]:
            with self.subTest(mutation=mutation):
                result = self.run_plan(states={1724: dict(state(), **mutation)})
                self.assertEqual(result['status'], 'error')
                self.assertEqual(self.remote.writes, [])

    def test_only_exact_managed_labels_removed_and_other_labels_preserved(self):
        unowned = {'high', 'medium', 'low', 'Urgent Ticket', 'Risk:limited', 'risk:other', 'security'}
        result = self.run_plan(states={1724: state(unowned | {DISRUPTIVE, CRITICAL})})
        pr = result['prs'][0]
        self.assertEqual(pr['status'], 'applied')
        self.assertEqual(set(pr['remove']), {DISRUPTIVE, CRITICAL})
        self.assertEqual(set(pr['after']), unowned | {LIMITED})
        self.assertEqual(len(self.remote.writes), 1)
        self.assertNotIn('--labels', self.remote.writes[0])

    def test_repeat_apply_does_not_mutate_or_read_definitions(self):
        first = self.run_plan(states={1724: state(['high'])})
        self.assertEqual(first['prs'][0]['status'], 'applied')
        current = self.remote.states
        repeated = self.run_plan(states=current, missing=labels.MANAGED)
        self.assertEqual(repeated['prs'][0]['status'], 'unchanged')
        self.assertEqual(self.remote.writes, [])
        self.assertEqual(len(self.remote.calls), 2)

    def test_definition_creation_lazy_exact_and_existing_definitions_untouched(self):
        for risk, (color, description) in labels.LABELS.items():
            result = self.run_plan(plan(risk=risk), missing=labels.MANAGED)
            self.assertEqual(result['created_labels'], [risk])
            self.assertEqual(self.remote.definitions, {risk: dict(name=risk, color=color, description=description)})
            self.assertEqual(result['prs'][0]['status'], 'applied')
        result = self.run_plan()
        self.assertEqual(result['created_labels'], [])
        self.assertEqual(self.remote.definitions[LIMITED]['description'], 'Keep me')
        self.assertFalse(any('--force' in call or '--method' in call for call in self.remote.calls))

    def test_refresh_before_definition_write_and_before_edit_blocks_stale_pins(self):
        # Read 1 preflights; read 2 refreshes; read 3 immediately precedes creation;
        # read 4 follows creation and immediately precedes editing.
        for read_number in [2, 3, 4]:
            for field, value in [('head', {'sha': 'c' * 40}), ('base', {'sha': 'c' * 40}),
                                 ('body', 'Changed'), ('state', 'closed')]:
                def race(remote, number, count):
                    if count == read_number:
                        remote.states[number][field] = value
                result = self.run_plan(missing=[LIMITED], hook=race)
                self.assertEqual(result['status'], 'error')
                self.assertEqual(len(self.remote.writes), 1 if read_number == 4 else 0)
                self.assertFalse(any(call[1:3] == ['pr', 'edit'] for call in self.remote.writes))
                self.assertEqual(result['created_labels'], [LIMITED] if read_number == 4 else [])

    def test_preedit_unowned_label_change_is_preserved_without_reclassification(self):
        def race(remote, number, count):
            if count == 4:
                remote.states[number]['labels'].append({'name': 'Urgent Ticket'})
        result = self.run_plan(hook=race)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['prs'][0]['after'], ['Urgent Ticket', LIMITED])

    def test_sequential_edit_failure_retains_partial_audit_and_stops(self):
        def failure(remote, args):
            if args[1:4] == ['pr', 'edit', '1725']:
                return subprocess.CompletedProcess(args, 1, '', 'PRIVATE TOKEN')
        result = self.run_plan(plan(1724, 1725, 1726), write_hook=failure,
                               states={n: state() for n in [1724, 1725, 1726]})
        self.assertEqual([pr['status'] for pr in result['prs']],
                         ['applied', 'failed_or_unverified', 'not_attempted'])
        self.assertEqual(len(self.remote.writes), 2)
        self.assertEqual(result['prs'][0]['after'], [LIMITED])
        self.assertIsNone(result['prs'][1]['after'])
        self.assertNotIn('PRIVATE TOKEN', json.dumps(result))

    def test_postwrite_races_stop_remaining_prs_and_retain_observed_labels(self):
        races = [dict(head={'sha': 'c' * 40}), dict(base={'sha': 'c' * 40}), dict(body='Changed'),
                 dict(state='closed'), dict(labels=[{'name': LIMITED}]),
                 dict(labels=[{'name': LIMITED}, {'name': DISRUPTIVE}, {'name': 'high'}]),
                 dict(labels=[{'name': LIMITED}, {'name': 'high'}, {'name': 'new-label'}])]
        for mutation in races:
            def race(remote, number, count):
                if number == 1724 and count == 5:
                    remote.states[number].update(mutation)
            result = self.run_plan(plan(1724, 1725), hook=race,
                                   states={1724: state(['high']), 1725: state()})
            self.assertEqual(result['status'], 'error')
            self.assertEqual([pr['status'] for pr in result['prs']],
                             ['failed_or_unverified', 'not_attempted'])
            self.assertEqual(len(self.remote.writes), 1)
            self.assertIsNotNone(result['prs'][0]['after'])

    def test_timeout_unknown_edit_outcome_is_never_retried(self):
        def failure(remote, args):
            raise subprocess.TimeoutExpired(args, 30, output='PRIVATE TOKEN')
        result = self.run_plan(write_hook=failure)
        self.assertEqual(result['prs'][0]['status'], 'failed_or_unverified')
        self.assertEqual(len(self.remote.writes), 1)
        self.assertNotIn('PRIVATE TOKEN', json.dumps(result))

    def test_creation_failure_stops_before_edit_and_keeps_audit(self):
        def failure(remote, args):
            return subprocess.CompletedProcess(args, 1, '', 'PRIVATE TOKEN')
        result = self.run_plan(missing=[LIMITED], write_hook=failure)
        self.assertEqual(result['created_labels'], [])
        self.assertEqual(result['failures'][0]['phase'], 'create_label')
        self.assertEqual(result['prs'][0]['status'], 'failed_or_unverified')
        self.assertEqual(len(self.remote.writes), 1)
        self.assertNotIn('PRIVATE TOKEN', json.dumps(result))

    def test_changed_head_after_first_pr_stops_batch(self):
        def race(remote, number, count):
            if number == 1725 and count == 2:
                remote.states[number]['base']['sha'] = 'c' * 40
        result = self.run_plan(plan(1724, 1725, 1726), hook=race,
                               states={n: state() for n in [1724, 1725, 1726]})
        self.assertEqual([pr['status'] for pr in result['prs']], ['applied', 'blocked', 'not_attempted'])
        self.assertEqual(len(self.remote.writes), 1)

    def test_invalid_definition_and_creation_responses_stop_without_retry(self):
        for response in [None, {}, {'name': 'Risk:limited'}]:
            remote = GitHub(self)
            remote.definitions[LIMITED] = response
            with patch.object(labels.subprocess, 'run', side_effect=remote.process):
                result = labels.run_plan(plan(), True)
            self.assertEqual(result['status'], 'error')
            self.assertEqual(remote.writes, [])
        def bad_create(remote, args):
            return subprocess.CompletedProcess(args, 0, '{}', '')
        result = self.run_plan(missing=[LIMITED], write_hook=bad_create)
        self.assertEqual(result['prs'][0]['status'], 'failed_or_unverified')
        self.assertEqual(len(self.remote.writes), 1)

    def test_transport_errors_are_sanitized_and_finite(self):
        for response in [subprocess.CompletedProcess([], 1, 'PRIVATE TOKEN', 'PRIVATE TOKEN'),
                         subprocess.CompletedProcess([], 0, 'PRIVATE TOKEN', ''),
                         subprocess.TimeoutExpired(['gh'], 30), OSError('PRIVATE TOKEN')]:
            with patch.object(labels.subprocess, 'run', side_effect=[response]) as process:
                result = labels.run_plan(plan(), True)
                self.assertEqual(result['status'], 'error')
                self.assertEqual(process.call_count, 1)
                self.assertNotIn('PRIVATE TOKEN', json.dumps(result))

    def test_cli_json_default_apply_and_unreadable_plan(self):
        for arguments, apply in [(['--plan', 'plan.json'], False), (['--plan', 'plan.json', '--apply'], True)]:
            with patch('builtins.open', mock_open(read_data=json.dumps(plan()))), \
                    patch.object(labels, 'run_plan', return_value=labels.report(apply)) as runner, \
                    patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(labels.main(arguments), 0)
                self.assertEqual(json.loads(output.getvalue())['mode'], 'apply' if apply else 'dry-run')
                runner.assert_called_once_with(plan(), apply)
        for content in ['{', 'null']:
            with patch('builtins.open', mock_open(read_data=content)), \
                    patch.object(labels.subprocess, 'run') as process, \
                    patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(labels.main(['--plan', 'plan.json']), 1)
                self.assertTrue(json.loads(output.getvalue())['failures'])
                process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
