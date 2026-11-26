"""Export recorded review results by Toronto date without network access."""

from datetime import date, datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo


DEFAULT_REPOSITORY = 'EnerZam/JouleMV'
TORONTO = ZoneInfo('America/Toronto')


def digest_date(timestamp: datetime) -> date:
    if timestamp.utcoffset() is None:
        raise ValueError('digest timestamps must include a timezone')
    return timestamp.astimezone(TORONTO).date()


def repo_of(row):
    repo = row.get('repository')
    return repo if isinstance(repo, str) and repo.strip() else DEFAULT_REPOSITORY


def pr_key_of(row):
    existing = row.get('pr_key')
    if isinstance(existing, str) and '#' in existing:
        return existing
    number = row.get('pr') if row.get('pr') is not None else row.get('number')
    if number is None:
        return None
    return f'{repo_of(row)}#{number}'


def review_result(token, job, result):
    repo = repo_of(job)
    number = job['number']
    key = f'{repo}#{number}'
    outcome = result['status']
    path = result.get('html_path')
    ping_alexis = bool(result.get('ping_alexis'))
    if outcome == 'completed' and path:
        if ping_alexis:
            ping = f'{key} re-requested at reviewed head, needs Alexis decision'
        elif result.get('verdict') == 'issues':
            ping = f'{key} changes requested, see report'
        else:
            ping = f'{key} review ready, check before approving'
    else:
        label = 'incomplete' if outcome == 'completed' else outcome
        detail = 'see report' if path else 'report unavailable'
        ping = f'{key} review {label}, {detail}'
    return {
        'token': token,
        'repository': repo,
        'pr_key': key,
        'pr': number,
        'url': job.get('url') or f'https://github.com/{repo}/pull/{number}',
        'head_sha': job['pins']['head'],
        'base_sha': job['pins']['base'],
        'outcome': outcome,
        'verdict': result['verdict'],
        'html_path': path,
        'summary_path': result.get('summary_path'),
        'review_url': result.get('review_url'),
        'finished_at': job['finished_at'],
        'ping': ping,
        'ping_alexis': ping_alexis,
        'reason': result.get('reason'),
        'live': result.get('live'),
        'publication': result.get('publication'),
        'thread': job['thread'],
        'run_dir': job['run_dir'],
    }


def _normalize_skip(row):
    item = dict(row)
    number = item.get('number')
    if number is not None:
        repo = repo_of(item)
        item['repository'] = repo
        item['pr_key'] = pr_key_of(item)
    return item


def _dedupe_skips(skips):
    seen = set()
    unique = []
    for row in skips:
        identity = (
            row.get('pr_key'),
            row.get('command'),
            row.get('reason'),
            row.get('head'),
            row.get('base'),
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(row)
    return unique


def snapshot(state, day):
    reviews, reservations = [], []
    for token, job in state['jobs'].items():
        if job['status'] == 'active':
            repo = repo_of(job)
            reservations.append({**job, 'token': token, 'repository': repo, 'pr_key': pr_key_of(job)})
        elif job['status'] == 'finished' and job.get('result'):
            if digest_date(datetime.fromisoformat(job['finished_at'])) == day:
                reviews.append(review_result(token, job, job['result']))
    reviews.sort(key=lambda row: datetime.fromisoformat(row['finished_at']), reverse=True)
    raw_skips = [
        _normalize_skip(row)
        for row in state.get('skips', [])
        if isinstance(row, dict) and row.get('at') and digest_date(datetime.fromisoformat(row['at'])) == day
    ]
    skips = _dedupe_skips(raw_skips)
    repos_by_lower = {}
    for collection in (reviews, reservations, skips):
        for row in collection:
            repo = row.get('repository')
            if isinstance(repo, str) and repo.strip():
                repos_by_lower.setdefault(repo.casefold(), repo)
    repositories = sorted(repos_by_lower.values(), key=str.casefold)
    return {
        'schema_version': 2,
        'date': day.isoformat(),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'repositories': repositories,
        'reviews': reviews,
        'reservations': reservations,
        'skips': skips,
    }


def one_line(value):
    plain = ' '.join(str(value).split())
    escaped = html.escape(plain, quote=False).replace('@', '&#64;')
    return re.sub(r'([\\`*_\[\]~])', r'\\\1', escaped)


def safe_url(value):
    text = str(value).strip()
    if text.startswith('https://') and not re.search(r'[\s<>"\'`\\]', text):
        return f'<{text}>'
    return one_line(text)


def safe_path(value):
    plain = ' '.join(str(value).split())
    if '`' not in plain and '<' not in plain and '>' not in plain:
        return f'`{plain}`'
    return one_line(plain)


def render_markdown(data):
    lines = [f"# Review queue for {data['date']}", '']
    pings = {}
    for row in data['reviews']:
        key = row.get('pr_key') or f"{repo_of(row)}#{row['pr']}"
        pings.setdefault(key.casefold(), row['ping'])
    lines.extend(f'- {ping}' for ping in pings.values())

    attention = {}
    for row in data['skips']:
        key = pr_key_of(row)
        if key and row.get('status') == 'needs_attention' and row.get('ping') and row.get('url'):
            attention[key.casefold()] = row
    if attention:
        lines.extend(['', '## Needs attention', ''])
        for row in attention.values():
            url = row['url']
            message = row['ping'].removeprefix(f'{url} ')
            lines.append(f'- {safe_url(url)} {one_line(message)}')

    lines.extend(['', '## Recorded results', '', f"Exported {data['generated_at']}.", ''])
    if not data['reviews']:
        lines.append('No finished reviews recorded for this date.')
    for row in data['reviews']:
        key = row.get('pr_key') or f"{repo_of(row)}#{row['pr']}"
        lines.append(f"- {one_line(key)} at {one_line(row['finished_at'])} · head {one_line(row['head_sha'])}"
                     f" · {one_line(row['outcome'])} · verdict {one_line(row['verdict'])}")
        for label, value, formatter in (('Runtime', row.get('live'), one_line),
                                        ('Publication', row.get('publication'), one_line),
                                        ('Review', row.get('review_url'), safe_url),
                                        ('Report', row.get('html_path'), safe_path),
                                        ('Summary', row.get('summary_path'), safe_path),
                                        ('Reason', row.get('reason'), one_line)):
            if value:
                lines.append(f'  - {label} · {formatter(value)}')

    if data['reservations']:
        lines.extend(['', '## Active reservations at export', ''])
        for job in data['reservations']:
            key = pr_key_of(job) or f"#{job['number']}"
            lines.append(f"- {one_line(key)} · {one_line(job['phase'])} · thread {one_line(job['thread'])}"
                         f" · token {one_line(job['token'])}")
            if job.get('defer_reason'):
                lines.append(f"  - {one_line(job['defer_reason'])}")

    if data['skips']:
        lines.extend(['', '## Skips and deferrals', ''])
        for row in data['skips']:
            key = pr_key_of(row)
            if key:
                target = f'{one_line(key)} · '
            elif row.get('repository'):
                target = f"{one_line(row['repository'])} · "
            else:
                target = ''
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
