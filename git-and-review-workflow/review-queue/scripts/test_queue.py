from contextlib import redirect_stdout
import copy
import importlib.util
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('review_queue', Path(__file__).with_name('queue.py'))
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)

sys.path.insert(0, str(queue.ROOT.parent / 'review-all/scripts'))
import history
import render_results


HEAD, BASE = 'a' * 40, 'b' * 40
JOULE = 'EnerZam/JouleMV'
FRONT = 'EnerZam/WebSynco_React_Front'
BACKEND = 'EnerZam/WebSynco'


def pull(number, reviewer='alchambron', head=HEAD, base=BASE, draft=False, repo=JOULE, state='open'):
    reviewers = [{'login': reviewer}] if reviewer else []
    return {
        'number': number,
        'state': state,
        'draft': draft,
        'head': {'sha': head},
        'base': {'sha': base, 'repo': {'full_name': repo}},
        'requested_reviewers': reviewers,
        'html_url': f'https://github.com/{repo}/pull/{number}',
        'title': f'Example change {repo}#{number}',
        'created_at': f'2026-11-{(number % 20) + 1:02d}T10:00:00Z',
        'updated_at': f'2026-11-{(number % 20) + 1:02d}T12:00:00Z',
    }


def search_hit(pr):
    repo = pr['base']['repo']['full_name']
    number = pr['number']
    return {
        'number': number,
        'html_url': pr['html_url'],
        'repository_url': f'https://api.github.com/repos/{repo}',
        'pull_request': {'url': f'https://api.github.com/repos/{repo}/pulls/{number}'},
    }


class GitHub:
    def __init__(self, prs):
        self.prs = {(pr['base']['repo']['full_name'], pr['number']): pr for pr in prs}
        self.requests = []
        self.search_hits = None

    def add(self, pr):
        self.prs[(pr['base']['repo']['full_name'], pr['number'])] = pr

    def get(self, endpoint, parameters=None):
        self.requests.append((endpoint, parameters))
        if endpoint == 'user':
            return {'login': 'alchambron'}
        if endpoint == 'search/issues':
            query = (parameters or {}).get('q', '')
            if self.search_hits is not None:
                hits = list(self.search_hits)
            else:
                hits = []
                for (repo, _), pr in self.prs.items():
                    if f'repo:' in query and f'repo:{repo}' not in query:
                        continue
                    if pr['state'] != 'open' or pr['draft']:
                        continue
                    if 'alchambron' in {u['login'].casefold() for u in pr['requested_reviewers']}:
                        hits.append(search_hit(pr))
            page = (parameters or {}).get('page', 1)
            per_page = (parameters or {}).get('per_page', 100)
            start = (page - 1) * per_page
            return {'total_count': len(hits), 'incomplete_results': False,
                    'items': hits[start:start + per_page]}
        # repos/OWNER/REPO/pulls/NUMBER
        parts = endpoint.split('/')
        repo = f'{parts[1]}/{parts[2]}'
        number = int(parts[4])
        return self.prs[(repo, number)]


class QueueTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.state = self.root / 'review-queue'
        self.selection = self.root / 'selection.json'
        self.run = self.root / 'run'
        self.checkouts = {}
        for repo in (JOULE, FRONT, BACKEND):
            folder = self.root / 'checkouts' / repo.replace('/', '_')
            (folder / '.git').mkdir(parents=True)
            self.checkouts[repo] = str(folder)
        self.repos_config = self.root / 'repos.json'
        queue.save_json(self.repos_config, self.checkouts)
        self.api = GitHub([pull(12)])
        self.start_patch(patch.object(queue, 'load_review', return_value=history))
        self.start_patch(patch.object(history, 'GitHub', return_value=self.api))
        self.start_patch(patch.dict('os.environ', {
            'REVIEW_STATE_DIR': str(self.root / 'review-all'),
            'REVIEW_QUEUE_REPOS_CONFIG': str(self.repos_config),
        }))

    def start_patch(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def cli(self, *args, expected=0):
        output = StringIO()
        with redirect_stdout(output):
            status = queue.main(['--state', str(self.state), *args])
        self.assertEqual(status, expected, output.getvalue())
        return output.getvalue()

    def plan(self, *args):
        stdout = self.cli('plan', '--output', str(self.selection), *args)
        self.last_plan_stdout = json.loads(stdout)
        return json.loads(self.selection.read_text())

    def claim(self, expected=0):
        output = self.cli('claim', '--selection', str(self.selection), '--run-dir', str(self.run),
                          '--thread', 'thread-link', '--host-ready', expected=expected)
        return json.loads(output)

    def ledger(self, number, live, repo=JOULE, review=None, publication=None, review_basis=None, scenarios=None):
        pr = {
            'number': number,
            'repository': repo,
            'title': f'Example change {repo}#{number}',
            'url': self.api.prs[(repo, number)]['html_url'],
            'head_sha': HEAD,
            'base_sha': BASE,
            'current_head_sha': HEAD,
            'current_base_sha': BASE,
            'review': review or {'status': 'clean', 'findings': [], 'standards': [], 'spec': [], 'limits': []},
            'publication': publication or {'status': 'not_required'},
            'live': live,
            'scenarios': scenarios if scenarios is not None else [],
        }
        if review_basis is not None:
            pr['review_basis'] = review_basis
        return {'version': 1, 'prs': [pr], 'login': 'alchambron', 'collected_at': queue.now().isoformat()}

    def render(self, data, run_dir=None):
        target = run_dir or self.run
        ledger_path = target / 'results.json'
        queue.save_json(ledger_path, data)
        render_results.render(ledger_path, target / 'report')

    def write_prior_run(self, folder, number, repo=JOULE, head=HEAD, base=BASE,
                        review_status='clean', live_status='not_required', pub_status='not_required'):
        data = {
            'version': 1,
            'login': 'alchambron',
            'collected_at': queue.now().isoformat(),
            'prs': [{
                'repository': repo,
                'number': number,
                'url': f'https://github.com/{repo}/pull/{number}',
                'title': 'Prior run',
                'head_sha': head,
                'base_sha': base,
                'current_head_sha': head,
                'current_base_sha': base,
                'review': {'status': review_status, 'findings': [], 'standards': [], 'spec': [], 'limits': []},
                'publication': {'status': pub_status,
                                **({'review_url': f'https://github.com/{repo}/pull/{number}#pullrequestreview-1'}
                                   if pub_status == 'verified' else {})},
                'live': {'status': live_status, 'reason': 'Doc only'} if live_status == 'not_required'
                else {'status': live_status, 'scope': 'pr-head-only', 'runtime_commit': head,
                      'environment': 'test', 'cleanup': {'database_dropped': True, 'servers_stopped': True,
                                                         'worktree_removed': True}},
                'scenarios': [] if live_status == 'not_required' else [{
                    'id': 's1', 'title': 'Works', 'preconditions': 'ok', 'route': '/',
                    'steps': ['open'], 'expected': 'ok', 'actual': 'ok', 'status': 'pass',
                    'screenshots': [], 'exports': [], 'cleanup': 'done',
                }],
            }],
        }
        self.render(data, run_dir=folder)

    def test_event_reads_one_pr_directly_and_holds_other_reviewers_and_drafts(self):
        self.api.add(pull(13, 'Kamyar-R'))
        self.api.add(pull(14, draft=True))
        selection = self.plan('--number', '12')
        self.assertEqual([12], [row['number'] for row in selection['selected']])
        self.assertEqual(self.checkouts[JOULE], selection['selected'][0]['checkout'])
        self.assertEqual(['user', f'repos/{JOULE}/pulls/12'], [call[0] for call in self.api.requests])
        for number, expected_msg in (('13', 'no current direct request'), ('14', 'draft PR')):
            result = self.plan('--number', number)
            self.assertFalse(result['selected'])
            self.assertIn(expected_msg, result['skipped'][0]['reason'])

    def test_multi_repo_search_discovers_all_repos_and_skips_missing_checkout_without_cloning(self):
        missing_repo = 'EnerZam/Uncloned_Repo'
        self.api.prs = {}
        self.api.add(pull(1892, repo=JOULE))
        self.api.add(pull(132, repo=FRONT))
        self.api.add(pull(131, repo=FRONT))
        self.api.add(pull(80, repo=BACKEND))
        self.api.add(pull(9, repo=missing_repo))
        result = self.plan()
        selected_keys = {row['key'] for row in result['selected']}
        self.assertEqual(
            {f'{JOULE}#1892', f'{FRONT}#132', f'{FRONT}#131', f'{BACKEND}#80'},
            selected_keys,
        )
        by_key = {row['key']: row for row in result['selected']}
        self.assertEqual(self.checkouts[FRONT], by_key[f'{FRONT}#132']['checkout'])
        self.assertIn('EnerZam/WebSynco_React_Front/pr-132-', by_key[f'{FRONT}#132']['run_dir_hint'])
        # Missing checkout is reported once in plan output and digest, never cloned.
        self.assertEqual(1, len(result['skipped']))
        self.assertEqual(f'{missing_repo}#9', result['skipped'][0]['key'])
        self.assertIn('no local checkout configured', result['skipped'][0]['reason'])
        self.assertIn('skipping without cloning', result['skipped'][0]['reason'])
        self.assertEqual(1, self.last_plan_stdout['skipped_count'])
        search_calls = [params['q'] for ep, params in self.api.requests if ep == 'search/issues']
        self.assertEqual(['is:pr is:open draft:false user-review-requested:alchambron'], search_calls)

        # Running plan again does not duplicate the skip in state or digest.
        self.plan()
        state = queue.read_state(self.state)
        plan_skips = [s for s in state['skips'] if s.get('pr_key') == f'{missing_repo}#9']
        self.assertEqual(1, len(plan_skips))
        today = queue.digest_date(queue.now()).isoformat()
        digest_data = json.loads((self.state / f'digest/{today}.json').read_text())
        self.assertEqual(1, len([s for s in digest_data['skips'] if s.get('pr_key') == f'{missing_repo}#9']))

    def test_t3_projects_list_resolves_checkouts_and_reports_missing_git_dir(self):
        t3_file = self.root / 't3-projects.json'
        broken_dir = self.root / 'not-a-git-repo'
        broken_dir.mkdir()
        queue.save_json(t3_file, {
            'projects': [
                {
                    'title': 'WebSynco_React_Front',
                    'workspaceRoot': self.checkouts[FRONT],
                    'deletedAt': None,
                    'archivedAt': None,
                    'repositoryIdentity': {'displayName': FRONT},
                },
                {
                    'title': 'BrokenRepo',
                    'workspaceRoot': str(broken_dir),
                    'deletedAt': None,
                    'archivedAt': None,
                    'repositoryIdentity': {'owner': 'EnerZam', 'name': 'BrokenRepo'},
                },
            ],
        })
        self.api.prs = {}
        self.api.add(pull(132, repo=FRONT))
        self.api.add(pull(5, repo='EnerZam/BrokenRepo'))
        with patch.dict('os.environ', {'REVIEW_QUEUE_REPOS_CONFIG': ''}):
            result = self.plan('--t3-projects', str(t3_file))
        self.assertEqual([f'{FRONT}#132'], [row['key'] for row in result['selected']])
        self.assertEqual([f'EnerZam/BrokenRepo#5'], [row['key'] for row in result['skipped']])
        self.assertIn('missing or not a git repository', result['skipped'][0]['reason'])

    def test_state_migration_is_idempotent_and_preserves_legacy_history(self):
        legacy = {
            'version': 1,
            'jobs': {
                'tok-1844': {
                    'number': 1844,
                    'pins': {'head': HEAD, 'base': BASE},
                    'run_dir': '/legacy/runs/pr-1844.abc',
                    'thread': 't3://1844',
                    'phase': 'live',
                    'status': 'finished',
                    'started_at': '2026-11-24T15:23:35+00:00',
                    'finished_at': '2026-11-24T15:41:56+00:00',
                    'result': {
                        'status': 'completed',
                        'verdict': 'clean',
                        'html_path': '/legacy/runs/pr-1844.abc/report/scenario-guide.html',
                        'reason': None,
                        'live': 'complete',
                        'publication': 'not_required',
                    },
                },
                'tok-old-no-result': {
                    'number': 1601,
                    'pins': {'head': 'c' * 40, 'base': BASE},
                    'run_dir': '/legacy/arrival-1601',
                    'thread': 't3://1601',
                    'phase': 'static',
                    'status': 'finished',
                    'started_at': '2026-11-18T22:15:08+00:00',
                    'finished_at': '2026-11-18T22:28:23+00:00',
                },
            },
            'prs': {
                '1844': {'note': 'legacy bare key'},
            },
            'skips': [
                {'at': '2026-11-23T19:36:46+00:00', 'command': 'claim', 'reason': 'cap reached', 'number': 1721},
            ],
            'pending_digest_dates': [],
        }
        queue.save_json(self.state / 'state.json', legacy)
        first = json.loads(self.cli('migrate'))
        migrated_once = json.loads((self.state / 'state.json').read_text())
        second = json.loads(self.cli('migrate'))
        migrated_twice = json.loads((self.state / 'state.json').read_text())

        self.assertEqual(first, second)
        self.assertEqual(migrated_once, migrated_twice)
        self.assertEqual(2, migrated_once['version'])
        self.assertEqual(JOULE, migrated_once['jobs']['tok-1844']['repository'])
        self.assertEqual(f'{JOULE}#1844', migrated_once['jobs']['tok-1844']['pr_key'])
        self.assertEqual(f'{JOULE}#1844 review ready, check before approving',
                         migrated_once['jobs']['tok-1844']['result']['ping'])
        self.assertIn('tok-old-no-result', migrated_once['jobs'])
        self.assertIn(f'{JOULE}#1844', migrated_once['prs'])
        self.assertNotIn('1844', migrated_once['prs'])
        self.assertEqual('legacy bare key', migrated_once['prs'][f'{JOULE}#1844']['note'])
        self.assertEqual(f'{JOULE}#1721', migrated_once['skips'][0]['pr_key'])

    def test_1892_bug_no_review_at_head_or_unpinged_review_is_selected_not_silently_skipped(self):
        # Case A: review-all history claims complete at head, but no finished review ledger exists at that head.
        fake_run = self.root / 'fake-run-no-ledger'
        queue.save_json(fake_run / queue.REPORT, {'stub': True})
        history.save_json(history.state_dir() / 'state.json', {'prs': {
            f'{JOULE}#1892': {
                'reviewedHead': HEAD, 'reviewedBase': BASE, 'complete': True,
                'outcome': 'clean', 'publication': 'not_required', 'runDir': str(fake_run),
            },
        }})
        self.api.prs = {}
        self.api.add(pull(1892, repo=JOULE))
        result_a = self.plan()
        self.assertEqual([f'{JOULE}#1892'], [row['key'] for row in result_a['selected']])
        self.assertEqual('new', result_a['selected'][0]['kind'])
        self.assertEqual('full', result_a['selected'][0]['basis']['mode'])
        self.assertEqual([], result_a['skipped'])

        # Case B: Exact 2026-11-24 #1892 case — review-all history has a finished review ledger at exact head/base
        # from an external batch run, but review-queue NEVER ran it and NEVER pinged Alexis.
        real_prior = self.root / 'prior-batch-1892'
        self.write_prior_run(real_prior, 1892, repo=JOULE, head=HEAD, base=BASE)
        history.save_json(history.state_dir() / 'state.json', {'prs': {
            f'{JOULE}#1892': {
                'reviewedHead': HEAD, 'reviewedBase': BASE, 'complete': True,
                'outcome': 'clean', 'publication': 'not_required', 'runDir': str(real_prior),
            },
        }})
        result_b = self.plan()
        self.assertEqual([f'{JOULE}#1892'], [row['key'] for row in result_b['selected']])
        self.assertEqual('not_pinged_at_current_pins', result_b['selected'][0]['basis']['reason'])
        self.assertEqual([], result_b['skipped'])

        # Case C: Once review-queue finishes and emits a ping at exact head/base, PR IS skipped as awaiting decision
        # and that skip is reported once in both plan output and digest.
        token = self.claim()['token']
        self.render(self.ledger(1892, {'status': 'not_required', 'reason': 'Doc only'}, repo=JOULE))
        self.cli('finish', '--token', token, '--status', 'completed', '--cleanup-verified')
        result_c = self.plan()
        self.assertEqual([], result_c['selected'])
        self.assertEqual(1, len(result_c['skipped']))
        self.assertEqual(f'{JOULE}#1892', result_c['skipped'][0]['key'])
        self.assertEqual('awaiting Alexis decision at current pins', result_c['skipped'][0]['reason'])
        # Repeat sweep: skip stays deduplicated (reported once in digest).
        self.plan()
        today = queue.digest_date(queue.now()).isoformat()
        digest_skips = json.loads((self.state / f'digest/{today}.json').read_text())['skips']
        matching = [s for s in digest_skips if s.get('pr_key') == f'{JOULE}#1892']
        self.assertEqual(1, len(matching))

    def test_rerequest_at_same_head_cannot_resolve_silently_as_reused(self):
        # Prior run published verified REQUEST_CHANGES at HEAD/BASE; author re-requested at exact same HEAD.
        prior_run = self.root / 'prior-1601'
        self.write_prior_run(prior_run, 1601, repo=JOULE, head=HEAD, base=BASE,
                             review_status='issues', live_status='not_required', pub_status='verified')
        history.save_json(history.state_dir() / 'state.json', {'prs': {
            f'{JOULE}#1601': {
                'reviewedHead': HEAD, 'reviewedBase': BASE, 'complete': True,
                'outcome': 'issues', 'publication': 'verified', 'runDir': str(prior_run),
            },
        }})
        self.api.prs = {}
        self.api.add(pull(1601, repo=JOULE))
        selection = self.plan()
        self.assertEqual([f'{JOULE}#1601'], [row['key'] for row in selection['selected']])
        self.assertEqual('rerequested', selection['selected'][0]['kind'])
        self.assertEqual('discussion', selection['selected'][0]['basis']['mode'])

        token = self.claim()['token']
        reused_ledger = self.ledger(
            1601,
            {'status': 'not_required', 'reason': 'Static findings remain'},
            repo=JOULE,
            review={'status': 'issues', 'findings': [{
                'severity': 'P2', 'title': 'Numeric export', 'body': 'Still string',
                'path': 'DataTable.tsx', 'line': 8, 'side': 'RIGHT', 'origin': 'static',
            }], 'standards': [], 'spec': [], 'limits': []},
            publication={
                'status': 'reused',
                'reused_threads': [f'https://github.com/{JOULE}/pull/1601#discussion_r1'],
            },
            review_basis={'mode': 'discussion', 'kind': 'rerequested'},
        )
        self.render(reused_ledger)

        # Silent publication: reused MUST fail closed.
        rejected = json.loads(self.cli('finish', '--token', token, '--status', 'completed',
                                       '--cleanup-verified', expected=2))
        self.assertIn('cannot resolve silently as publication: reused', rejected['reason'])

        # --ping-alexis without --reason is also rejected.
        self.cli('finish', '--token', token, '--status', 'completed', '--cleanup-verified',
                 '--ping-alexis', expected=2)

        # Passing --ping-alexis with a concrete reason succeeds and emits an explicit Alexis ping.
        finished = json.loads(self.cli(
            'finish', '--token', token, '--status', 'completed', '--cleanup-verified',
            '--ping-alexis', '--reason', 'Author pushed no new commits; existing threads still stand',
        ))
        self.assertEqual(f'{JOULE}#1601 re-requested at reviewed head, needs Alexis decision', finished['ping'])
        self.assertTrue(finished['ping_alexis'])

        # Alternative path: posting a fresh review (publication: verified) succeeds without --ping-alexis.
        self.run = self.root / 'run-fresh'
        self.api.prs = {}
        self.api.add(pull(1602, repo=FRONT))
        prior_front = self.root / 'prior-front-1602'
        self.write_prior_run(prior_front, 1602, repo=FRONT, head=HEAD, base=BASE,
                             review_status='issues', live_status='not_required', pub_status='verified')
        history.save_json(history.state_dir() / 'state.json', {'prs': {
            f'{FRONT}#1602': {
                'reviewedHead': HEAD, 'reviewedBase': BASE, 'complete': True,
                'outcome': 'issues', 'publication': 'verified', 'runDir': str(prior_front),
            },
        }})
        self.plan('--repo', FRONT, '--number', '1602')
        tok2 = self.claim()['token']
        fresh_ledger = self.ledger(
            1602,
            {'status': 'not_required', 'reason': 'Static findings published'},
            repo=FRONT,
            review={'status': 'issues', 'findings': [{
                'severity': 'P2', 'title': 'Fix null', 'body': 'Null crash',
                'path': 'src/App.tsx', 'line': 12, 'side': 'RIGHT', 'origin': 'static',
            }], 'standards': [], 'spec': [], 'limits': []},
            publication={
                'status': 'verified',
                'review_url': f'https://github.com/{FRONT}/pull/1602#pullrequestreview-999',
            },
            review_basis={'mode': 'discussion', 'kind': 'rerequested'},
        )
        self.render(fresh_ledger)
        fresh_done = json.loads(self.cli('finish', '--token', tok2, '--status', 'completed', '--cleanup-verified'))
        self.assertEqual(f'{FRONT}#1602 changes requested, see report', fresh_done['ping'])
        self.assertEqual(f'https://github.com/{FRONT}/pull/1602#pullrequestreview-999', fresh_done['review_url'])

    def test_cross_repo_same_pr_number_isolation_and_combined_concurrency_caps(self):
        self.api.prs = {}
        self.api.add(pull(12, repo=JOULE))
        self.api.add(pull(12, repo=FRONT))
        self.api.add(pull(12, repo=BACKEND))

        self.plan('--repo', JOULE, '--number', '12')
        self.run = self.root / 'run-joule-12'
        tok1 = self.claim()['token']

        # Same PR number (#12) in WebSynco_React_Front is distinct and takes the 2nd static slot.
        self.plan('--repo', FRONT, '--number', '12')
        self.run = self.root / 'run-front-12'
        tok2 = self.claim()['token']

        # 3rd static slot across repos is blocked by combined cap of 2.
        self.plan('--repo', BACKEND, '--number', '12')
        self.run = self.root / 'run-backend-12'
        self.assertIn('shared static resource cap', self.claim(expected=2)['reason'])

        # Park both and verify live cap of 1 applies across repos combined.
        self.cli('park', '--token', tok1)
        self.cli('park', '--token', tok2)
        self.cli('claim-live', '--token', tok1, '--host-ready')
        self.assertIn('shared live resource cap', self.cli('claim-live', '--token', tok2, '--host-ready', expected=2))

    def test_sweep_pages_and_queues_missed_changed_and_failed_runs(self):
        self.api.prs = {}
        for number in range(1, 102):
            self.api.add(pull(number, repo=JOULE))
        self.api.add(pull(102, repo=JOULE))
        self.api.add(pull(103, head='c' * 40, repo=JOULE))
        self.api.add(pull(104, repo=JOULE))
        original = self.root / 'review-all-run'
        self.write_prior_run(original, 102, repo=JOULE, head=HEAD, base=BASE)
        prior_102 = {'reviewedHead': HEAD, 'reviewedBase': BASE, 'complete': True, 'runDir': str(original)}
        prior_103 = {'reviewedHead': HEAD, 'reviewedBase': BASE, 'complete': True, 'runDir': str(original)}
        prior_104 = {'reviewedHead': HEAD, 'reviewedBase': BASE, 'complete': True, 'runDir': str(original)}
        history.save_json(history.state_dir() / 'state.json', {'prs': {
            history.key_of(JOULE, 102): prior_102,
            history.key_of(JOULE, 103): prior_103,
            history.key_of(JOULE, 104): prior_104,
        }})
        queue.save_state(self.state, {'version': 2, 'jobs': {
            'done-102': {
                'repository': JOULE, 'number': 102, 'pr_key': f'{JOULE}#102', 'status': 'finished',
                'pins': {'head': HEAD, 'base': BASE}, 'run_dir': str(original), 'thread': 't',
                'phase': 'static', 'finished_at': queue.now().isoformat(),
                'result': {'status': 'completed', 'verdict': 'clean', 'html_path': str(original / queue.REPORT),
                           'ping': f'{JOULE}#102 review ready, check before approving'},
            },
            'failed': {
                'repository': JOULE, 'number': 104, 'pr_key': f'{JOULE}#104', 'status': 'finished',
                'pins': {'head': HEAD, 'base': BASE}, 'run_dir': '/tmp/f', 'thread': 't',
                'phase': 'static', 'finished_at': queue.now().isoformat(),
                'result': {'status': 'failed', 'verdict': 'pending', 'html_path': None},
            },
        }})
        with patch.object(history, 'carry_basis', side_effect=lambda api, name, entry, head, basis, warnings: basis):
            result = self.plan()
        selected_kinds = {row['number']: row['kind'] for row in result['selected']}
        self.assertEqual('new', selected_kinds[101])
        self.assertEqual('moved', selected_kinds[103])
        self.assertEqual('new', selected_kinds[104])
        self.assertEqual(102, result['skipped'][0]['number'])
        search_pages = [params['page'] for ep, params in self.api.requests if ep == 'search/issues']
        self.assertEqual([1, 2], search_pages)

    def test_park_releases_static_capacity_and_waits_for_shared_fixer(self):
        self.plan('--number', '12')
        token = self.claim()['token']
        self.assertFalse(self.plan('--number', '12')['selected'])
        self.cli('park', '--token', token)
        respond = self.root / 'respond-queue/state.json'
        queue.save_json(respond, {'jobs': {'fixer': {'status': 'active', 'phase': 'fix', 'number': 99}}})
        self.assertIn('shared live resource cap', self.cli('claim-live', '--token', token, '--host-ready', expected=2))
        job = queue.read_state(self.state)['jobs'][token]
        self.assertEqual('waiting_live', job['phase'])
        self.assertIn('shared live resource cap', job['defer_reason'])
        self.api.add(pull(13))
        self.plan('--number', '13')
        self.run = self.root / 'other-run'
        self.assertIn('token', self.claim())
        respond.unlink()
        self.cli('claim-live', '--token', token, '--host-ready')
        self.api.prs[(JOULE, 12)]['requested_reviewers'] = [{'login': 'Kamyar-R'}]
        self.assertIn('no longer directly requested', self.cli('guard', '--token', token, expected=2))

    def test_update_from_work_live_slot_gates_review_queue_live_and_same_pr(self):
        updater = self.root / 'update-from-work/state.json'
        queue.save_json(updater, {'jobs': {'upd': {'status': 'active', 'phase': 'update', 'number': 12}}})
        self.plan('--number', '12')
        self.assertIn('reserved by update-from-work', self.claim(expected=2)['reason'])

        queue.save_json(updater, {'jobs': {'upd': {'status': 'active', 'phase': 'update', 'number': 99}}})
        token = self.claim()['token']
        self.cli('park', '--token', token)
        self.assertIn('shared live resource cap', self.cli('claim-live', '--token', token, '--host-ready', expected=2))
        updater.unlink()
        self.cli('claim-live', '--token', token, '--host-ready')

    def test_changes_in_pins_and_stale_attempts_require_replanning(self):
        self.plan('--number', '12')
        self.api.prs[(JOULE, 12)]['head']['sha'] = 'c' * 40
        self.assertIn('head or base moved', self.claim(expected=2)['reason'])
        self.plan('--number', '12')
        token = self.claim()['token']
        self.cli('finish', '--token', token, '--status', 'failed', '--reason', 'worker exited',
                 '--cleanup-verified')
        self.assertIn('selection predates a newer attempt', self.claim(expected=2)['reason'])
        self.assertEqual('c' * 40, self.plan('--number', '12')['selected'][0]['head'])

    def test_finish_requires_cleaned_up_worker_and_renderer_validated_evidence_and_renders_summary(self):
        self.plan('--number', '12')
        token = self.claim()['token']
        args = ['finish', '--token', token, '--status', 'completed']
        self.cli(*args, expected=2)
        self.cli(*args, '--cleanup-verified', expected=2)
        data = self.ledger(12, {'status': 'complete'})
        self.render(data)
        self.cli(*args, '--cleanup-verified', expected=2)
        data['prs'][0]['live'] = {'status': 'not_required', 'reason': 'Documentation-only change'}
        self.render(data)
        self.api.prs[(JOULE, 12)]['requested_reviewers'] = []
        result = json.loads(self.cli(*args, '--cleanup-verified'))
        written = json.loads(Path(result['digest']).read_text())['reviews'][0]
        self.assertEqual((JOULE, f'{JOULE}#12', 12, HEAD, 'completed', str(self.run / queue.REPORT)),
                         (written['repository'], written['pr_key'], written['pr'],
                          written['head_sha'], written['outcome'], written['html_path']))
        self.assertEqual(str(self.run / queue.SUMMARY), written['summary_path'])
        self.assertEqual(f'{JOULE}#12 review ready, check before approving', written['ping'])

        # Coordinator inline summary command prints rendered Markdown.
        inline_md = self.cli('summary', '--token', token)
        self.assertIn(f'{JOULE}#12', inline_md)
        self.assertIn('Documentation-only change', inline_md)
        self.assertIn(str(self.run / queue.REPORT), inline_md)

        with patch.object(queue, 'load_review', side_effect=AssertionError('offline')):
            repeat = json.loads(self.cli(*args, '--cleanup-verified'))
        self.assertEqual(written['finished_at'], repeat['finished_at'])

    def test_invalid_state_returns_deferred_without_overwriting(self):
        self.state.mkdir()
        path = self.state / 'state.json'
        path.write_text('[]\n')
        result = json.loads(self.cli('digest', expected=2))
        self.assertEqual('deferred', result['status'])
        self.assertIn('invalid review queue state', result['reason'])
        self.assertEqual('[]\n', path.read_text())

    def test_malformed_evidence_blocks_completion_but_releases_failed_job_offline(self):
        self.plan('--number', '12')
        token = self.claim()['token']
        data = self.ledger(12, {'status': 'not_required'})
        self.render(data)
        data['prs'][0].update(review=None, live=[], publication={'status': []})
        queue.save_json(self.run / 'results.json', data)
        rendered = self.run / queue.REPORT.with_name('results.json')
        report_data = json.loads(rendered.read_text())
        report_data['prs'][0].update(live=None, publication=[])
        queue.save_json(rendered, report_data)
        args = ['finish', '--token', token, '--cleanup-verified', '--status']
        self.cli(*args, 'completed', expected=2)
        with patch.object(queue, 'load_review', side_effect=AssertionError('offline')):
            result = json.loads(self.cli(*args, 'failed', '--reason', 'worker exited'))
        self.assertEqual(('failed', 'pending'), (result['outcome'], result['verdict']))
        self.assertIsNone(result['live'])
        self.assertIsNone(result['publication'])
        self.assertEqual('finished', queue.read_state(self.state)['jobs'][token]['status'])

    def test_finish_reports_stale_head_or_base_as_blocked(self):
        for number, revision in ((12, 'head'), (13, 'base')):
            with self.subTest(revision=revision):
                self.api.add(pull(number))
                self.run = self.root / f'run-{number}'
                self.plan('--number', str(number))
                token = self.claim()['token']
                self.render(self.ledger(number, {'status': 'not_required'}))
                self.api.prs[(JOULE, number)][revision]['sha'] = 'c' * 40
                result = json.loads(self.cli('finish', '--token', token, '--status', 'completed', '--cleanup-verified'))
                self.assertEqual(('blocked', HEAD, BASE), (result['outcome'], result['head_sha'], result['base_sha']))
                self.assertEqual(f'{JOULE}#{number} review blocked, see report', result['ping'])
                self.assertEqual('finished', queue.read_state(self.state)['jobs'][token]['status'])

    def test_pending_digest_is_retried_offline_for_original_toronto_date(self):
        self.plan('--number', '12')
        token = self.claim()['token']
        before_midnight = queue.dt.datetime.fromisoformat('2026-11-20T04:59:59+00:00')
        with patch.object(queue, 'now', return_value=before_midnight), patch.object(queue, 'export', side_effect=OSError('disk full')):
            self.cli('finish', '--token', token, '--status', 'failed', '--reason', 'worker exited',
                     '--cleanup-verified', expected=2)
        state = queue.read_state(self.state)
        self.assertEqual('finished', state['jobs'][token]['status'])
        self.assertEqual(['2026-11-19'], state['pending_digest_dates'])
        after_midnight = queue.dt.datetime.fromisoformat('2026-11-20T05:00:01+00:00')
        with patch.object(queue, 'now', return_value=after_midnight), patch.object(queue, 'load_review', side_effect=AssertionError('offline')):
            result = json.loads(self.cli('digest', '--retry-pending'))
            self.assertEqual([], json.loads(self.cli('digest', '--retry-pending'))['exports'])
        path = self.state / 'digest/2026-11-19.json'
        self.assertEqual({'exports': [str(path)], 'failures': {}}, result)
        self.assertEqual(before_midnight.isoformat(), json.loads(path.read_text())['reviews'][0]['finished_at'])


if __name__ == '__main__':
    unittest.main()
