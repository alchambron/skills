"""Export recorded review results by Toronto date without network access."""

from datetime import date, datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo


REPOSITORY = 'EnerZam/JouleMV'
TORONTO = ZoneInfo('America/Toronto')


def digest_date(timestamp: datetime) -> date:
    if timestamp.utcoffset() is None:
        raise ValueError('digest timestamps must include a timezone')
    return timestamp.astimezone(TORONTO).date()


def review_result(token, job, result):
    number = job['number']
    outcome = result['status']
    path = result['html_path']
    if outcome == 'completed' and path:
        if result.get('verdict') == 'issues':
            ping = f'#{number} changes requested, see report'
        else:
            ping = f'#{number} review ready, check before approving'
    else:
        label = 'incomplete' if outcome == 'completed' else outcome
        detail = 'see report' if path else 'report unavailable'
        ping = f'#{number} review {label}, {detail}'
    return {
        'token': token,
        'pr': number,
        'head_sha': job['pins']['head'],
        'base_sha': job['pins']['base'],
        'outcome': outcome,
        'verdict': result['verdict'],
        'html_path': path,
        'finished_at': job['finished_at'],
        'ping': ping,
        'reason': result.get('reason'),
        'live': result.get('live'),
        'publication': result.get('publication'),
        'thread': job['thread'],
        'run_dir': job['run_dir'],
    }


def snapshot(state, day):
    reviews, reservations = [], []
    for token, job in state['jobs'].items():
        if job['status'] == 'active':
            reservations.append({**job, 'token': token})
        elif job['status'] == 'finished' and job.get('result'):
            if digest_date(datetime.fromisoformat(job['finished_at'])) == day:
                reviews.append(review_result(token, job, job['result']))
    reviews.sort(key=lambda row: datetime.fromisoformat(row['finished_at']), reverse=True)
    return {
        'schema_version': 1,
        'date': day.isoformat(),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'repository': REPOSITORY,
        'reviews': reviews,
        'reservations': reservations,
        'skips': [row for row in state.get('skips', [])
                  if row.get('at') and digest_date(datetime.fromisoformat(row['at'])) == day],
    }


def one_line(value):
    plain = ' '.join(str(value).split())
    escaped = html.escape(plain, quote=False).replace('@', '&#64;')
    return re.sub(r'([\\`*_\[\]~])', r'\\\1', escaped)


def render_markdown(data):
    lines = [f"# Review queue for {data['date']}", '']
    pings = {}
    for row in data['reviews']:
        pings.setdefault(row['pr'], row['ping'])
    lines.extend(f'- {ping}' for ping in pings.values())

    lines.extend(['', '## Recorded results', '', f"Exported {data['generated_at']}.", ''])
    if not data['reviews']:
        lines.append('No finished reviews recorded for this date.')
    for row in data['reviews']:
        lines.append(f"- #{row['pr']} at {one_line(row['finished_at'])} · head {one_line(row['head_sha'])}"
                     f" · {one_line(row['outcome'])} · verdict {one_line(row['verdict'])}")
        for label, value in (('Runtime', row['live']), ('Publication', row['publication']),
                             ('Report', row['html_path']), ('Reason', row['reason'])):
            if value:
                lines.append(f'  - {label} · {one_line(value)}')

    if data['reservations']:
        lines.extend(['', '## Active reservations at export', ''])
        for job in data['reservations']:
            lines.append(f"- #{job['number']} · {one_line(job['phase'])} · thread {one_line(job['thread'])}"
                         f" · token {one_line(job['token'])}")
            if job.get('defer_reason'):
                lines.append(f"  - {one_line(job['defer_reason'])}")

    if data['skips']:
        lines.extend(['', '## Skips and deferrals', ''])
        for row in data['skips']:
            target = f"#{row['number']} · " if row.get('number') else ''
            lines.append(f"- {one_line(row['at'])} · {target}{one_line(row['reason'])}")

    return '\n'.join(lines) + '\n'


def write_atomic(path, content):
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as file:
        staged = Path(file.name)
        try:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


def export(root: Path, state: dict, day: date) -> Path:
    """Write this day's JSON and Markdown. Serialize concurrent exports at the caller."""
    data = snapshot(state, day)
    directory = root / 'digest'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'{day}.json'
    write_atomic(directory / f'{day}.md', render_markdown(data))
    write_atomic(target, json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    return target
