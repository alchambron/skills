#!/usr/bin/env python3
"""Fail-closed response queue planning, durable reservations and digest export.

No command dispatches agents or mutates GitHub; SKILL.md owns orchestration.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import uuid
import re
from datetime import datetime, timezone
import fcntl

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'EnerZam/JouleMV'
LOGIN = 'alchambron'
DEFAULT_STATE = Path.home()/'.codex/review-state/respond-queue'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def companions(respond_all, respond):
    sys.path.insert(0, str(respond_all/'scripts'))
    requirements = json.loads((ROOT/'requirements.json').read_text())['requires']
    def version(folder):
        value = (folder/'VERSION').read_text().strip()
        if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
            raise ValueError('invalid VERSION')
        return tuple(map(int, value.split('.')))
    for name, folder in [('respond-all', respond_all), ('respond', respond)]:
        limits = requirements[name]
        lower = tuple(map(int, limits['minimum'].split('.')))
        upper = tuple(map(int, limits['maximum_exclusive'].split('.')))
        if not lower <= version(folder) < upper:
            raise ValueError(f'{name}: unsupported version')
    return (load('respond_queue_collector', respond_all/'scripts/collect_feedback.py'),
            load('respond_queue_history', respond_all/'scripts/history.py'))


def stamp():
    return datetime.now(timezone.utc).isoformat()


def api(endpoint):
    result = subprocess.run(['gh', 'api', '--hostname', 'github.com', endpoint],
                            capture_output=True, text=True, timeout=120, check=True)
    return json.loads(result.stdout)


def pages(endpoint):
    found = []
    page = 1
    while True:
        rows = api(f'{endpoint}{"&" if "?" in endpoint else "?"}per_page=100&page={page}')
        if not isinstance(rows, list):
            raise ValueError('invalid inventory response')
        found.extend(rows)
        if len(rows) < 100:
            return found
        page += 1


def human(user):
    login = user.get('login', '').casefold()
    return (user.get('type') == 'User' and bool(login) and login != LOGIN and
            not any(word in login for word in ('[bot]', 'copilot', 'coderabbit')))


def eligible(pr, reviews, stack):
    if (pr.get('state') != 'open' or pr.get('user', {}).get('login', '').casefold() != LOGIN or
            pr.get('base', {}).get('repo', {}).get('full_name', '').casefold() != REPOSITORY.casefold()):
        raise ValueError('not an open authored JouleMV PR')
    if not stack or any(p.get('user', {}).get('login', '').casefold() != LOGIN for p in stack):
        raise ValueError('mixed or unknown stack ownership')
    requests = [r for r in reviews if r.get('state') == 'CHANGES_REQUESTED' and human(r.get('user') or {})]
    if not requests:
        raise ValueError('no human changes-requested review')
    return requests


def stack_members(pr):
    # Include connected ancestors AND descendants, not only the target's base chain.
    inventory = pages(f'repos/{REPOSITORY}/pulls?state=open')
    by_number = {p['number']: p for p in inventory}
    by_number[pr['number']] = pr
    group = {pr['number']}
    changed = True
    while changed:
        changed = False
        for n, p in by_number.items():
            for m in list(group):
                q = by_number[m]
                if (p['base']['ref'] == q['head']['ref'] or q['base']['ref'] == p['head']['ref']):
                    if n not in group:
                        group.add(n); changed = True
    return [by_number[n] for n in sorted(group)]


def snapshot(number):
    pr = api(f'repos/{REPOSITORY}/pulls/{number}')
    reviews = pages(f'repos/{REPOSITORY}/pulls/{number}/reviews')
    requests = eligible(pr, reviews, stack_members(pr))
    return pr, requests


def batch_id(items):
    return hashlib.sha256(json.dumps(sorted((i['url'], i['date']) for i in items)).encode()).hexdigest()


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root/'queue.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        path = root/'state.json'
        state = json.loads(path.read_text()) if path.exists() else {'schema_version': 1, 'jobs': {}, 'batches': {}, 'skips': []}
        yield state
        save(path, state)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    with temp.open('w') as handle:
        json.dump(data, handle, indent=2); handle.write('\n'); handle.flush()
        import os
        os.fsync(handle.fileno())
    temp.replace(path)


def reserve(state, phase, number, head, batch, run_dir, thread, host_ready):
    if not host_ready:
        raise ValueError('Mac busy, offline or status unknown')
    active = [j for j in state['jobs'].values() if j['status'] == 'active']
    if any(j['number'] == number for j in active):
        raise ValueError('PR already reserved')
    if sum(j['phase'] == phase for j in active) >= {'analysis': 2, 'fix': 1}[phase]:
        raise ValueError('machine-wide resource cap reached')
    key = f'{number}:{batch}'
    prior = state['batches'].get(key)
    if prior and prior['status'] in ('completed', 'handoff', 'skipped'):
        raise ValueError('feedback batch already terminal')
    record = state['batches'].setdefault(key, dict(number=number, feedback_batch=batch,
        head=head, fix_rounds=0, status='queued', actions=[], commits=[], replies=[],
        re_request={}, ci={'state': 'unknown'}, handoff_reason=None))
    if phase == 'fix':
        if record['fix_rounds'] >= 2:
            raise ValueError('two fix rounds exhausted; hand off to Alexis')
        if record['ci'].get('state') in ('pending', 'missing'):
            raise ValueError('CI must settle before another fixer')
        record['fix_rounds'] += 1
    token = uuid.uuid4().hex
    state['jobs'][token] = dict(number=number, phase=phase, head=head, batch_key=key,
        run_dir=str(run_dir), thread=thread, status='active', started_at=stamp())
    record['status'] = 'active'
    return token


def finish(state, token, outcome, cleanup):
    job = state['jobs'][token]
    if job['status'] != 'active':
        raise ValueError('reservation already finished')
    if not cleanup:
        raise ValueError('verify worker and runtime cleanup before release')
    record = state['batches'][job['batch_key']]
    status = outcome['status']
    if status not in ('analyzed', 'waiting_ci', 'completed', 'handoff', 'skipped'):
        raise ValueError('invalid outcome status')
    if status in ('handoff', 'skipped') and not outcome.get('reason'):
        raise ValueError('handoffs/skips require a reason')
    if status == 'completed' and outcome.get('ci', record['ci']).get('state') not in ('passed', 'not_required'):
        raise ValueError('completion requires settled CI (or explicit not_required for no push)')
    for field in ('actions', 'commits', 'replies'):
        record[field].extend(outcome.get(field, []))
    for field in ('head', 're_request', 'ci'):
        if field in outcome:
            record[field] = outcome[field]
    record.update(status=status, handoff_reason=outcome.get('reason'), updated_at=stamp())
    job.update(status='finished', finished_at=stamp())


def digest(state):
    return {'schema_version': 1, 'generated_at': stamp(), 'repository': REPOSITORY,
            'prs': list(state['batches'].values()), 'reservations': list(state['jobs'].values()),
            'skips': state['skips'], 'source': 'Recorded evidence; not a live CI refresh'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--respond-all', type=Path, default=ROOT.parent/'respond-all')
    parser.add_argument('--respond', type=Path, default=ROOT.parent/'respond')
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan'); p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('claim'); p.add_argument('--phase', choices=['analysis', 'fix'], required=True)
    p.add_argument('--number', type=int, required=True); p.add_argument('--batch', required=True)
    p.add_argument('--head', required=True); p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--thread', required=True); p.add_argument('--host-ready', action='store_true')
    p = sub.add_parser('guard'); p.add_argument('--token', required=True); p.add_argument('--head', required=True)
    p = sub.add_parser('finish'); p.add_argument('--token', required=True)
    p.add_argument('--outcome', type=Path, required=True); p.add_argument('--cleanup-verified', action='store_true')
    p = sub.add_parser('observe-ci')
    p.add_argument('--number', type=int, required=True); p.add_argument('--batch', required=True)
    p.add_argument('--head', required=True); p.add_argument('--ci', type=Path, required=True)
    p = sub.add_parser('complete')
    p.add_argument('--number', type=int, required=True); p.add_argument('--batch', required=True)
    p.add_argument('--run-dir', type=Path, required=True)
    p = sub.add_parser('handoff')
    p.add_argument('--number', type=int, required=True); p.add_argument('--batch', required=True)
    p.add_argument('--reason', required=True)
    sub.add_parser('digest')
    args = parser.parse_args(argv)
    try:
        if args.command == 'digest':
            with locked(args.state) as state:
                data = digest(state)
                save(args.state/'digest-index.json', data)
                (args.state/'digest.md').write_text('\n'.join(['# Respond queue', '', data['generated_at'], ''] +
                    [f"- #{r['number']}: {r['status']}; rounds {r['fix_rounds']}/2; CI {r['ci'].get('state')}; {r.get('handoff_reason') or ''}" for r in data['prs']] +
                    [f"- Skip: {r}" for r in data['skips']])+'\n')
            print(args.state/'digest-index.json'); return 0
        if args.command in ('observe-ci', 'handoff', 'complete'):
            with locked(args.state) as state:
                key = f'{args.number}:{args.batch}'
                if any(j['number'] == args.number and j['status'] == 'active' for j in state['jobs'].values()):
                    raise ValueError('worker still active; retain reservation')
                record = state['batches'][key]
                if args.command == 'complete':
                    _, history = companions(args.respond_all, args.respond)
                    ledger = history.ledger.read(args.run_dir)
                    entry = ledger['prs'][f'https://github.com/{REPOSITORY}/pull/{args.number}']
                    if (batch_id(entry['untouched']) != args.batch or
                            entry.get('status') == 'skipped' or
                            (entry.get('currentHeadSha') or entry.get('publishedHeadSha') or entry.get('initialHeadSha')) != record['head'] or
                            record['ci'].get('state') not in ('passed', 'not_required') or
                            not history.settled(entry)):
                        raise ValueError('completion requires matching settled ledger and CI')
                    record.update(status='completed', updated_at=stamp())
                elif args.command == 'handoff':
                    if not args.reason.strip():
                        raise ValueError('handoff reason required')
                    record.update(status='handoff', handoff_reason=args.reason, updated_at=stamp())
                else:
                    ci = json.loads(args.ci.read_text())
                    if (ci.get('state') not in ('passed', 'failed', 'missing') or
                            ci.get('head') != args.head or record['head'] != args.head):
                        raise ValueError('settled exact-head CI evidence required')
                    record.update(ci=ci, status='ci_settled', updated_at=stamp())
            return 0
        if args.command == 'finish':
            with locked(args.state) as state:
                finish(state, args.token, json.loads(args.outcome.read_text()), args.cleanup_verified)
            return 0
        collector, history = companions(args.respond_all, args.respond)
        if api('user').get('login', '').casefold() != LOGIN:
            raise ValueError('authenticated account must be alchambron')
        if args.command == 'plan':
            raw = args.state/'discovery.json'; raw.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([sys.executable, str(args.respond_all/'scripts/collect_feedback.py'),
                            '--repo', REPOSITORY, '--out', str(raw)], check=True)
            discovery = json.loads(raw.read_text())
            if not discovery.get('complete'):
                raise ValueError('incomplete discovery')
            with history.locked() as directory:
                filtered, dropped = history.filter_discovery(discovery, history.read_state(directory))
            selected, skipped = [], list(dropped)
            for match in filtered['matches']:
                try:
                    pr, reviews = snapshot(match['number'])
                    reviewers = {r['user']['login'] for r in reviews}
                    triggers = [i for i in match['untouched'] if i['kind'] == 'CHANGES_REQUESTED' and i['author'] in reviewers]
                    if not triggers:
                        raise ValueError('no new human changes-requested review')
                    batch = batch_id(match['untouched'])
                    selected.append({**match, 'feedback_batch': batch, 'headSha': pr['head']['sha']})
                except ValueError as exc:
                    skipped.append({'url': match['url'], 'reason': str(exc)})
            with locked(args.state) as state:
                remaining = []
                for row in selected:
                    prior = state['batches'].get(f"{row['number']}:{row['feedback_batch']}")
                    if prior:
                        skipped.append({'url': row['url'], 'reason': 'unchanged feedback batch; resume saved run', 'head': row['headSha']})
                    else:
                        remaining.append(row)
                state['skips'].extend(skipped)
            save(args.output, {**filtered, 'matches': remaining, 'skips': skipped})
            print(json.dumps({'selection': str(args.output), 'count': len(remaining)})); return 0
        if args.command == 'claim':
            if not args.host_ready:
                raise ValueError('Mac busy, offline or status unknown')
            pr, _ = snapshot(args.number)
            if pr['head']['sha'] != args.head:
                raise ValueError('head moved; refresh analysis')
            ledger = json.loads((args.run_dir/'ledger.json').read_text())
            url = f'https://github.com/{REPOSITORY}/pull/{args.number}'
            entry = ledger['prs'][url]
            if batch_id(entry['untouched']) != args.batch:
                raise ValueError('batch does not match frozen respond ledger')
            if not any(i['kind'] == 'CHANGES_REQUESTED' for i in entry['untouched']):
                raise ValueError('batch has no changes-requested trigger')
            with locked(args.state) as state:
                token = reserve(state, args.phase, args.number, args.head, args.batch,
                                args.run_dir.resolve(), args.thread, args.host_ready)
            print(json.dumps({'token': token})); return 0
        with locked(args.state) as state:
            job = state['jobs'][args.token]
            if job['status'] != 'active' or job['phase'] != 'fix':
                raise ValueError('mutation requires active fixer reservation')
            pr, _ = snapshot(job['number'])
            if pr['head']['sha'] != args.head:
                raise ValueError('remote head moved; stop mutation')
        print(json.dumps({'eligible': True, 'head': args.head})); return 0
    except Exception as exc:
        with locked(args.state) as state:
            state['skips'].append({'at': stamp(), 'command': args.command, 'reason': str(exc)})
        print(json.dumps({'status': 'skipped', 'reason': str(exc)})); return 2


if __name__ == '__main__':
    sys.exit(main())
