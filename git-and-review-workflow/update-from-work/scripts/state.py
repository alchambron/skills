#!/usr/bin/env python3
"""Shared-slot reservations and dated digest exports for update-from-work.

Uses respond-queue/scripts/shared_slots.py and ~/.codex/review-state/queue-slots.lock
so update-from-work shares the single live slot with review-queue live acceptance
and respond-queue fixers. Also exports unified dated YYYY-MM-DD.json|.md digests
with pending_digest_dates retry support.
"""
import argparse
from datetime import date, datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'EnerZam/JouleMV'
TORONTO = ZoneInfo('America/Toronto')
DEFAULT_STATE = Path.home() / '.codex/review-state/update-from-work'
SKIP_LIMIT = 200


def load_slots(respond_queue):
    path = Path(respond_queue).resolve() / 'scripts/shared_slots.py'
    spec = importlib.util.spec_from_file_location('update_from_work_slots', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def now():
    return datetime.now(timezone.utc)


def digest_date(timestamp):
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    if timestamp.utcoffset() is None:
        raise ValueError('digest timestamps must include a timezone')
    return timestamp.astimezone(TORONTO).date()


def write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        staged = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


def save_json(path, data):
    write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def read_state(root):
    try:
        state = json.loads((root / 'state.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        state = {'schema_version': 1, 'jobs': {}, 'runs': {}, 'skips': [], 'pending_digest_dates': []}
    if (not isinstance(state, dict) or not isinstance(state.get('jobs'), dict)
            or not isinstance(state.get('runs', {}), dict)
            or not isinstance(state.get('skips', []), list)
            or not isinstance(state.get('pending_digest_dates', []), list)):
        raise ValueError('invalid update-from-work state')
    state.setdefault('schema_version', 1)
    state.setdefault('runs', {})
    state.setdefault('skips', [])
    state.setdefault('pending_digest_dates', [])
    return state


def save_state(root, state):
    state['skips'] = state['skips'][-SKIP_LIMIT:]
    save_json(root / 'state.json', state)


def active_jobs(state):
    return [j for j in state['jobs'].values() if j.get('status') == 'active']


def claim(root, slots, state, number, run_dir, thread, host_ready):
    if not host_ready:
        raise ValueError('Mac busy, offline or availability unknown')
    if type(number) is not int or number < 1:
        raise ValueError('PR number must be a positive integer')
    slots.check_capacity(root, 'update-from-work', 'update', number, active_jobs(state))
    token = uuid.uuid4().hex
    state['jobs'][token] = {
        'token': token,
        'number': number,
        'phase': 'update',
        'status': 'active',
        'run_dir': str(Path(run_dir).resolve()),
        'thread': thread,
        'started_at': now().isoformat(),
    }
    return token


def finish(state, token, cleanup, status=None, reason=None):
    job = state['jobs'].get(token)
    if not job:
        raise ValueError('unknown reservation token')
    if job['status'] != 'active':
        raise ValueError('reservation already finished')
    if not cleanup:
        raise ValueError('verify worker and checkout cleanup before release')
    job['status'] = 'finished'
    job['finished_at'] = now().isoformat()
    if status:
        job['outcome'] = status
    if reason:
        job['reason'] = reason
    return job


def record_defer(state, reason, number=None):
    state['skips'].append({'at': now().isoformat(), 'command': 'defer', 'number': number, 'reason': reason})
    state['skips'] = state['skips'][-SKIP_LIMIT:]


def build_digest(state, day):
    key = day.isoformat()
    run = state.get('runs', {}).get(key) or {}
    reservations = [{**job, 'token': token} for token, job in state['jobs'].items() if job.get('status') == 'active']
    skips = [row for row in state.get('skips', [])
             if isinstance(row, dict) and row.get('at') and digest_date(row['at']) == day]
    return {
        'schema_version': 1,
        'date': key,
        'generated_at': now().isoformat(),
        'repository': REPOSITORY,
        'run_status': run.get('status', 'no_run_recorded'),
        'pinned_work': run.get('pinned_work'),
        'work_ci': run.get('work_ci'),
        'queue': run.get('queue', []),
        'updated': run.get('updated', []),
        'skipped_conflicts': run.get('skipped_conflicts', []),
        'skipped_other': run.get('skipped_other', []),
        'ci_still_running': run.get('ci_still_running', []),
        'resolutions': run.get('resolutions', []),
        'queue_evidence': run.get('queue_evidence'),
        'report_path': run.get('report_path'),
        'reservations': reservations,
        'skips': skips,
    }


def render_markdown(data):
    lines = [f"# Update from work for {data['date']}", '',
             f"Status: {data['run_status']} · pinned work: {data['pinned_work'] or 'n/a'} · CI: {data['work_ci'] or 'unknown'}",
             '']
    if data['updated']:
        lines.append('## Updated')
        lines.extend(f"- #{row.get('number')} ({(row.get('head') or '')[:8]}) · CI {row.get('ci')}" for row in data['updated'])
        lines.append('')
    if data['queue']:
        lines.append('## Merge queue (top 5)')
        for row in data['queue'][:5]:
            prs = ', '.join(f'#{n}' for n in row.get('prs', []))
            unresolved = row.get('unresolved_threads') or {}
            note = ''
            if unresolved:
                total = sum(v for v in unresolved.values() if isinstance(v, int))
                note = f' · {total} unresolved thread(s)'
            lines.append(f"- {prs} (approved {row.get('approval_time', 'n/a')}){note}")
        lines.append('')
    if data['skipped_conflicts']:
        lines.append('## Skipped conflicts')
        lines.extend(f"- #{row.get('number')}: {', '.join(row.get('files', []))}" for row in data['skipped_conflicts'])
        lines.append('')
    if data['ci_still_running']:
        lines.append('## CI still running')
        lines.extend(f"- #{row.get('number')} ({(row.get('head') or '')[:8]})" for row in data['ci_still_running'])
        lines.append('')
    if data['reservations']:
        lines.append('## Active reservations')
        lines.extend(f"- #{j['number']} · {j['phase']} · thread {j['thread']} · token {j['token']}" for j in data['reservations'])
        lines.append('')
    if data['skips']:
        lines.append('## Skips and deferrals')
        for row in data['skips'][-20:]:
            prefix = f"#{row['number']} · " if row.get('number') else ''
            lines.append(f"- {row['at']} · {prefix}{row['reason']}")
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def export_digest(root, state, day):
    data = build_digest(state, day)
    directory = root / 'digest'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'{day.isoformat()}.json'
    write_atomic(directory / f'{day.isoformat()}.md', render_markdown(data))
    save_json(target, data)
    key = day.isoformat()
    if key in state['pending_digest_dates']:
        state['pending_digest_dates'].remove(key)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--respond-queue', type=Path, default=ROOT.parent / 'respond-queue')
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('claim')
    p.add_argument('--number', type=int, required=True)
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--thread', required=True)
    p.add_argument('--host-ready', action='store_true')
    p = sub.add_parser('finish')
    p.add_argument('--token', required=True)
    p.add_argument('--cleanup-verified', action='store_true')
    p.add_argument('--status')
    p.add_argument('--reason')
    p = sub.add_parser('defer')
    p.add_argument('--number', type=int)
    p.add_argument('--reason', required=True)
    p = sub.add_parser('export-digest')
    p.add_argument('--report-json', type=Path, required=True)
    p.add_argument('--report-path')
    p.add_argument('--date', type=date.fromisoformat)
    d = sub.add_parser('digest').add_mutually_exclusive_group()
    d.add_argument('--date', type=date.fromisoformat)
    d.add_argument('--retry-pending', action='store_true')
    args = parser.parse_args(argv)
    root = args.state.resolve()
    try:
        slots = load_slots(args.respond_queue)
        with slots.locked(root):
            state = read_state(root)
            if args.command == 'claim':
                token = claim(root, slots, state, args.number, args.run_dir, args.thread, args.host_ready)
                save_state(root, state)
                print(json.dumps({'token': token}))
                return 0
            if args.command == 'finish':
                job = finish(state, args.token, args.cleanup_verified, args.status, args.reason)
                save_state(root, state)
                print(json.dumps({'token': args.token, 'number': job['number'], 'status': job['status']}))
                return 0
            if args.command == 'defer':
                record_defer(state, args.reason, args.number)
                save_state(root, state)
                return 0
            if args.command == 'export-digest':
                payload = json.loads(args.report_json.read_text(encoding='utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('report JSON must be an object')
                if args.report_path:
                    payload = {**payload, 'report_path': args.report_path}
                day = args.date or (digest_date(payload['finished_at']) if payload.get('finished_at') else digest_date(now()))
                key = day.isoformat()
                state['runs'][key] = payload
                if key not in state['pending_digest_dates']:
                    state['pending_digest_dates'].append(key)
                save_state(root, state)
                path = export_digest(root, state, day)
                save_state(root, state)
                print(json.dumps({'digest': str(path), 'date': key}))
                return 0
            if args.retry_pending:
                exports, failures = [], {}
                for key in list(state['pending_digest_dates']):
                    try:
                        exports.append(str(export_digest(root, state, date.fromisoformat(key))))
                    except OSError as exc:
                        failures[key] = str(exc)
                save_state(root, state)
                print(json.dumps({'exports': exports, 'failures': failures}))
                return 2 if failures else 0
            day = args.date or digest_date(now())
            path = export_digest(root, state, day)
            save_state(root, state)
            print(str(path))
            return 0
    except Exception as exc:
        print(json.dumps({'status': 'skipped', 'command': args.command, 'reason': str(exc)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
