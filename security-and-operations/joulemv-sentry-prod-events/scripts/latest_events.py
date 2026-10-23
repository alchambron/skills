#!/usr/bin/env python3
"""Read production error events using the installed Sentry credential wrapper."""
import contextlib
import io
import json
import runpy
import sys
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

WRAPPER = '/Users/alchambron/.config/sentry/codex-sentry.py'
PROJECTS = ('joulemv-backend-prod', 'joulemv-frontend-prod')
QUERY = 'level:error !error.type:ExpiredJwtException'
TORONTO = ZoneInfo('America/Toronto')


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def call(project, command, *args):
    # The plugin lacks a sort flag. Issue lists accept an empty statsPeriod;
    # issue-events reject it, so omit it there to avoid a hidden 14-day limit.
    original_encode = urllib.parse.urlencode
    original_argv = sys.argv

    def encode(params, *positional, **keywords):
        params = dict(params)
        if command == 'list-issues':
            params['sort'] = 'date'
        elif params.get('statsPeriod') == '':
            params.pop('statsPeriod')
        return original_encode(params, *positional, **keywords)

    output = io.StringIO()
    try:
        urllib.parse.urlencode = encode
        sys.argv = [WRAPPER, '--org', 'enerzam-0h', '--project', project,
                    command, *args]
        with contextlib.redirect_stdout(output):
            runpy.run_path(WRAPPER, run_name='__main__')
        return json.loads(output.getvalue())
    finally:
        urllib.parse.urlencode = original_encode
        sys.argv = original_argv


def latest(project):
    filters = ('--time-range', '', '--environment', 'production', '--query', QUERY)
    issues = call(project, 'list-issues', *filters, '--limit', '50')
    issues.sort(key=lambda issue: timestamp(issue['lastSeen']), reverse=True)
    events = {}
    complete = len(issues) < 50
    for issue in issues:
        ranked = sorted(events.values(), key=lambda e: timestamp(e['timestamp']), reverse=True)
        if len(ranked) >= 5 and timestamp(issue['lastSeen']) < timestamp(ranked[4]['timestamp']):
            complete = True
            break
        records = call(project, 'issue-events', issue['id'], *filters, '--limit', '5')
        for event in records:
            metadata = event.get('metadata') or {}
            if metadata.get('type') == 'ExpiredJwtException':
                raise RuntimeError('Sentry returned an expired JWT despite the exclusion filter.')
            tags = {tag['key']: tag['value'] for tag in event.get('tags', [])}
            event_id = event['eventID']
            date = event['dateCreated']
            events[event_id] = {
                'timestamp': date,
                'toronto_time': timestamp(date).astimezone(TORONTO).strftime('%Y-%m-%d %H:%M:%S %Z'),
                'endpoint_or_task': event.get('culprit'),
                'error_type': metadata.get('type'),
                'error': metadata.get('value') or event.get('title'),
                'release': tags.get('release'),
                'event_id': event_id,
                'url': issue['permalink'].rstrip('/') + '/events/' + event_id + '/',
            }
    ranked = sorted(events.values(), key=lambda e: timestamp(e['timestamp']), reverse=True)[:5]
    return {'project': project, 'events': ranked,
            'warning': None if complete else 'Reached 50 issues without proving the latest-five boundary; report may be incomplete.'}


if __name__ == '__main__':
    print(json.dumps({'projects': [latest(project) for project in PROJECTS]}, indent=2))
