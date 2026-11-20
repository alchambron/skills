import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('respond_queue', Path(__file__).with_name('queue.py'))
q = importlib.util.module_from_spec(spec); spec.loader.exec_module(q)
SKILLS = Path(__file__).resolve().parents[2]
ledger = q.load('respond_queue_test_ledger', SKILLS/'respond-all/scripts/ledger.py')
snapshots = q.load('respond_queue_test_snapshot', SKILLS/'respond/scripts/feedback_snapshot.py')

HEAD = 'a' * 40
PUSHED = 'b' * 40
URL = f'https://github.com/{q.REPOSITORY}/pull/1'
REVIEW_URL = URL + '#pullrequestreview-11'
DATE = '2026-01-01T00:00:00Z'


def pull(number=1, ref='feat', base='work', author='alchambron', head_repo=q.REPOSITORY, sha=HEAD):
    return {'number': number, 'state': 'open', 'user': {'login': author},
            'head': {'sha': sha, 'ref': ref, 'repo': {'full_name': head_repo}},
            'base': {'ref': base, 'repo': {'full_name': q.REPOSITORY, 'default_branch': 'main'}}}


def review(state='CHANGES_REQUESTED', login='alice', number=11, date=DATE):
    return {'id': number, 'state': state, 'user': {'login': login, 'type': 'User'}, 'body': 'please fix',
            'html_url': f'{URL}#pullrequestreview-{number}', 'submitted_at': date, 'commit_id': HEAD}


TRIGGER = {'kind': 'CHANGES_REQUESTED', 'url': REVIEW_URL, 'date': DATE, 'author': 'alice'}
BATCH = q.batch_id([TRIGGER])


class FakeGitHub:
    def __init__(self):
        self.pr = pull()
        self.reviews = [review()]
        self.inventory = [self.pr]

    def api(self, endpoint):
        if endpoint == 'user':
            return {'login': 'alchambron'}
        if endpoint.endswith('/pulls/1'):
            return self.pr
        raise AssertionError(endpoint)

    def pages(self, endpoint):
        if 'pulls?state=open' in endpoint:
            return self.inventory
        if endpoint.endswith('/reviews'):
            return self.reviews
        if endpoint.endswith('/comments'):
            return []
        raise AssertionError(endpoint)


class Contexts(unittest.TestCase):
    def enter(self, manager):
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = q.main(list(argv))
    return code, out.getvalue()


class StateMachineTests(Contexts):
    def setUp(self):
        self.state = {'jobs': {}, 'batches': {}, 'skips': []}
        self.root = Path(self.enter(tempfile.TemporaryDirectory()))/'respond-queue'

    def claim(self, phase='analysis', number=1, batch='batch', head='head', host_ready=True):
        return q.reserve(self.state, phase, number, head, batch, '/tmp/run', 'thread',
                         host_ready, state_root=self.root)

    def analyzed(self, number=1, batch='batch'):
        q.finish(self.state, self.claim('analysis', number, batch), {'status': 'analyzed'}, True)

    def waiting(self, number=1, batch='batch', head=HEAD):
        self.analyzed(number, batch)
        token = self.claim('fix', number, batch)
        q.finish(self.state, token, {'status': 'waiting_ci', 'head': head, 'commits': [head],
                                     'ci': {'state': 'pending', 'head': head}}, True)
        return self.state['batches'][f'{number}:{batch}']

    def test_caps_and_duplicates(self):
        self.analyzed(4); self.analyzed(5)
        self.claim(number=1); self.claim(number=2)
        with self.assertRaisesRegex(ValueError, 'cap'): self.claim(number=3)
        with self.assertRaisesRegex(ValueError, 'reserved'): self.claim(number=1)
        self.claim('fix', 4)
        with self.assertRaisesRegex(ValueError, 'cap'): self.claim('fix', 5)

    def test_host_unknown(self):
        with self.assertRaises(ValueError):
            self.claim('fix', host_ready=False)
        self.assertEqual(self.state['jobs'], {})

    def test_fix_requires_analysis(self):
        with self.assertRaisesRegex(ValueError, 'analysis'): self.claim('fix')
        self.assertEqual(self.state['batches'], {})

    def test_waiting_ci_finish_validation(self):
        self.analyzed()
        token = self.claim('fix')
        bad = [{'status': 'waiting_ci'},
               {'status': 'waiting_ci', 'ci': {'state': 'pending'}, 'commits': [PUSHED], 'head': PUSHED},
               {'status': 'waiting_ci', 'ci': {'state': 'pending', 'head': HEAD}, 'commits': [PUSHED], 'head': PUSHED},
               {'status': 'waiting_ci', 'ci': {'state': 'passed', 'head': PUSHED}, 'commits': [PUSHED], 'head': PUSHED},
               {'status': 'waiting_ci', 'ci': {'state': 'pending', 'head': PUSHED}, 'commits': [HEAD], 'head': PUSHED},
               {'status': 'waiting_ci', 'ci': {'state': 'pending', 'head': 'short'}, 'commits': ['short'], 'head': 'short'},
               {'status': 'completed', 'ci': {'state': 'passed', 'head': PUSHED}},
               {'status': 'analyzed'}]
        for outcome in bad:
            with self.assertRaises(ValueError, msg=outcome): q.finish(self.state, token, outcome, True)
        self.assertEqual(self.state['jobs'][token]['status'], 'active')
        with self.assertRaises(ValueError): q.finish(self.state, token, {'status': 'no_push', 'ci': {'state': 'not_required'}}, False)

    def test_pending_unknown_or_missing_ci_blocks_fix(self):
        record = self.waiting()
        self.assertEqual(record['head'], HEAD)
        with self.assertRaisesRegex(ValueError, 'CI pending'): self.claim('fix', head=HEAD)
        record['status'], record['ci'] = 'ci_settled', {'state': 'unknown', 'head': HEAD}
        with self.assertRaisesRegex(ValueError, 'exact-head'): self.claim('fix', head=HEAD)
        record['ci'] = {'state': 'missing', 'head': HEAD}
        with self.assertRaisesRegex(ValueError, 'exact-head'): self.claim('fix', head=HEAD)
        record['ci'] = {'state': 'failed'}
        with self.assertRaisesRegex(ValueError, 'exact-head'): self.claim('fix', head=HEAD)
        record['ci'] = {'state': 'failed', 'head': HEAD}
        with self.assertRaisesRegex(ValueError, 'CI-observed'): self.claim('fix', head=PUSHED)
        self.claim('fix', head=HEAD)
        self.assertEqual(record['fix_rounds'], 2)

    def test_cross_batch_gate(self):
        self.waiting(batch='first')
        self.analyzed(batch='second')
        with self.assertRaisesRegex(ValueError, 'CI pending for 1:first'): self.claim('fix', batch='second')
        q.observe_ci(self.state, 1, 'first', HEAD, {'state': 'failed', 'head': HEAD})
        self.claim('fix', batch='second', head=HEAD)

    def test_round_limit(self):
        self.analyzed()
        for _ in range(2):
            q.finish(self.state, self.claim('fix'), {'status': 'no_push', 'ci': {'state': 'not_required'}}, True)
        with self.assertRaisesRegex(ValueError, 'two fix rounds'): self.claim('fix')

    def test_observe_ci_preserves_terminal_and_exact_head(self):
        record = self.waiting()
        for ci, head in [({'state': 'pending', 'head': HEAD}, HEAD), ({'state': 'passed', 'head': PUSHED}, HEAD),
                         ({'state': 'passed', 'head': PUSHED}, PUSHED), ({'state': 'unknown', 'head': HEAD}, HEAD)]:
            with self.assertRaises(ValueError): q.observe_ci(self.state, 1, 'batch', head, ci)
        record['ci'] = {'state': 'pending', 'head': PUSHED}
        with self.assertRaises(ValueError): q.observe_ci(self.state, 1, 'batch', HEAD, {'state': 'passed', 'head': HEAD})
        record['ci'] = {'state': 'pending', 'head': HEAD}
        q.handoff(self.state, 1, 'batch', 'CI infrastructure down')
        with self.assertRaisesRegex(ValueError, 'waiting_ci'):
            q.observe_ci(self.state, 1, 'batch', HEAD, {'state': 'passed', 'head': HEAD})
        with self.assertRaisesRegex(ValueError, 'terminal'): q.handoff(self.state, 1, 'batch', 'again')
        with self.assertRaises(ValueError): q.complete(self.state, 1, 'batch', {}, lambda e: True)
        self.assertEqual(record['status'], 'handoff')
        with self.assertRaisesRegex(ValueError, 'terminal'): self.claim('analysis')

    def test_handoff_waits_for_worker_and_unknown_batch(self):
        self.claim()
        with self.assertRaisesRegex(ValueError, 'active'): q.handoff(self.state, 1, 'batch', 'x')
        with self.assertRaisesRegex(ValueError, 'unknown'): q.handoff(self.state, 2, 'batch', 'x')

    def test_analysis_never_overwrites_fix_progress(self):
        self.waiting()
        with self.assertRaisesRegex(ValueError, 'analysis not allowed'): self.claim('analysis')

    def test_token_is_durable(self):
        token = self.claim()
        self.assertEqual(self.state['jobs'][token]['token'], token)
        self.assertEqual(q.digest(self.state)['reservations'][0]['token'], token)

    def test_no_push_cannot_move_head(self):
        self.analyzed()
        token = self.claim('fix')
        with self.assertRaises(ValueError):
            q.finish(self.state, token, {'status': 'no_push', 'head': PUSHED, 'ci': {'state': 'not_required'}}, True)
        with self.assertRaises(ValueError):
            q.finish(self.state, token, {'status': 'no_push', 'commits': [PUSHED], 'ci': {'state': 'not_required'}}, True)


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.pr = pull()

    def test_human_only(self):
        q.eligible(self.pr, [review()], [self.pr])
        for login, kind in [('robot[bot]', 'User'), ('CodeRabbit', 'User'), ('copilot', 'User'), ('alice', 'Bot')]:
            row = review(); row['user'] = {'login': login, 'type': kind}
            with self.assertRaises(ValueError): q.eligible(self.pr, [row], [self.pr])

    def test_author_and_stack(self):
        other = pull(author='bob')
        for pr, stack in [(other, [other]), (self.pr, [self.pr, other]), (self.pr, [])]:
            with self.assertRaises(ValueError): q.eligible(pr, [review()], stack)

    def test_superseded_requests(self):
        later = '2026-01-02T00:00:00Z'
        for state in ('APPROVED', 'DISMISSED'):
            with self.assertRaisesRegex(ValueError, 'no current'):
                q.eligible(self.pr, [review(), review(state, number=12, date=later)], [self.pr])
        dismissed = review('DISMISSED')
        with self.assertRaisesRegex(ValueError, 'no current'): q.eligible(self.pr, [dismissed], [self.pr])
        commented = review('COMMENTED', number=12, date=later)
        self.assertEqual(len(q.eligible(self.pr, [review(), commented], [self.pr], [TRIGGER])), 1)

    def test_trigger_must_be_current(self):
        newer = review(number=12, date='2026-01-02T00:00:00Z')
        with self.assertRaisesRegex(ValueError, 'current'): q.eligible(self.pr, [review(), newer], [self.pr], [TRIGGER])
        for trigger in [dict(TRIGGER, author='bob'), dict(TRIGGER, date='2025-12-31T00:00:00Z'),
                        dict(TRIGGER, url=URL + '#pullrequestreview-99')]:
            with self.assertRaises(ValueError): q.eligible(self.pr, [review()], [self.pr], [trigger])
        with self.assertRaisesRegex(ValueError, 'trigger'):
            q.eligible(self.pr, [review()], [self.pr], [dict(TRIGGER, kind='COMMENTED')])
        bob = review(login='bob', number=13)
        stale_bob = dict(TRIGGER, url=bob['html_url'], author='bob')
        approval = review('APPROVED', login='bob', number=14, date='2026-01-03T00:00:00Z')
        with self.assertRaises(ValueError):
            q.eligible(self.pr, [review(), bob, approval], [self.pr], [TRIGGER, stale_bob])


class StackTests(unittest.TestCase):
    def numbers(self, pr, inventory):
        return [p['number'] for p in q.stack_members(pr, inventory)]

    def test_links_ancestors_and_descendants(self):
        root, middle, leaf = pull(1, 'a', 'work'), pull(2, 'b', 'a'), pull(3, 'c', 'b')
        unrelated = pull(4, 'd', 'work')
        self.assertEqual(self.numbers(middle, [root, middle, leaf, unrelated]), [1, 2, 3])

    def test_fork_and_integration_heads_do_not_join(self):
        target = pull(1, 'a', 'work')
        fork_work = pull(2, 'work', 'main', author='bob', head_repo='bob/JouleMV')
        fork_feature = pull(5, 'a', 'work', author='bob', head_repo='bob/JouleMV')
        release = pull(3, 'work', 'main', author='bob')
        main_head = pull(4, 'main', 'release', author='bob')
        sibling = pull(6, 'x', 'work')
        self.assertEqual(self.numbers(target, [target, fork_work, fork_feature, release, main_head, sibling]), [1])

    def test_duplicate_head_is_ambiguous(self):
        target, duplicate = pull(1, 'a', 'work'), pull(2, 'a', 'release/x')
        with self.assertRaisesRegex(ValueError, 'ambiguous'): q.stack_members(target, [target, duplicate])
        child = pull(3, 'c', 'a')
        with self.assertRaisesRegex(ValueError, 'ambiguous'): q.stack_members(child, [target, duplicate, child])

    def test_unknown_default_branch(self):
        pr = pull(); del pr['base']['repo']['default_branch']
        with self.assertRaisesRegex(ValueError, 'default branch'): q.stack_members(pr, [pr])


class VersionTests(Contexts):
    def folders(self, respond_all='1.0.0', respond='1.0.0'):
        root = Path(self.enter(tempfile.TemporaryDirectory()))
        for name, value in (('ra', respond_all), ('r', respond)):
            (root/name).mkdir(); (root/name/'VERSION').write_text(value + '\n')
        return root/'ra', root/'r'

    def test_ranges(self):
        q.check_versions(*self.folders())
        q.check_versions(*self.folders('1.9.9', '1.0.1'))
        for values in [('2.0.0', '1.0.0'), ('0.9.9', '1.0.0'), ('1.0', '1.0.0'), ('1.0.0', '01.0.0'),
                       ('1.0.0', 'v1.0.0'), ('1.0.0-beta', '1.0.0')]:
            with self.assertRaises(ValueError, msg=values): q.check_versions(*self.folders(*values))

    def test_malformed_requirements(self):
        good = {'minimum': '1.0.0', 'maximum_exclusive': '2.0.0'}
        for bad in [{'minimum': '1.0', 'maximum_exclusive': '2.0.0'}, {'minimum': '1.0.0'},
                    {'minimum': '2.0.0', 'maximum_exclusive': '2.0.0'}, None]:
            with self.assertRaises(ValueError, msg=bad):
                q.check_versions(*self.folders(), {'respond-all': bad, 'respond': good})

    def test_shipped_requirements_accept_repository_versions(self):
        q.check_versions(SKILLS/'respond-all', SKILLS/'respond')


class CliTests(Contexts):
    def setUp(self):
        self.root = Path(self.enter(tempfile.TemporaryDirectory()))
        self.state = self.root/'state'
        self.github = FakeGitHub()
        self.enter(mock.patch.object(q, 'api', self.github.api))
        self.enter(mock.patch.object(q, 'pages', self.github.pages))
        self.enter(mock.patch.dict(os.environ, {'RESPOND_STATE_DIR': str(self.root/'history')}))
        self.run_dir = ledger.init(self.root/'runs')
        ledger.apply(self.run_dir, [{'pr': URL, 'create': {'repository': q.REPOSITORY, 'number': 1,
                                                           'headSha': HEAD, 'untouched': [TRIGGER]}}])
        self.snapshot = self.root/'plan.json'
        self.snapshot.write_text(json.dumps({'humanFeedbackSnapshot': snapshots.build_snapshot(
            URL, 'alchambron', [], [], self.github.reviews)}))

    def claim(self, phase='analysis', head=HEAD, ready=True, batch=BATCH):
        args = ['--state', str(self.state), 'claim', '--phase', phase, '--number', '1', '--batch', batch,
                '--head', head, '--run-dir', str(self.run_dir), '--thread', 't']
        code, out = run(*args, *(['--host-ready'] if ready else []))
        return code, json.loads(out)

    def finish(self, token, outcome, cleanup=True):
        path = self.root/f'outcome-{token}.json'
        path.write_text(json.dumps(outcome))
        return run('--state', str(self.state), 'finish', '--token', token, '--outcome', str(path),
                   *(['--cleanup-verified'] if cleanup else []))[0]

    def guard(self, token, head=HEAD):
        return run('--state', str(self.state), 'guard', '--token', token, '--head', head,
                   '--snapshot', str(self.snapshot))

    def state_data(self):
        return json.loads((self.state/'state.json').read_text())

    def test_claim_failures_fail_closed(self):
        self.assertEqual(self.claim(ready=False)[0], 2)
        self.assertEqual(self.claim(head=PUSHED)[0], 2)
        self.assertEqual(self.claim(batch='other')[0], 2)
        self.assertEqual(self.claim('fix')[0], 2)
        self.github.reviews.append(review('APPROVED', number=12, date='2026-01-02T00:00:00Z'))
        code, out = self.claim()
        self.assertEqual(code, 2); self.assertIn('no current', out['reason'])
        self.assertEqual(self.state_data()['batches'], {})
        self.assertEqual(len(self.state_data()['skips']), 5)

    def test_review_queue_reservations_gate_response_claims(self):
        review_root = self.root/'review-queue'
        review_root.mkdir()
        jobs = review_root/'state.json'
        jobs.write_text(json.dumps({'jobs': {
            'parked': {'status': 'active', 'phase': 'waiting_live', 'number': 1},
        }}))
        code, out = self.claim()
        self.assertEqual(code, 2)
        self.assertIn('reserved by review-queue', out['reason'])

        jobs.write_text(json.dumps({'jobs': {
            'static1': {'status': 'active', 'phase': 'static', 'number': 2},
            'static2': {'status': 'active', 'phase': 'static', 'number': 3},
        }}))
        code, out = self.claim()
        self.assertEqual(code, 2)
        self.assertIn('shared static resource cap', out['reason'])

        jobs.write_text(json.dumps({'jobs': {
            'runtime': {'status': 'active', 'phase': 'live', 'number': 2},
        }}))
        code, out = self.claim()
        self.assertEqual(code, 0)
        self.assertEqual(self.finish(out['token'], {'status': 'analyzed'}), 0)
        code, out = self.claim('fix')
        self.assertEqual(code, 2)
        self.assertIn('shared live resource cap', out['reason'])

    def test_malformed_review_queue_state_prevents_claim(self):
        peer = self.root/'review-queue'
        peer.mkdir()
        for state in ('{', '{}', '{"jobs":{"token":{"status":"active","number":2,"phase":"unknown"}}}'):
            with self.subTest(state=state):
                (peer/'state.json').write_text(state)
                code, out = self.claim()
                self.assertEqual(code, 2)
                self.assertIn('shared slot state', out['reason'])
        self.assertEqual(self.state_data()['jobs'], {})

    def test_guard_checks_reservation_head_feedback_and_race(self):
        code, out = self.claim()
        self.assertEqual(code, 0)
        self.assertEqual(self.guard(out['token'])[0], 2)  # analysis reservations cannot mutate
        self.assertEqual(self.finish(out['token'], {'status': 'analyzed'}), 0)
        token = self.claim('fix')[1]['token']
        self.assertEqual(self.guard(token)[0], 0)
        self.assertEqual(self.guard(token, PUSHED)[0], 2)
        self.github.pr = pull(sha=PUSHED)
        self.assertEqual(self.guard(token, PUSHED)[0], 0)
        self.github.reviews[0] = dict(self.github.reviews[0], body='edited request')
        code, out = self.guard(token, PUSHED)
        self.assertEqual(code, 2); self.assertIn('feedback changed', out)
        self.github.reviews[0] = review()
        self.github.reviews.append(review('DISMISSED', number=12, date='2026-01-02T00:00:00Z'))
        self.assertEqual(self.guard(token, PUSHED)[0], 2)
        self.github.reviews.pop()

        def finished_meanwhile(*_):
            with q.locked(self.state) as state:
                state['jobs'][token]['status'] = 'finished'
            return snapshots.build_snapshot(URL, 'alchambron', [], [], self.github.reviews)
        with mock.patch.object(q, 'live_feedback', finished_meanwhile):
            code, out = self.guard(token, PUSHED)
        self.assertEqual(code, 2); self.assertIn('changed during guard', out)
        self.assertEqual(self.guard('missing')[0], 2)

    def test_guard_requires_snapshot(self):
        self.finish(self.claim()[1]['token'], {'status': 'analyzed'})
        token = self.claim('fix')[1]['token']
        self.snapshot.write_text(json.dumps({'humanFeedbackSnapshot': None}))
        self.assertEqual(self.guard(token)[0], 2)

    def push_and_settle(self, ci_state):
        self.finish(self.claim()[1]['token'], {'status': 'analyzed'})
        token = self.claim('fix')[1]['token']
        self.assertEqual(self.finish(token, {'status': 'waiting_ci', 'head': PUSHED, 'commits': [PUSHED],
                                             'replies': ['https://reply'], 'ci': {'state': 'pending', 'head': PUSHED}}), 0)
        ci = self.root/'ci.json'
        ci.write_text(json.dumps({'state': ci_state, 'head': PUSHED, 'observed_at': 'now'}))
        return run('--state', str(self.state), 'observe-ci', '--number', '1', '--batch', BATCH,
                   '--head', PUSHED, '--ci', str(ci))[0]

    def complete(self):
        return run('--state', str(self.state), 'complete', '--number', '1', '--batch', BATCH,
                   '--run-dir', str(self.run_dir))[0]

    def test_complete_requires_real_settled_ledger_on_exact_head(self):
        self.assertEqual(self.push_and_settle('passed'), 0)
        self.assertEqual(self.complete(), 2)  # ledger still on the initial head and unsettled
        ledger.apply(self.run_dir, [{'pr': URL, 'set': {'currentHeadSha': PUSHED}},
                                    {'pr': URL, 'action': {'key': 'reply', 'status': 'planned'}}])
        self.assertEqual(self.complete(), 2)  # outstanding action
        ledger.apply(self.run_dir, [{'pr': URL, 'set': {'phase': 'done', 'status': 'completed'}},
                                    {'pr': URL, 'action': {'key': 'reply', 'status': 'verified'}}])
        self.assertEqual(self.complete(), 0)
        self.assertEqual(self.state_data()['batches'][f'1:{BATCH}']['status'], 'completed')
        self.assertEqual(self.complete(), 2)
        ci = self.root/'ci.json'
        self.assertEqual(run('--state', str(self.state), 'observe-ci', '--number', '1', '--batch', BATCH,
                             '--head', PUSHED, '--ci', str(ci))[0], 2)
        self.assertEqual(self.state_data()['batches'][f'1:{BATCH}']['status'], 'completed')

    def test_failed_or_missing_ci_cannot_complete(self):
        self.assertEqual(self.push_and_settle('failed'), 0)
        ledger.apply(self.run_dir, [{'pr': URL, 'set': {'currentHeadSha': PUSHED, 'phase': 'done',
                                                         'status': 'completed'}}])
        self.assertEqual(self.complete(), 2)
        self.github.pr = pull(sha=PUSHED)
        self.assertEqual(self.claim('fix', PUSHED)[0], 0)

    def test_no_push_completion(self):
        self.finish(self.claim()[1]['token'], {'status': 'analyzed'})
        token = self.claim('fix')[1]['token']
        self.assertEqual(self.finish(token, {'status': 'no_push', 'replies': ['https://reply'],
                                             'ci': {'state': 'not_required'}}), 0)
        self.assertEqual(self.complete(), 2)
        ledger.apply(self.run_dir, [{'pr': URL, 'set': {'phase': 'done', 'status': 'completed'}}])
        self.assertEqual(self.complete(), 0)

    def test_lost_token_recovery_from_digest(self):
        self.claim()
        self.assertEqual(run('--state', str(self.state), 'digest')[0], 0)
        data = json.loads((self.state/'digest-index.json').read_text())
        token = data['reservations'][0]['token']
        self.assertIn(token, (self.state/'digest.md').read_text())
        self.assertEqual(self.finish(token, {'status': 'analyzed'}, cleanup=False), 2)
        self.assertEqual(self.finish(token, {'status': 'analyzed'}), 0)

    def plan(self, discovery):
        def collector(command, check):
            Path(command[command.index('--out') + 1]).write_text(json.dumps(discovery))
        output = self.root/'selection.json'
        with mock.patch.object(q.subprocess, 'run', collector):
            code, out = run('--state', str(self.state), 'plan', '--output', str(output))
        return code, json.loads(out), output

    def test_plan_selects_current_batches_and_skips_others(self):
        match = {'url': URL, 'number': 1, 'untouched': [TRIGGER], 'feedback': TRIGGER}
        self.assertEqual(self.plan({'complete': False, 'matches': [match]})[0], 2)
        code, out, output = self.plan({'complete': True, 'matches': [match]})
        self.assertEqual((code, out['count']), (0, 1))
        row = json.loads(output.read_text())['matches'][0]
        self.assertEqual((row['feedback_batch'], row['headSha']), (BATCH, HEAD))
        self.claim()
        code, out, output = self.plan({'complete': True, 'matches': [match]})
        self.assertEqual(out['count'], 0)
        self.assertIn('resume saved run', json.loads(output.read_text())['skips'][0]['reason'])
        self.github.reviews.append(review('APPROVED', number=12, date='2026-01-02T00:00:00Z'))
        stale = dict(match, untouched=[TRIGGER, {'kind': 'COMMENTED', 'url': 'x', 'date': DATE, 'author': 'a'}])
        code, out, output = self.plan({'complete': True, 'matches': [stale]})
        self.assertEqual(out['count'], 0)
        self.assertIn('no current', json.loads(output.read_text())['skips'][0]['reason'])

    def test_signoff_hold_records_handoff_and_plan_dedupes_hourly_skips(self):
        hold_body = "Approved from my side. I'm setting this to Request changes because it touches `src/main/sql/update-tenant.sql`."
        self.github.reviews = [dict(review(), body=hold_body)]
        match = {'url': URL, 'number': 1, 'untouched': [TRIGGER], 'feedback': TRIGGER}
        code, out, output = self.plan({'complete': True, 'matches': [match]})
        self.assertEqual((code, out['count']), (0, 0))
        record = self.state_data()['batches'][f'1:{BATCH}']
        self.assertEqual(record['status'], 'handoff')
        self.assertEqual(record['fix_rounds'], 0)
        self.assertIn('sign-off hold', record['handoff_reason'].lower())
        # Repeated hourly sweeps distinguish terminal batches and do not spam state['skips'].
        for _ in range(3):
            code, out, output = self.plan({'complete': True, 'matches': [match]})
            self.assertEqual((code, out['count']), (0, 0))
            self.assertIn('already handoff', json.loads(output.read_text())['skips'][0]['reason'])
        already_skips = [s for s in self.state_data()['skips'] if 'already handoff' in s.get('reason', '')]
        self.assertEqual(len(already_skips), 1)

    def test_signoff_hold_fails_closed_on_inline_comments_or_questions(self):
        hold_body = "Approved from my side. I'm setting this to Request changes because it touches `src/main/sql/update-tenant.sql`."
        self.assertTrue(q.is_signoff_hold([TRIGGER], [dict(review(), body=hold_body)]))
        self.assertFalse(q.is_signoff_hold([TRIGGER, {'kind': 'inline', 'url': URL + '#discussion_r1', 'date': DATE, 'author': 'alice'}],
                                           [dict(review(), body=hold_body)]))
        self.assertFalse(q.is_signoff_hold([TRIGGER], [dict(review(), body=hold_body + ' Can you also rename this?')]))
        self.assertFalse(q.is_signoff_hold([TRIGGER], [dict(review(), body='Please fix the bug.')]))

    def test_update_from_work_reservation_gates_respond_fixer_and_same_pr(self):
        updater_root = self.root/'update-from-work'
        updater_root.mkdir()
        (updater_root/'state.json').write_text(json.dumps({'jobs': {
            'upd': {'status': 'active', 'phase': 'update', 'number': 1},
        }}))
        code, out = self.claim('analysis')
        self.assertEqual(code, 2)
        self.assertIn('reserved by update-from-work', out['reason'])

        (updater_root/'state.json').write_text(json.dumps({'jobs': {
            'upd': {'status': 'active', 'phase': 'update', 'number': 99},
        }}))
        code, out = self.claim('analysis')
        self.assertEqual(code, 0)
        self.assertEqual(self.finish(out['token'], {'status': 'analyzed'}), 0)
        code, out = self.claim('fix')
        self.assertEqual(code, 2)
        self.assertIn('shared live resource cap', out['reason'])

    def test_handoff_kind_classification_and_dated_digest_retry(self):
        self.assertEqual(q.infer_handoff_kind('Pure sign-off hold; awaiting human sign-off.'), 'external_hold')
        self.assertEqual(q.infer_handoff_kind('Hosted CI is missing on the pushed head.'), 'infra_blocked')
        self.assertEqual(q.infer_handoff_kind('Migration placement requires stack decision.'), 'needs_decision')
        self.assertEqual(q.infer_handoff_kind('Any reason', 'external_hold'), 'external_hold')
        with self.assertRaises(ValueError):
            q.infer_handoff_kind('Any reason', 'bogus')

        self.finish(self.claim()[1]['token'], {'status': 'analyzed'})
        with mock.patch.object(q, 'export_dated_digest', side_effect=OSError('disk full')):
            code, _ = run('--state', str(self.state), 'handoff', '--number', '1', '--batch', BATCH,
                          '--reason', 'Actions spending limit blocked CI')
        self.assertEqual(code, 0)
        st = self.state_data()
        self.assertEqual(st['batches'][f'1:{BATCH}']['handoff_kind'], 'infra_blocked')
        self.assertEqual(len(st['pending_digest_dates']), 1)

        code, out = run('--state', str(self.state), 'digest', '--retry-pending')
        self.assertEqual(code, 0)
        retried = json.loads(out)
        self.assertEqual(retried['failures'], {})
        self.assertEqual(len(retried['exports']), 1)
        dated = json.loads(Path(retried['exports'][0]).read_text())
        self.assertEqual(len(dated['handoffs_by_kind']['infra_blocked']), 1)
        self.assertIn('Blocked by CI / infrastructure', Path(retried['exports'][0]).with_suffix('.md').read_text())
        self.assertEqual(self.state_data()['pending_digest_dates'], [])

    def test_digest_mixed_schema_atomic_and_bounded(self):
        self.state.mkdir()
        legacy = {'schema_version': 1,
                  'jobs': {'oldtoken': {'number': 7, 'phase': 'fix', 'status': 'active'}},
                  'batches': {'7:x': {'number': 7, 'status': 'waiting_ci', 'ci': {'state': 'pending'}}},
                  'skips': ['plain string'] + [{'reason': str(i)} for i in range(300)]}
        (self.state/'state.json').write_text(json.dumps(legacy))
        self.assertEqual(run('--state', str(self.state), 'digest')[0], 0)
        data = json.loads((self.state/'digest-index.json').read_text())
        self.assertEqual(data['reservations'][0]['token'], 'oldtoken')
        self.assertEqual(data['prs'][0]['fix_rounds'], 0)
        self.assertEqual(data['prs'][0]['commits'], [])
        self.assertEqual(len(data['skips']), q.SKIP_LIMIT)
        self.assertEqual(len(self.state_data()['skips']), q.SKIP_LIMIT)
        self.assertIn('token oldtoken', (self.state/'digest.md').read_text())
        self.assertEqual([p.name for p in self.state.iterdir() if p.name.startswith('.')], [])
        for _ in range(3):
            self.claim(ready=False)
        self.assertEqual(len(self.state_data()['skips']), q.SKIP_LIMIT)

    def test_digest_runs_offline(self):
        with mock.patch.object(q, 'api', side_effect=AssertionError('network')):
            self.assertEqual(run('--state', str(self.state), 'digest')[0], 0)


class SharedSlotProcessTests(Contexts):
    def test_two_queue_processes_compete_for_last_slot(self):
        root = Path(self.enter(tempfile.TemporaryDirectory()))
        review = root/'review-queue'
        q.save(review/'state.json', {'jobs': {'first': {'number': 1, 'phase': 'static', 'status': 'active'}}})
        script = '''
import fcntl
import json
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
from shared_slots import check_capacity, locked

root, phase, number = Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
original_flock = fcntl.flock

def report_contention(handle, operation):
    try:
        original_flock(handle, operation | fcntl.LOCK_NB)
    except BlockingIOError:
        print('blocked', flush=True)
        original_flock(handle, operation)
    else:
        sys.exit('expected the other queue to hold the shared lock')

if phase == 'analysis':
    fcntl.flock = report_contention

path = root / 'state.json'
with locked(root):
    jobs = json.loads(path.read_text())['jobs'] if path.exists() else {}
    try:
        check_capacity(root, root.name, phase, number, jobs.values())
    except ValueError as exc:
        sys.exit(str(exc))
    if phase == 'static':
        print('reserved', flush=True)
        if sys.stdin.readline().strip() != 'commit':
            sys.exit('parent did not release reservation')
    jobs[str(number)] = dict(number=number, phase=phase, status='active')
    path.write_text(json.dumps({'jobs': jobs}))
'''
        def start(queue, phase, number):
            process = self.enter(subprocess.Popen(
                [sys.executable, '-B', '-c', script, str(Path(__file__).resolve().parent),
                 str(queue), phase, str(number)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
            ))
            self.addCleanup(process.kill)
            return process

        def expect_signal(process, signal):
            ready, _, _ = select.select([process.stdout], [], [], 10)
            self.assertTrue(ready, f'child did not signal {signal}')
            self.assertEqual(signal, process.stdout.readline().strip())

        first = start(review, 'static', 2)
        expect_signal(first, 'reserved')
        second = start(root/'respond-queue', 'analysis', 3)
        expect_signal(second, 'blocked')
        self.assertIsNone(second.poll())
        first.stdin.write('commit\n')
        first.stdin.flush()
        _, first_error = first.communicate(timeout=10)
        _, second_error = second.communicate(timeout=10)
        self.assertEqual((first.returncode, second.returncode), (0, 1), (first_error, second_error))
        self.assertIn('shared static resource cap', second_error)


if __name__ == '__main__': unittest.main()
