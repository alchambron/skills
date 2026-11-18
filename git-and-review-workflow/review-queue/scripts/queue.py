#!/usr/bin/env python3
"""Read-only GitHub queue planning and durable, fail-closed cross-thread reservations.

Reservations never expire: interrupted jobs require verified cleanup before release.
No command launches a thread, runtime or GitHub mutation.
"""
import argparse
from contextlib import contextmanager
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import sys
import uuid
from zoneinfo import ZoneInfo

REPOSITORY = 'EnerZam/JouleMV'
LOGIN = 'alchambron'
ROOT = Path(__file__).resolve().parents[1]


def load_review(path):
    sys.path.insert(0, str(Path(path).resolve() / 'scripts'))
    import check_dependencies
    import history
    import skill_versions
    limits = skill_versions.requirements(ROOT, ['review-all'])['review-all']
    _, error = skill_versions.version_status(path, limits)
    if error:
        raise ValueError(error)
    skill_versions.parse_version(skill_versions.read_version(ROOT))
    result = check_dependencies.check(skill_dir=path)
    if not result['ok']:
        raise ValueError(check_dependencies.notice(result))
    return history


def eligible(pr):
    return (pr.get('state') == 'open' and not pr.get('draft') and
            pr.get('base', {}).get('repo', {}).get('full_name', '').casefold() == REPOSITORY.casefold() and
            LOGIN in {u.get('login', '').casefold() for u in pr.get('requested_reviewers', [])})


def overnight(now, receipt):
    local = now.astimezone(ZoneInfo('America/Toronto'))
    day = local.date() if local.hour >= 22 else local.date() - dt.timedelta(days=1)
    start = dt.datetime.combine(day, dt.time(22), ZoneInfo('America/Toronto'))
    deadline = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(9), ZoneInfo('America/Toronto'))
    if not start <= local < deadline:
        raise ValueError('outside 22:00–09:00 Toronto window')
    if receipt.get('repository') != REPOSITORY or receipt.get('status') != 'completed':
        raise ValueError('update-from-work completion receipt missing')
    began = dt.datetime.fromisoformat(receipt['started_at'])
    ended = dt.datetime.fromisoformat(receipt['completed_at'])
    if not start <= began <= ended <= now:
        raise ValueError('update-from-work receipt is stale or invalid')
    return deadline.isoformat()


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'queue.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        state = json.loads((root / 'state.json').read_text()) if (root / 'state.json').exists() else {'version': 1, 'jobs': {}}
        yield state
        temporary = root / 'state.tmp'
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.replace(root / 'state.json')


def reserve(state, phase, number, pins, run_dir, thread):
    active = [j for j in state['jobs'].values() if j['status'] == 'active']
    if any(j['number'] == number for j in active):
        raise ValueError('PR already active')
    if sum(j['phase'] == phase for j in active) >= {'static': 2, 'live': 1}[phase]:
        raise ValueError('resource cap reached')
    token = uuid.uuid4().hex
    state['jobs'][token] = dict(number=number, phase=phase, pins=pins, run_dir=str(run_dir), thread=thread,
                                status='active', started_at=dt.datetime.now(dt.timezone.utc).isoformat())
    return token


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review-all', required=True, type=Path)
    p.add_argument('--state', type=Path, default=Path.home()/'.codex/review-state/review-queue')
    sub = p.add_subparsers(dest='command', required=True)
    plan = sub.add_parser('plan'); plan.add_argument('--output', required=True, type=Path)
    claim = sub.add_parser('claim')
    claim.add_argument('--phase', choices=['static', 'live'], required=True)
    claim.add_argument('--number', type=int, required=True)
    claim.add_argument('--run-dir', type=Path, required=True)
    claim.add_argument('--thread', required=True)
    claim.add_argument('--host-ready', action='store_true', help='Coordinator verified host idle, available and no unrelated runtime')
    claim.add_argument('--update-receipt', type=Path)
    finish = sub.add_parser('finish'); finish.add_argument('--token', required=True)
    finish.add_argument('--cleanup-verified', action='store_true')
    digest = sub.add_parser('digest'); digest.add_argument('--output', required=True, type=Path)
    args = p.parse_args(argv)
    try:
        history = load_review(args.review_all)
        api = history.GitHub()
        if api.get('user').get('login', '').casefold() != LOGIN:
            raise ValueError('authenticated account must be alchambron')
        if args.command == 'plan':
            with history.locked() as directory:
                prior = history.read_state(directory)
            result = history.select(api, prior, [REPOSITORY], False, args.state/'search')
            if not result['searchComplete']:
                raise ValueError('incomplete inventory; no dispatch')
            result['selected'] = [c for c in result['selected'] if eligible(api.get(f"repos/{REPOSITORY}/pulls/{c['number']}"))]
            history.save_json(args.output, result)
            print(json.dumps({'selection': str(args.output), 'count': len(result['selected'])}))
        elif args.command == 'claim':
            if not args.host_ready:
                raise ValueError('host busy or availability unknown')
            pr = api.get(f'repos/{REPOSITORY}/pulls/{args.number}')
            if not eligible(pr):
                raise ValueError('PR no longer eligible')
            if args.phase == 'live':
                overnight(dt.datetime.now(dt.timezone.utc), json.loads(args.update_receipt.read_text()) if args.update_receipt else {})
                ledger = json.loads((args.run_dir/'results.json').read_text())
                rows = [r for r in ledger['prs'] if r['repository'] == REPOSITORY and r['number'] == args.number]
                if len(rows) != 1 or rows[0]['review']['status'] != 'clean':
                    raise ValueError('live queue requires clean static review')
                if rows[0]['head_sha'] != pr['head']['sha'] or rows[0]['base_sha'] != pr['base']['sha']:
                    raise ValueError('pins moved; static refresh required')
            with locked(args.state) as state:
                token = reserve(state, args.phase, args.number, {'head': pr['head']['sha'], 'base': pr['base']['sha']}, args.run_dir, args.thread)
            print(json.dumps({'token': token}))
        elif args.command == 'finish':
            with locked(args.state) as state:
                job = state['jobs'][args.token]
                if job['phase'] == 'live' and not args.cleanup_verified:
                    raise ValueError('retain live reservation until cleanup verified')
                job['status'] = 'finished'
                job['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        else:
            with locked(args.state) as state:
                jobs = dict(state['jobs'])
            rows = []
            for token, job in jobs.items():
                run = Path(job['run_dir'])
                row = {**job, 'token': token, 'ledger': str(run/'results.json'),
                       'report': str(run/'report/scenario-guide.html')}
                try:
                    data = json.loads((run/'results.json').read_text())
                    targets = [pr for pr in data['prs'] if pr['repository'] == REPOSITORY and pr['number'] == job['number']]
                    if len(targets) != 1:
                        raise ValueError('missing unique PR ledger entry')
                    pr = targets[0]
                    row['outcome'] = {key: pr.get(key) for key in
                                      ('url', 'head_sha', 'base_sha', 'current_head_sha', 'current_base_sha',
                                       'review_request', 'review', 'publication', 'live', 'coverage_gaps', 'scenarios')}
                    row['report_exists'] = (run/'report/scenario-guide.html').is_file()
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    row['error'] = str(exc)
                rows.append(row)
            history.save_json(args.output, {'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                                           'jobs': rows, 'note': 'Saved outcomes; refresh request/pins before current claims. Reservations are not acceptance outcomes.'})
        return 0
    except Exception as exc:  # CLI boundary: fail closed, including dependency/API errors.
        print(json.dumps({'status': 'skipped', 'reason': str(exc)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
