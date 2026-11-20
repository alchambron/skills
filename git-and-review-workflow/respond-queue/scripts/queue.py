#!/usr/bin/env python3
"""Fail-closed response queue planning, durable reservations and digest export.

No command dispatches agents or mutates GitHub; SKILL.md owns orchestration.

Batch states: queued -> active(analysis) -> analyzed -> active(fix) ->
waiting_ci -> ci_settled -> active(fix) ... ; a fix without a push finishes
as no_push. Only `complete` reaches completed (from ci_settled with passed
exact-head CI, or no_push). completed, handoff and skipped are terminal.
"""
import argparse
from contextlib import contextmanager
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'EnerZam/JouleMV'
LOGIN = 'alchambron'
TORONTO = ZoneInfo('America/Toronto')
DEFAULT_STATE = Path.home()/'.codex/review-state/respond-queue'
INTEGRATION_BRANCHES = ('work',)
TERMINAL = ('completed', 'handoff', 'skipped')
SETTLED_CI = ('passed', 'failed', 'missing')
HANDOFF_KINDS = ('external_hold', 'infra_blocked', 'needs_decision')
SKIP_LIMIT = 200
SEMVER = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)')
SHA = re.compile(r'[0-9a-f]{40}')
# Pure policy sign-off holds explicitly approve from the reviewer's side and set
# Request changes solely because the PR touches a sign-off-gated file. Any extra
# comments, questions, or fix wording falls through to full analysis (fail-closed).
SIGN_OFF_HOLD = re.compile(
    r"\AApproved from my side\.\s+"
    r"I(?:'|\u2019)?m setting this to Request changes because it touches\s+[^?\n]+\.?\Z",
    re.IGNORECASE,
)
HOLD_REASON = 'Pure sign-off hold (no code change requested); awaiting human sign-off.'
EXTERNAL_HOLD_RE = re.compile(
    r'sign-off hold|approval hold|pure sign-off hold|awaiting human sign-off|'
    r'names no (?:code|change|repair)|named no repair|identified no code correction',
    re.IGNORECASE,
)
INFRA_BLOCKED_RE = re.compile(
    r'billing|spending limit|ci (?:is )?missing|hosted (?:github actions )?ci is missing|'
    r'zero workflow runs|zero actions runs|infrastructure|offline|mac busy|host unavailable|github unavailable',
    re.IGNORECASE,
)
BATCH_DEFAULTS = dict(number=None, feedback_batch=None, head=None, fix_rounds=0, status='unknown',
                      actions=[], commits=[], replies=[], re_request={}, ci={'state': 'unknown'},
                      handoff_reason=None, handoff_kind=None)
JOB_DEFAULTS = dict(number=None, phase=None, head=None, batch_key=None, run_dir=None, thread=None,
                    status='unknown', started_at=None)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared_slots = load('review_response_shared_slots', Path(__file__).with_name('shared_slots.py'))


def parse_version(value, label):
    if not isinstance(value, str) or not SEMVER.fullmatch(value.strip()):
        raise ValueError(f'{label}: invalid version {value!r}')
    return tuple(map(int, value.strip().split('.')))


def check_versions(respond_all, respond, requirements=None):
    if requirements is None:
        requirements = json.loads((ROOT/'requirements.json').read_text())['requires']
    for name, folder in [('respond-all', respond_all), ('respond', respond)]:
        limits = requirements.get(name)
        if not isinstance(limits, dict):
            raise ValueError(f'{name}: missing version range')
        lower = parse_version(limits.get('minimum'), f'{name} minimum')
        upper = parse_version(limits.get('maximum_exclusive'), f'{name} maximum_exclusive')
        if not lower < upper:
            raise ValueError(f'{name}: empty version range')
        found = parse_version((Path(folder)/'VERSION').read_text(), f'{name} VERSION')
        if not lower <= found < upper:
            raise ValueError(f'{name}: unsupported version {".".join(map(str, found))}')


def companions(respond_all, respond):
    """Return respond-all's collector and history modules and respond's snapshot module."""
    check_versions(respond_all, respond)
    for folder in (respond/'scripts', respond_all/'scripts'):
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
    return (load('respond_queue_collector', respond_all/'scripts/collect_feedback.py'),
            load('respond_queue_history', respond_all/'scripts/history.py'),
            load('respond_queue_snapshot', respond/'scripts/feedback_snapshot.py'))


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest_date(timestamp):
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    if timestamp.utcoffset() is None:
        raise ValueError('digest timestamps must include a timezone')
    return timestamp.astimezone(TORONTO).date()


def infer_handoff_kind(reason, explicit=None):
    if explicit is not None:
        if explicit not in HANDOFF_KINDS:
            raise ValueError(f'handoff_kind must be one of {", ".join(HANDOFF_KINDS)}')
        return explicit
    text = str(reason or '')
    if EXTERNAL_HOLD_RE.search(text):
        return 'external_hold'
    if INFRA_BLOCKED_RE.search(text):
        return 'infra_blocked'
    return 'needs_decision'


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


def current_requests(reviews):
    """Each human reviewer's latest decisive review, kept only while it requests changes."""
    latest = {}
    for review in sorted(reviews, key=lambda r: r.get('submitted_at') or ''):
        user = review.get('user') or {}
        if human(user) and review.get('state') in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'):
            latest[user['login'].casefold()] = review
    return [r for r in latest.values() if r['state'] == 'CHANGES_REQUESTED']


def eligible(pr, reviews, stack, triggers=None):
    """Return current change requests; with triggers, every batch trigger must still be current."""
    if (pr.get('state') != 'open' or pr.get('user', {}).get('login', '').casefold() != LOGIN or
            pr.get('base', {}).get('repo', {}).get('full_name', '').casefold() != REPOSITORY.casefold()):
        raise ValueError('not an open authored JouleMV PR')
    if not stack or any(p.get('user', {}).get('login', '').casefold() != LOGIN for p in stack):
        raise ValueError('mixed or unknown stack ownership')
    requests = current_requests(reviews)
    if not requests:
        raise ValueError('no current human changes-requested review')
    if triggers is not None:
        wanted = [t for t in triggers if t.get('kind') == 'CHANGES_REQUESTED']
        if not wanted:
            raise ValueError('feedback batch has no changes-requested trigger')
        current = {r.get('html_url'): r for r in requests}
        for trigger in wanted:
            review = current.get(trigger.get('url'))
            if (not review or review['user']['login'].casefold() != (trigger.get('author') or '').casefold() or
                    (review.get('submitted_at') or '') > (trigger.get('date') or '')):
                raise ValueError('batch trigger is no longer the reviewer\'s current changes-requested review')
    return requests


def stack_members(pr, inventory=None):
    """Connected open stack through same-repository head branches, ancestors and descendants.

    Integration/default branches never link PRs, fork heads never link, and a
    branch heading two open PRs is ambiguous and fails closed.
    """
    if inventory is None:
        inventory = pages(f'repos/{REPOSITORY}/pulls?state=open')
    default = (pr.get('base', {}).get('repo') or {}).get('default_branch')
    if not default:
        raise ValueError('unknown default branch; stack cannot be resolved')
    blocked = {default, *INTEGRATION_BRANCHES}
    by_number = {p['number']: p for p in inventory}
    by_number[pr['number']] = pr

    def local_head(p):
        head = p.get('head') or {}
        return ((head.get('repo') or {}).get('full_name', '').casefold() == REPOSITORY.casefold() and
                head.get('ref') not in blocked and bool(head.get('ref')))

    heads = {}
    for p in by_number.values():
        if local_head(p):
            heads.setdefault(p['head']['ref'], []).append(p['number'])

    def owner(ref):
        owners = heads.get(ref, [])
        if len(owners) > 1:
            raise ValueError(f'ambiguous stack: branch {ref} heads PRs {sorted(owners)}')
        return owners

    group, frontier = {pr['number']}, [pr['number']]
    while frontier:
        member = by_number[frontier.pop()]
        linked = list(owner(member['base']['ref'])) if member['base']['ref'] not in blocked else []
        if local_head(member):
            owner(member['head']['ref'])
            linked += [n for n, p in by_number.items() if p['base']['ref'] == member['head']['ref']]
        for number in linked:
            if number not in group:
                group.add(number); frontier.append(number)
    return [by_number[n] for n in sorted(group)]


def snapshot(number, triggers=None):
    pr = api(f'repos/{REPOSITORY}/pulls/{number}')
    reviews = pages(f'repos/{REPOSITORY}/pulls/{number}/reviews')
    requests = eligible(pr, reviews, stack_members(pr), triggers)
    return pr, requests


def live_feedback(snapshotter, number):
    url = f'https://github.com/{REPOSITORY}/pull/{number}'
    return snapshotter.build_snapshot(url, LOGIN, pages(f'repos/{REPOSITORY}/issues/{number}/comments'),
                                      pages(f'repos/{REPOSITORY}/pulls/{number}/comments'),
                                      pages(f'repos/{REPOSITORY}/pulls/{number}/reviews'))


def frozen_snapshot(path):
    data = json.loads(Path(path).read_text())
    data = data.get('humanFeedbackSnapshot', data) if isinstance(data, dict) else data
    if not isinstance(data, dict) or data.get('schemaVersion') != 1 or not data.get('sha256'):
        raise ValueError('original humanFeedbackSnapshot required')
    return data


def batch_id(items):
    return hashlib.sha256(json.dumps(sorted((i['url'], i['date']) for i in items)).encode()).hexdigest()


def ledger_entry(ledger_module, run_dir, number, batch):
    entry = ledger_module.read(run_dir)['prs'].get(f'https://github.com/{REPOSITORY}/pull/{number}')
    if not entry:
        raise ValueError('PR missing from respond ledger')
    if batch_id(entry.get('untouched', [])) != batch:
        raise ValueError('batch does not match frozen respond ledger')
    return entry


@contextmanager
def locked(root):
    with shared_slots.locked(root):
        path = root/'state.json'
        state = json.loads(path.read_text()) if path.exists() else {
            'schema_version': 1, 'jobs': {}, 'batches': {}, 'skips': [], 'pending_digest_dates': []}
        for key, value in (('jobs', {}), ('batches', {}), ('skips', []), ('pending_digest_dates', [])):
            state.setdefault(key, value)
        yield state
        state['skips'] = state['skips'][-SKIP_LIMIT:]
        save(path, state)


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    with temp.open('w') as handle:
        handle.write(text); handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


def save(path, data):
    write_atomic(path, json.dumps(data, indent=2) + '\n')


def is_signoff_hold(untouched, requests):
    """True only when every untouched item is a pure sign-off hold review with no edits requested."""
    if not untouched or any(item.get('kind') != 'CHANGES_REQUESTED' for item in untouched):
        return False
    by_url = {r.get('html_url'): r for r in requests}
    for item in untouched:
        review = by_url.get(item.get('url'))
        if not review:
            return False
        body = ' '.join((review.get('body') or '').split())
        if not body or '?' in body or not SIGN_OFF_HOLD.fullmatch(body):
            return False
    return True


def add_skips(state, rows, *, dedupe=False):
    existing = list(state['skips'])
    if not dedupe:
        state['skips'] = (existing + list(rows))[-SKIP_LIMIT:]
        return
    for row in rows:
        key = (row.get('url'), row.get('command'), row.get('reason'), row.get('head'))
        matched = next(
            (i for i in range(len(existing) - 1, -1, -1)
             if isinstance(existing[i], dict)
             and (existing[i].get('url'), existing[i].get('command'),
                  existing[i].get('reason'), existing[i].get('head')) == key),
            None,
        )
        if matched is not None:
            existing[matched] = {**existing[matched], 'at': row.get('at', existing[matched].get('at'))}
        else:
            existing.append(row)
    state['skips'] = existing[-SKIP_LIMIT:]


def active_jobs(state):
    return [j for j in state['jobs'].values() if j.get('status') == 'active']


def reserve(state, phase, number, head, batch, run_dir, thread, host_ready, *, state_root):
    if not host_ready:
        raise ValueError('Mac busy, offline or status unknown')
    shared_slots.check_capacity(state_root, 'respond-queue', phase, number, active_jobs(state))
    key = f'{number}:{batch}'
    prior = state['batches'].get(key)
    if prior and prior['status'] in TERMINAL:
        raise ValueError('feedback batch already terminal')
    if phase == 'fix':
        waiting = [k for k, r in state['batches'].items()
                   if r.get('number') == number and r.get('status') == 'waiting_ci']
        if waiting:
            raise ValueError(f'CI pending for {waiting[0]}; settle it before another fixer on this PR')
        if not prior or prior['status'] not in ('analyzed', 'ci_settled', 'no_push'):
            raise ValueError('fix requires a finished analysis or settled CI')
        if prior['fix_rounds'] >= 2:
            raise ValueError('two fix rounds exhausted; hand off to Alexis')
        if prior['commits']:
            ci = prior.get('ci') or {}
            if ci.get('state') not in ('passed', 'failed') or ci.get('head') != prior['head']:
                raise ValueError('settled exact-head CI required before another fixer')
            if head != prior['head']:
                raise ValueError('claim head differs from the CI-observed pushed head')
    elif prior and prior['status'] not in ('queued', 'analyzed'):
        raise ValueError(f'analysis not allowed while batch is {prior["status"]}')
    record = state['batches'].setdefault(key, dict(copy.deepcopy(BATCH_DEFAULTS), number=number,
                                                   feedback_batch=batch, head=head, status='queued'))
    if phase == 'fix':
        record['fix_rounds'] += 1
    token = uuid.uuid4().hex
    state['jobs'][token] = dict(token=token, number=number, phase=phase, head=head, batch_key=key,
        run_dir=str(run_dir), thread=thread, status='active', started_at=stamp())
    record['status'] = 'active'
    return token


def finish(state, token, outcome, cleanup):
    job = state['jobs'].get(token)
    if not job:
        raise ValueError('unknown reservation token')
    if job['status'] != 'active':
        raise ValueError('reservation already finished')
    if not cleanup:
        raise ValueError('verify worker and runtime cleanup before release')
    record = state['batches'][job['batch_key']]
    status = outcome.get('status')
    allowed = {'analysis': ('analyzed', 'handoff', 'skipped'),
               'fix': ('waiting_ci', 'no_push', 'handoff', 'skipped')}[job['phase']]
    if status not in allowed:
        raise ValueError(f'{job["phase"]} reservations finish as one of {", ".join(allowed)}; '
                         'use complete after settled CI')
    if status in ('handoff', 'skipped') and not str(outcome.get('reason') or '').strip():
        raise ValueError('handoffs/skips require a reason')
    ci = outcome.get('ci')
    commits = outcome.get('commits', [])
    if status == 'waiting_ci':
        head = outcome.get('head')
        if (not isinstance(head, str) or not SHA.fullmatch(head) or not commits or head not in commits or
                not isinstance(ci, dict) or ci.get('state') != 'pending' or ci.get('head') != head):
            raise ValueError('waiting_ci requires the pushed 40-hex head in commits and pending CI on that head')
    if status == 'no_push':
        if commits or not isinstance(ci, dict) or ci.get('state') != 'not_required':
            raise ValueError('no_push requires no commits and ci.state not_required')
        if outcome.get('head', record['head']) != record['head']:
            raise ValueError('no_push cannot move the head')
        ci = {**ci, 'head': record['head']}
    for field in ('actions', 'commits', 'replies'):
        record[field].extend(outcome.get(field, []))
    if status == 'waiting_ci' or (status == 'handoff' and outcome.get('head') in commits):
        record['head'] = outcome['head']
    if 're_request' in outcome:
        record['re_request'] = outcome['re_request']
    if isinstance(ci, dict):
        record['ci'] = ci
    if status == 'handoff':
        kind = infer_handoff_kind(outcome.get('reason'), outcome.get('handoff_kind'))
        record.update(status=status, handoff_reason=outcome.get('reason'), handoff_kind=kind, updated_at=stamp())
    else:
        record.update(status=status, handoff_reason=record.get('handoff_reason'), updated_at=stamp())
    if status == 'skipped':
        record['skip_reason'] = outcome['reason']
    job.update(status='finished', finished_at=stamp())


def require_idle(state, number):
    if any(j['number'] == number for j in active_jobs(state)):
        raise ValueError('worker still active; retain reservation')


def batch_record(state, number, batch):
    record = state['batches'].get(f'{number}:{batch}')
    if not record:
        raise ValueError('unknown feedback batch')
    return record


def observe_ci(state, number, batch, head, ci):
    require_idle(state, number)
    record = batch_record(state, number, batch)
    if record['status'] != 'waiting_ci':
        raise ValueError(f'CI observation requires waiting_ci, batch is {record["status"]}')
    pending = record.get('ci') or {}
    if (not isinstance(ci, dict) or ci.get('state') not in SETTLED_CI or ci.get('head') != head or
            record['head'] != head or pending.get('head') != head):
        raise ValueError('settled exact-head CI evidence required')
    record.update(ci=ci, status='ci_settled', updated_at=stamp())


def handoff(state, number, batch, reason, kind=None):
    require_idle(state, number)
    record = batch_record(state, number, batch)
    if record['status'] in TERMINAL:
        raise ValueError(f'batch already terminal ({record["status"]})')
    if not reason.strip():
        raise ValueError('handoff reason required')
    resolved_kind = infer_handoff_kind(reason, kind)
    record.update(status='handoff', handoff_reason=reason, handoff_kind=resolved_kind, updated_at=stamp())


def complete(state, number, batch, entry, settled):
    require_idle(state, number)
    record = batch_record(state, number, batch)
    ci = record.get('ci') or {}
    if record['status'] == 'ci_settled':
        if ci.get('state') != 'passed' or ci.get('head') != record['head']:
            raise ValueError('completion requires passed CI on the exact pushed head')
    elif record['status'] == 'no_push':
        if ci.get('state') != 'not_required' or record['commits']:
            raise ValueError('no_push completion requires no pushed commits')
    else:
        raise ValueError(f'completion requires ci_settled or no_push, batch is {record["status"]}')
    head = entry.get('currentHeadSha') or entry.get('publishedHeadSha') or entry.get('initialHeadSha')
    if entry.get('status') == 'skipped' or head != record['head'] or not settled(entry):
        raise ValueError('completion requires a matching settled respond ledger on the recorded head')
    record.update(status='completed', updated_at=stamp())


def normalized(rows, defaults, key=None):
    result = []
    for name, row in rows:
        row = row if isinstance(row, dict) else {'invalid': row}
        merged = {**copy.deepcopy(defaults), **row}
        if key:
            merged.setdefault(key, name)
        if merged.get('status') == 'handoff' and not merged.get('handoff_kind'):
            merged['handoff_kind'] = infer_handoff_kind(merged.get('handoff_reason'))
        result.append(merged)
    return result


def handoffs_grouped(prs):
    grouped = {kind: [] for kind in HANDOFF_KINDS}
    for row in prs:
        if row.get('status') == 'handoff':
            kind = row.get('handoff_kind') if row.get('handoff_kind') in grouped else infer_handoff_kind(row.get('handoff_reason'))
            grouped[kind].append(row)
    return grouped


def digest(state, day=None):
    jobs = normalized(state.get('jobs', {}).items(), JOB_DEFAULTS, 'token')
    all_prs = normalized(state.get('batches', {}).items(), BATCH_DEFAULTS)
    all_skips = [s if isinstance(s, dict) else {'reason': str(s)} for s in state.get('skips', [])][-SKIP_LIMIT:]
    if day is not None:
        prs = [
            row for row in all_prs
            if row.get('status') not in TERMINAL
            or (row.get('updated_at') and digest_date(row['updated_at']) == day)
        ]
        skips = [s for s in all_skips if s.get('at') and digest_date(s['at']) == day]
        reservations = [j for j in jobs if j.get('status') == 'active']
    else:
        prs, skips, reservations = all_prs, all_skips, jobs
    payload = {'schema_version': 1, 'generated_at': stamp(), 'repository': REPOSITORY,
               'prs': prs, 'handoffs_by_kind': handoffs_grouped(prs),
               'reservations': reservations, 'skips': skips,
               'source': 'Recorded evidence; not a live CI refresh'}
    if day is not None:
        payload['date'] = day.isoformat()
    return payload


def _pr_line(r):
    ci = r.get('ci') if isinstance(r.get('ci'), dict) else {}
    kind = f" [{r['handoff_kind']}]" if r.get('status') == 'handoff' and r.get('handoff_kind') else ''
    return (f"- #{r.get('number')}: {r.get('status')}{kind}; rounds {r.get('fix_rounds')}/2; "
            f"CI {ci.get('state')} {(ci.get('head') or '')[:8]}; {r.get('handoff_reason') or ''}").rstrip()


def digest_markdown(data):
    header = f"# Respond queue for {data['date']}" if data.get('date') else '# Respond queue'
    lines = [header, '', data['generated_at'], '']
    grouped = data.get('handoffs_by_kind') or handoffs_grouped(data.get('prs', []))
    sections = (
        ("Needs Alexis's decision", grouped.get('needs_decision', [])),
        ('Waiting on external sign-off', grouped.get('external_hold', [])),
        ('Blocked by CI / infrastructure', grouped.get('infra_blocked', [])),
    )
    for title, rows in sections:
        if rows:
            lines.extend([f'## {title}', ''])
            lines.extend(_pr_line(r) for r in rows)
            lines.append('')
    other = [r for r in data.get('prs', []) if r.get('status') != 'handoff']
    if other:
        lines.extend(['## Completed and in progress', ''])
        lines.extend(_pr_line(r) for r in other)
        lines.append('')
    for j in data.get('reservations', []):
        if j.get('status') == 'active':
            lines.append(f"- Active {j.get('phase')} #{j.get('number')} thread {j.get('thread')} token {j.get('token')}")
    for s in data.get('skips', [])[-20:]:
        lines.append(f"- Skip: {s.get('url') or s.get('command') or ''} {s.get('reason')}".rstrip())
    return '\n'.join(lines).rstrip() + '\n'


def export_dated_digest(root, state, day):
    data = digest(state, day=day)
    directory = root / 'digest'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'{day.isoformat()}.json'
    write_atomic(directory / f'{day.isoformat()}.md', digest_markdown(data))
    save(target, data)
    key = day.isoformat()
    if key in state.get('pending_digest_dates', []):
        state['pending_digest_dates'].remove(key)
    return target


def record_pending_date(state, day):
    key = day.isoformat()
    if key not in state['pending_digest_dates']:
        state['pending_digest_dates'].append(key)


def try_export_today(root, state):
    day = digest_date(datetime.now(timezone.utc))
    record_pending_date(state, day)
    try:
        export_dated_digest(root, state, day)
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
    p.add_argument('--snapshot', type=Path, required=True,
                   help='analysis/publication plan or snapshot file holding the original humanFeedbackSnapshot')
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
    p.add_argument('--kind', choices=HANDOFF_KINDS)
    d = sub.add_parser('digest').add_mutually_exclusive_group()
    d.add_argument('--date', type=date.fromisoformat)
    d.add_argument('--retry-pending', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'digest':
            with locked(args.state) as state:
                if args.retry_pending:
                    exports, failures = [], {}
                    for key in list(state['pending_digest_dates']):
                        try:
                            exports.append(str(export_dated_digest(args.state, state, date.fromisoformat(key))))
                        except OSError as exc:
                            failures[key] = str(exc)
                    print(json.dumps({'exports': exports, 'failures': failures}))
                    return 2 if failures else 0
                data = digest(state)
                save(args.state/'digest-index.json', data)
                write_atomic(args.state/'digest.md', digest_markdown(data))
                target_day = args.date or digest_date(datetime.now(timezone.utc))
                dated_path = export_dated_digest(args.state, state, target_day)
            print(dated_path if args.date else args.state/'digest-index.json'); return 0
        if args.command == 'observe-ci':
            ci = json.loads(args.ci.read_text())
            with locked(args.state) as state:
                observe_ci(state, args.number, args.batch, args.head, ci)
                try_export_today(args.state, state)
            return 0
        if args.command == 'handoff':
            with locked(args.state) as state:
                handoff(state, args.number, args.batch, args.reason, args.kind)
                try_export_today(args.state, state)
            return 0
        if args.command == 'complete':
            _, history, _ = companions(args.respond_all, args.respond)
            entry = ledger_entry(history.ledger, args.run_dir, args.number, args.batch)
            with locked(args.state) as state:
                complete(state, args.number, args.batch, entry, history.settled)
                try_export_today(args.state, state)
            return 0
        if args.command == 'finish':
            outcome = json.loads(args.outcome.read_text())
            if not isinstance(outcome, dict):
                raise ValueError('outcome must be a JSON object')
            with locked(args.state) as state:
                finish(state, args.token, outcome, args.cleanup_verified)
                try_export_today(args.state, state)
            return 0
        collector, history, snapshotter = companions(args.respond_all, args.respond)
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
            selected, skipped, holds = [], list(dropped), []
            for match in filtered['matches']:
                try:
                    pr, requests = snapshot(match['number'], match['untouched'])
                    entry = {**match, 'feedback_batch': batch_id(match['untouched']),
                             'headSha': pr['head']['sha']}
                    if is_signoff_hold(match['untouched'], requests):
                        holds.append(entry)
                    else:
                        selected.append(entry)
                except ValueError as exc:
                    skipped.append({'url': match['url'], 'reason': str(exc)})
            with locked(args.state) as state:
                for row in holds:
                    key = f"{row['number']}:{row['feedback_batch']}"
                    if key not in state['batches']:
                        state['batches'][key] = dict(
                            copy.deepcopy(BATCH_DEFAULTS), number=row['number'],
                            feedback_batch=row['feedback_batch'], head=row['headSha'],
                            status='handoff', handoff_reason=HOLD_REASON, handoff_kind='external_hold',
                            updated_at=stamp(),
                        )
                        skipped.append({'url': row['url'], 'reason': HOLD_REASON, 'head': row['headSha']})
                        try_export_today(args.state, state)
                    else:
                        existing = state['batches'][key]
                        skipped.append({'url': row['url'],
                                        'reason': f"unchanged feedback batch; already {existing.get('status', 'recorded')}",
                                        'head': row['headSha']})
                remaining = []
                for row in selected:
                    key = f"{row['number']}:{row['feedback_batch']}"
                    if key in state['batches']:
                        existing = state['batches'][key]
                        reason = (f"unchanged feedback batch; already {existing.get('status')}"
                                  if existing.get('status') in TERMINAL
                                  else 'unchanged feedback batch; resume saved run')
                        skipped.append({'url': row['url'], 'reason': reason, 'head': row['headSha']})
                    else:
                        remaining.append(row)
                add_skips(state, ({**s, 'at': stamp(), 'command': 'plan'} for s in skipped), dedupe=True)
            save(args.output, {**filtered, 'matches': remaining, 'skips': skipped})
            print(json.dumps({'selection': str(args.output), 'count': len(remaining)})); return 0
        if args.command == 'claim':
            if not args.host_ready:
                raise ValueError('Mac busy, offline or status unknown')
            entry = ledger_entry(history.ledger, args.run_dir, args.number, args.batch)
            pr, _ = snapshot(args.number, entry['untouched'])
            if pr['head']['sha'] != args.head:
                raise ValueError('head moved; refresh analysis')
            with locked(args.state) as state:
                token = reserve(state, args.phase, args.number, args.head, args.batch,
                                args.run_dir.resolve(), args.thread, args.host_ready, state_root=args.state)
            print(json.dumps({'token': token})); return 0
        # guard: read the reservation, verify GitHub without holding the lock, then recheck.
        with locked(args.state) as state:
            job = copy.deepcopy(state['jobs'].get(args.token))
        if not job or job['status'] != 'active' or job['phase'] != 'fix':
            raise ValueError('mutation requires active fixer reservation')
        expected = frozen_snapshot(args.snapshot)
        batch = job['batch_key'].split(':', 1)[1]
        entry = ledger_entry(history.ledger, job['run_dir'], job['number'], batch)
        pr, _ = snapshot(job['number'], entry['untouched'])
        if pr['head']['sha'] != args.head:
            raise ValueError('remote head moved; stop mutation')
        if live_feedback(snapshotter, job['number']) != expected:
            raise ValueError('human feedback changed since analysis; stop mutation')
        with locked(args.state) as state:
            if state['jobs'].get(args.token) != job:
                raise ValueError('reservation changed during guard; stop mutation')
        print(json.dumps({'eligible': True, 'head': args.head})); return 0
    except Exception as exc:
        with locked(args.state) as state:
            add_skips(state, [{'at': stamp(), 'command': args.command, 'reason': str(exc)}])
        print(json.dumps({'status': 'skipped', 'reason': str(exc)})); return 2


if __name__ == '__main__':
    sys.exit(main())
