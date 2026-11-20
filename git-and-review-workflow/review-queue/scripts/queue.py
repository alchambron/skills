#!/usr/bin/env python3
"""Plan directly requested JouleMV reviews and reserve shared Mac review slots."""
import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import re
import sys
import uuid

from queue_digest import digest_date, export, review_result, write_atomic

REPOSITORY = 'EnerZam/JouleMV'
LOGIN = 'alchambron'
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = Path.home() / '.codex/review-state/review-queue'
REPORT = Path('report/scenario-guide.html')
SHA = re.compile(r'[0-9a-f]{40}')


def load_slots(respond_queue):
    path = respond_queue.resolve() / 'scripts/shared_slots.py'
    spec = importlib.util.spec_from_file_location('review_queue_slots', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_review(path, respond_queue):
    sys.path.insert(0, str(path.resolve() / 'scripts'))
    import check_dependencies
    import history
    import skill_versions

    limits = skill_versions.requirements(ROOT, ('review-all', 'respond-queue'))
    for name, folder in (('review-all', path), ('respond-queue', respond_queue)):
        _, error = skill_versions.version_status(folder, limits[name])
        if error:
            raise ValueError(f'{name} at {folder}: {error}')
    skill_versions.parse_version(skill_versions.read_version(ROOT))
    result = check_dependencies.check(skill_dir=path)
    if not result['ok']:
        raise ValueError(check_dependencies.notice(result))
    return history


def now():
    return dt.datetime.now(dt.timezone.utc)


def read_state(root):
    try:
        state = json.loads((root / 'state.json').read_text())
    except FileNotFoundError:
        state = {'version': 1, 'jobs': {}}
    if state.get('version') != 1 or not isinstance(state.get('jobs'), dict):
        raise ValueError('invalid review queue state')
    state.setdefault('skips', [])
    return state


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(data, indent=2) + '\n')


def save_state(root, state):
    save_json(root / 'state.json', state)


def eligible(pr):
    if pr['state'] != 'open' or pr['draft']:
        return False
    reviewers = pr['requested_reviewers']
    if not isinstance(reviewers, list):
        raise ValueError('incomplete direct reviewer inventory')
    return (pr['base']['repo']['full_name'].casefold() == REPOSITORY.casefold()
            and LOGIN in {user['login'].casefold() for user in reviewers})


def pins(pr):
    revision = {key: pr[key]['sha'] for key in ('head', 'base')}
    if any(not isinstance(sha, str) or not SHA.fullmatch(sha) for sha in revision.values()):
        raise ValueError('missing or invalid GitHub head/base pin')
    return revision


def fetch_pr(api, number):
    pr = api.get(f'repos/{REPOSITORY}/pulls/{number}')
    if pr['number'] != number:
        raise ValueError('GitHub returned a different PR number')
    return pr


def inventory(api, number):
    if number is not None:
        return [fetch_pr(api, number)]
    rows = {}
    for page in range(1, 101):
        chunk = api.get(f'repos/{REPOSITORY}/pulls', {'state': 'open', 'per_page': 100, 'page': page})
        if not isinstance(chunk, list):
            raise ValueError('incomplete open PR inventory')
        rows.update((pr['number'], pr) for pr in chunk)
        if len(chunk) < 100:
            return rows.values()
    raise ValueError('open PR inventory exceeded 100 pages')


def plan_reviews(api, history, prior, state, number):
    selected, skipped, warnings = [], [], []
    attempts = {job['number']: (token, job) for token, job in state['jobs'].items()}
    active = {job['number'] for job in state['jobs'].values() if job['status'] == 'active'}
    for pr in inventory(api, number):
        pr_number = pr['number']
        item = {'key': history.key_of(REPOSITORY, pr_number), 'number': pr_number,
                'repository': REPOSITORY, 'url': pr['html_url']}
        if not eligible(pr):
            if number is not None:
                skipped.append({**item, 'reason': 'no current direct request on an open non-draft PR'})
            continue
        if pr_number in active:
            skipped.append({**item, 'reason': 'resume existing reservation'})
            continue
        revision = pins(pr)
        entry = prior['prs'].get(item['key'])
        kind, basis = history.classify(entry, revision['head'], False)
        if entry and entry.get('reviewedHead') and entry.get('reviewedBase') != revision['base']:
            kind, basis = 'moved', {'mode': 'full', 'previousRun': entry.get('runDir'),
                                    'reason': 'base_changed_or_unrecorded'}
        if kind == 'moved' and basis['mode'] == 'incremental':
            basis = history.carry_basis(api, REPOSITORY, entry, revision['head'], basis, warnings)
        token, last = attempts.get(pr_number, (None, {}))
        if kind == 'awaiting_decision':
            previous_run = basis.get('previousRun')
            failed = last.get('pins') == revision and last.get('result', {}).get('status') in ('blocked', 'failed')
            if previous_run and (Path(previous_run) / REPORT).is_file() and not failed:
                skipped.append({**item, **revision, 'reason': 'awaiting Alexis decision at current pins'})
                continue
        selected.append({**item, **revision, 'kind': kind, 'basis': basis, 'previous_attempt': token,
                         'title': pr.get('title'), 'updatedAt': pr.get('updated_at'),
                         'createdAt': pr.get('created_at')})
    selected.sort(key=lambda row: (history.PRIORITY[row['kind']], row['createdAt'] or '', row['number']))
    return {'login': LOGIN, 'searchComplete': True, 'selected': selected, 'skipped': skipped, 'warnings': warnings}


def recheck(api, number, expected):
    pr = fetch_pr(api, number)
    if not eligible(pr):
        raise ValueError('PR no longer directly requested, open and non-draft')
    if pins(pr) != expected:
        raise ValueError('head or base moved; replan the current revision')


def claim(root, slots, state, selection, run_dir, thread):
    number = selection['number']
    run_dir = str(run_dir.resolve())
    attempts = [(token, job) for token, job in state['jobs'].items() if job['number'] == number]
    if (attempts[-1][0] if attempts else None) != selection['previous_attempt']:
        raise ValueError('selection predates a newer attempt; replan')
    jobs = [job for job in state['jobs'].values() if job['status'] == 'active']
    slots.check_capacity(root, 'review-queue', 'static', number, jobs)
    if any(job['run_dir'] == run_dir for job in state['jobs'].values()):
        raise ValueError('start each attempt in a new run directory; resume active reservations with their token')
    token = uuid.uuid4().hex
    state['jobs'][token] = {'number': number, 'pins': {key: selection[key] for key in ('head', 'base')},
                            'run_dir': run_dir, 'thread': thread, 'phase': 'static',
                            'status': 'active', 'started_at': now().isoformat()}
    return token


def active_job(state, token):
    job = state['jobs'].get(token)
    if not job or job['status'] != 'active':
        raise ValueError('active review reservation required')
    return job


def defer(state, command, reason, number=None, token=None):
    if token:
        job = state['jobs'].get(token)
        if job and job['status'] == 'active':
            number = job['number']
            job['defer_reason'] = reason
    state['skips'].append({'at': now().isoformat(), 'command': command, 'reason': reason, 'number': number})
    state['skips'] = state['skips'][-200:]


def read_result(path, job):
    try:
        (pr,) = json.loads(path.read_text())['prs']
        if str(pr['repository']).casefold() == REPOSITORY.casefold() and pr['number'] == job['number']:
            if all(pr.get(f'{key}_sha') in (None, sha) for key, sha in job['pins'].items()):
                return pr
    except (OSError, KeyError, TypeError, ValueError):
        pass
    return {}


def run_result(job):
    run = Path(job['run_dir'])
    report = run / REPORT
    ledger = read_result(run / 'results.json', job)
    rendered = read_result(report.with_name('results.json'), job)
    if rendered and report.is_file() and report.stat().st_size:
        return ledger, rendered, str(report)
    return ledger, {}, None


def finish(state, token, status, reason, cleanup):
    job = state['jobs'].get(token)
    if not job:
        raise ValueError('unknown reservation token')
    if job.get('result'):
        return job
    if not cleanup:
        raise ValueError('verify worker and owned runtime cleanup before release')
    pr, rendered, report = run_result(job)
    verdict = pr.get('review', {}).get('status', 'pending')
    raw_live = pr.get('live', {}).get('status')
    raw_publication = pr.get('publication', {}).get('status')
    live = rendered.get('live', {}).get('display_status', raw_live)
    publication = rendered.get('publication', {}).get('display_status', raw_publication)
    if status == 'completed':
        if (not report or verdict not in ('clean', 'issues') or live not in ('complete', 'not_required')
                or publication not in ('verified', 'reused', 'not_required', 'not_requested')
                or live != raw_live or publication != raw_publication
                or rendered.get('live', {}).get('cleanup_status') == 'pending'
                or rendered.get('freshness') != 'current' or rendered.get('review', {}).get('status') != verdict
                or not Path(report).with_name('inline-report.html').is_file()
                or pr.get('current_head_sha') != job['pins']['head']
                or pr.get('current_base_sha') != job['pins']['base']):
            raise ValueError('completion requires the matching fresh review-all ledger, settled publication and runtime, and HTML')
    elif not reason or not reason.strip():
        raise ValueError('blocked and failed reviews require a reason')
    job.update(status='finished', finished_at=now().isoformat(),
               result={'status': status, 'verdict': verdict, 'html_path': report, 'reason': reason,
                       'live': live, 'publication': publication})
    job.pop('defer_reason', None)
    return job


def claim_selection(path):
    data = json.loads(path.read_text())
    (selected,) = data['selected']
    if data['login'] != LOGIN or not data['searchComplete'] or selected['repository'] != REPOSITORY:
        raise ValueError('claim requires a complete, single-PR queue selection')
    return selected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-all', type=Path, default=ROOT.parent / 'review-all')
    parser.add_argument('--respond-queue', type=Path, default=ROOT.parent / 'respond-queue')
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan')
    plan.add_argument('--number', type=int, help='Plan the PR named in a review-request event')
    plan.add_argument('--output', type=Path, required=True)
    start = commands.add_parser('claim')
    start.add_argument('--selection', type=Path, required=True)
    start.add_argument('--run-dir', type=Path, required=True)
    start.add_argument('--thread', required=True)
    start.add_argument('--host-ready', action='store_true')
    commands.add_parser('park').add_argument('--token', required=True)
    commands.add_parser('guard').add_argument('--token', required=True)
    live = commands.add_parser('claim-live')
    live.add_argument('--token', required=True)
    live.add_argument('--host-ready', action='store_true')
    release = commands.add_parser('finish')
    release.add_argument('--token', required=True)
    release.add_argument('--status', choices=('completed', 'blocked', 'failed'), required=True)
    release.add_argument('--reason')
    release.add_argument('--cleanup-verified', action='store_true')
    wait = commands.add_parser('defer')
    wait.add_argument('--number', type=int)
    wait.add_argument('--token')
    wait.add_argument('--reason', required=True)
    commands.add_parser('digest').add_argument('--date', type=dt.date.fromisoformat)
    args = parser.parse_args(argv)
    root = args.state.resolve()
    slots = None
    number = getattr(args, 'number', None)
    token = getattr(args, 'token', None)
    try:
        slots = load_slots(args.respond_queue)
        if args.command in ('finish', 'digest', 'defer', 'park'):
            with slots.locked(root):
                state = read_state(root)
                if args.command == 'digest':
                    print(export(root, state, args.date or digest_date(now())))
                    return 0
                if args.command == 'finish':
                    job = finish(state, token, args.status, args.reason, args.cleanup_verified)
                    save_state(root, state)
                    date = digest_date(dt.datetime.fromisoformat(job['finished_at']))
                    path = export(root, state, date)
                    print(json.dumps({'digest': str(path), **review_result(token, job, job['result'])}))
                    return 0
                if args.command == 'park':
                    job = active_job(state, token)
                    if job['phase'] != 'static':
                        raise ValueError('only a static reservation can wait for runtime')
                    job['phase'] = 'waiting_live'
                else:
                    defer(state, 'defer', args.reason, number, token)
                save_state(root, state)
            return 0

        history = load_review(args.review_all, args.respond_queue)
        api = history.GitHub()
        if api.get('user')['login'].casefold() != LOGIN:
            raise ValueError('authenticated account must be alchambron')
        if args.command == 'plan':
            with slots.locked(root):
                state = read_state(root)
            with history.locked() as directory:
                prior = history.read_state(directory)
            selection = plan_reviews(api, history, prior, state, number)
            save_json(args.output, selection)
            print(json.dumps({'selection': str(args.output), 'count': len(selection['selected'])}))
            return 0

        if args.command in ('claim', 'claim-live') and not args.host_ready:
            raise ValueError('Mac busy, offline or availability unknown')
        if args.command == 'claim':
            selection = claim_selection(args.selection)
            number = selection['number']
            recheck(api, number, {key: selection[key] for key in ('head', 'base')})
            with slots.locked(root):
                state = read_state(root)
                token = claim(root, slots, state, selection, args.run_dir, args.thread)
                save_state(root, state)
            print(json.dumps({'token': token}))
            return 0

        with slots.locked(root):
            reservation = active_job(read_state(root), token)
        recheck(api, reservation['number'], reservation['pins'])
        with slots.locked(root):
            state = read_state(root)
            job = active_job(state, token)
            if job != reservation:
                raise ValueError('reservation changed during GitHub check; retry')
            if args.command == 'claim-live':
                if job['phase'] != 'waiting_live':
                    raise ValueError('park the static reservation before claiming runtime')
                peers = [other for key, other in state['jobs'].items() if key != token and other['status'] == 'active']
                slots.check_capacity(root, 'review-queue', 'live', job['number'], peers)
                job['phase'] = 'live'
                job.pop('defer_reason', None)
                save_state(root, state)
            elif job['phase'] not in ('static', 'live'):
                raise ValueError('publication requires a static or live slot')
        print(json.dumps({'eligible': True, 'number': job['number'], 'phase': job['phase'], **job['pins']}))
        return 0
    except Exception as exc:  # CLI boundary, including dependency and GitHub failures.
        if slots is not None:
            try:
                with slots.locked(root):
                    state = read_state(root)
                    defer(state, args.command, str(exc), number, token)
                    save_state(root, state)
            except (OSError, ValueError, TypeError):
                pass
        print(json.dumps({'status': 'deferred', 'command': args.command, 'number': number,
                          'token': token, 'reason': str(exc)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
