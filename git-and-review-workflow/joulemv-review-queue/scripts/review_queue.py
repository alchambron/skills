#!/usr/bin/env python3
"""Read-only JouleMV collection, Jev classification, and Teams rendering."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import tempfile
from urllib.parse import quote
from zoneinfo import ZoneInfo
import merge_signals as signals
import typesafe_triage as jev

REPO = 'EnerZam/JouleMV'
ACTOR = 'author { login __typename }'
COMMENT = f'id {ACTOR} body createdAt updatedAt url'
CONNECTIONS = {
    'reviews': f'id {ACTOR} body updatedAt submittedAt state url commit {{ oid }}',
    'comments': COMMENT,
    'reviewRequests': 'requestedReviewer { __typename ... on User { login } ... on Team { slug } }',
    'commits': 'commit { oid committedDate parents { totalCount } }',
    'timelineItems': '... on ReviewRequestedEvent { createdAt requestedReviewer { __typename ... on User { login } ... on Team { slug } } }',
    'reviewThreads': f'id isResolved isOutdated comments(first:100) {{ pageInfo {{ hasNextPage endCursor }} nodes {{ {COMMENT} pullRequestReview {{ id }} }} }}',
    'files': 'path additions deletions changeType',
}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))


class GitHub:
    def __init__(self, incremental=False):
        self.incremental = incremental
        self.calls = 0
        self.lock = threading.Lock()

    def command(self, *args, acceptable=(0,)):
        with self.lock:
            self.calls += 1
        proc = subprocess.run(['gh', *args], capture_output=True, text=True, timeout=90)
        if proc.returncode not in acceptable:
            raise RuntimeError(f'GitHub command failed ({proc.returncode}): {args[0]}')
        try:
            return json.loads(proc.stdout)
        except ValueError:
            raise RuntimeError('GitHub returned no usable JSON') from None

    def graphql(self, query):
        data = self.command('api', 'graphql', '-f', 'query=' + query)
        if data.get('errors'):
            raise RuntimeError('GitHub GraphQL returned errors')
        return data['data']

    def inventory(self):
        pages = self.command('api', '--method', 'GET', f'repos/{REPO}/pulls?state=open&per_page=100', '--paginate', '--slurp')
        return [item for page in pages for item in page]

    def pr_query(self, number, fields):
        return self.graphql(f'query {{ repository(owner:"EnerZam",name:"JouleMV") {{ pullRequest(number:{number}) {{ {fields} }} }} }}')['repository']['pullRequest']

    def evidence_fields(self, fields):
        return fields.replace(' body ', ' ') if self.incremental else fields

    def pr_fields(self):
        fields = ' '.join(f'{name}(first:100) {{ pageInfo {{ hasNextPage endCursor }} nodes {{ {self.evidence_fields(node)} }} }}' for name, node in CONNECTIONS.items())
        return ('number title url description: body createdAt isDraft headRefOid baseRefName updatedAt mergeable reviewDecision changedFiles '
                'labels(first:50) { nodes { name } } author { login __typename } ' + fields)

    def initial_batch(self, items):
        selections = ' '.join(f'p{item["number"]}: pullRequest(number:{item["number"]}) {{ {self.pr_fields()} }}' for item in items)
        return self.graphql('query { repository(owner:"EnerZam",name:"JouleMV") { ' + selections + ' } }')['repository']

    def collect_pr(self, item, prefetched=None):
        pr = prefetched if prefetched is not None else self.pr_query(item['number'], self.pr_fields())
        for name, node in CONNECTIONS.items():
            connection = pr[name]
            nodes = list(connection['nodes'])
            while connection['pageInfo']['hasNextPage']:
                cursor = json.dumps(connection['pageInfo']['endCursor'])
                connection = self.pr_query(item['number'], f'{name}(first:100,after:{cursor}) {{ pageInfo {{ hasNextPage endCursor }} nodes {{ {self.evidence_fields(node)} }} }}')[name]
                nodes.extend(connection['nodes'])
            pr[name] = nodes
        for thread in pr['reviewThreads']:
            connection = thread['comments']
            nodes = list(connection['nodes'])
            while connection['pageInfo']['hasNextPage']:
                cursor = json.dumps(connection['pageInfo']['endCursor'])
                query = f'query {{ node(id:{json.dumps(thread["id"])}) {{ ... on PullRequestReviewThread {{ comments(first:100,after:{cursor}) {{ pageInfo {{ hasNextPage endCursor }} nodes {{ {self.evidence_fields(COMMENT)} pullRequestReview {{ id }} }} }} }} }} }}'
                connection = self.graphql(query)['node']['comments']
                nodes.extend(connection['nodes'])
            thread['comments'] = nodes
        pr['assignees'] = [x['login'] for x in item.get('assignees', [])]
        return pr

    def rules(self, base):
        result = {}
        for name, endpoint in [('protection', f'branches/{quote(base, safe="")}/protection'), ('rules', f'rules/branches/{quote(base, safe="")}')]:
            try:
                result[name] = self.command('api', '--method', 'GET', f'repos/{REPO}/{endpoint}')
            except RuntimeError:
                result[name] = None
        return result


def body_records(pr):
    yield from pr['reviews']
    yield from pr['comments']
    for thread in pr['reviewThreads']:
        yield from thread['comments']


def refresh_bodies(gh, prs, cache_path, workers):
    """Refresh every event's metadata; reuse text only at the same updatedAt."""
    try:
        saved = json.loads(cache_path.read_text())
        cached = saved['bodies'] if saved.get('repo') == REPO and saved.get('version') == 1 else {}
        if not isinstance(cached, dict):
            cached = {}
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        cached = {}
    records = {r['id']: r for pr in prs for r in body_records(pr)}
    bodies, missing = {}, []
    for oid, record in records.items():
        old = cached.get(oid)
        if isinstance(record.get('body'), str):
            bodies[oid] = {'body': record['body'], 'updatedAt': record.get('updatedAt')}
        elif (isinstance(old, dict) and record.get('updatedAt')
                and old.get('updatedAt') == record['updatedAt'] and isinstance(old.get('body'), str)):
            bodies[oid] = old
        else:
            missing.append(oid)
    hits = sum('body' not in records[oid] for oid in bodies)
    def fetch(ids):
        query = ('query { nodes(ids:' + json.dumps(ids) + ') { '
                 '... on IssueComment { id body updatedAt } '
                 '... on PullRequestReview { id body updatedAt } '
                 '... on PullRequestReviewComment { id body updatedAt } } }')
        try:
            return gh.graphql(query)['nodes']
        except (RuntimeError, subprocess.TimeoutExpired):
            return []
    chunks = [missing[i:i+50] for i in range(0, len(missing), 50)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for nodes in pool.map(fetch, chunks):
            for node in nodes:
                if (isinstance(node, dict) and node.get('id') in records
                        and isinstance(node.get('body'), str)
                        and node.get('updatedAt') == records[node['id']].get('updatedAt')):
                    bodies[node['id']] = node
    complete, errors = [], []
    for pr in prs:
        if any(r['id'] not in bodies for r in body_records(pr)):
            errors.append(f"#{pr['number']}: discussion text unavailable or changed during collection; omitted.")
            continue
        for record in body_records(pr):
            record['body'] = bodies[record['id']]['body']
        complete.append(pr)
    cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w', dir=cache_path.parent, delete=False) as out:
        json.dump({'repo': REPO, 'version': 1, 'bodies': bodies}, out)
    os.replace(out.name, cache_path)
    return complete, errors, {'body_cache_hits': hits, 'bodies_requested': len(missing)}


def collect(workers, body_cache=None):
    start = time.perf_counter()
    gh = GitHub(incremental=body_cache is not None and body_cache.exists())
    inventory = gh.inventory()
    errors = []
    def fetch(item, prefetched=None):
        try:
            return gh.collect_pr(item, prefetched)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            errors.append(f'#{item["number"]}: {type(exc).__name__}')
            return None
    def fetch_batch(items):
        try:
            data = gh.initial_batch(items)
        except (RuntimeError, subprocess.TimeoutExpired):
            # Preserve coverage if GitHub rejects a large query.
            data = {}
        return [p for item in items if (p := fetch(item, data.get(f'p{item["number"]}'))) is not None]
    chunks = [inventory[i:i+8] for i in range(0, len(inventory), 8)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        prs = [p for batch in pool.map(fetch_batch, chunks) for p in batch]
    refreshed = gh.inventory()
    old = {p['number']: (p['updated_at'], p['head']['sha']) for p in inventory}
    changed = [p for p in refreshed if old.get(p['number']) != (p['updated_at'], p['head']['sha'])]
    current = {p['number'] for p in refreshed}
    prs = [p for p in prs if p['number'] in current and p['number'] not in {c['number'] for c in changed}]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        prs.extend(p for p in pool.map(fetch, changed) if p)
    if changed:
        errors.append('PRs changed during collection and were refreshed; snapshot is not atomic.')
    body_metrics = {}
    if body_cache is not None:
        prs, body_errors, body_metrics = refresh_bodies(gh, prs, body_cache, workers)
        errors.extend(body_errors)
    rules = {base: gh.rules(base) for base in {p['baseRefName'] for p in prs}}
    return {'collected_at': datetime.now(ZoneInfo('America/Toronto')).isoformat(), 'prs': sorted(prs, key=lambda p: p['number']),
            'rules': rules, 'errors': errors, 'open_count': len(refreshed),
            'collection_metrics': {'seconds': round(time.perf_counter()-start, 3), 'github_commands': gh.calls, **body_metrics}}


def human(actor):
    if not actor or not actor.get('login'):
        return False
    login = actor['login'].lower()
    return actor.get('__typename') != 'Bot' and not any(x in login for x in ('[bot]', 'coderabbit', 'copilot', 'dependabot', 'github-actions'))


def event(raw, **extra):
    actor = raw.get('author') or {}
    return {'id': raw['id'], 'actor': actor.get('login', 'unknown'), 'actor_type': actor.get('__typename', 'Unknown'),
            'at': raw.get('submittedAt') or raw.get('createdAt') or '', 'body': raw.get('body') or '', 'url': raw.get('url') or '', **extra}


def effective_reviews(pr):
    latest = {}
    for review in sorted(pr['reviews'], key=lambda r: r.get('submittedAt') or ''):
        if human(review.get('author')) and review['state'] in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'):
            latest[review['author']['login']] = review
    return latest


def latest_request_at(pr, reviewer):
    return max((e.get('createdAt', '') for e in pr['timelineItems']
                if (e.get('requestedReviewer') or {}).get('login') == reviewer), default='')


def latest_review_at(pr, reviewer):
    return max((r.get('submittedAt') or '' for r in pr['reviews']
                if human(r.get('author')) and r['author']['login'] == reviewer
                and r['state'] != 'PENDING'), default='')


def current_request_at(pr, reviewer):
    """An active request must also be later than the review that consumed it."""
    if not any((r.get('requestedReviewer') or {}).get('login') == reviewer
               for r in pr['reviewRequests']):
        return None
    requested, reviewed = latest_request_at(pr, reviewer), latest_review_at(pr, reviewer)
    return requested if requested and requested > reviewed else None


def author_handoff_pending(pr, reviewer, feedback_at):
    author = (pr.get('author') or {}).get('login')
    replies = [r for r in body_records(pr) if (r.get('author') or {}).get('login') == author
               and (r.get('submittedAt') or r.get('createdAt') or '') > feedback_at
               and (r.get('body') or '').strip()]
    replied = max((r.get('submittedAt') or r.get('createdAt') or '' for r in replies), default='')
    reviewed = latest_review_at(pr, reviewer)
    return bool(replied and replied > reviewed)


def mentioned_people(body):
    prose = re.sub(r'```[^\n]*\n[\s\S]*?```|`[^`]*`', '', body)
    return set(re.findall(r'(?<![A-Za-z0-9_./])@([A-Za-z0-9-]+)(?![A-Za-z0-9/-]|\s*\()', prose))


def prepare(snapshot):
    cases, obligations = [], []
    for pr in snapshot['prs']:
        author = (pr.get('author') or {}).get('login', 'unknown')
        actors = {(r.get('author') or {}).get('login'): r.get('author') for r in body_records(pr)}
        effective = effective_reviews(pr)
        events, candidates = {}, []
        # Keep the complete human discussion, plus bot roots only for human replies.
        for raw in pr['reviews']:
            if human(raw.get('author')) and raw['state'] != 'PENDING':
                events[raw['id']] = event(raw, review_state=raw['state'], commit=(raw.get('commit') or {}).get('oid'))
                if raw['author']['login'] != author and raw['body'].strip() and raw['state'] == 'COMMENTED':
                    candidates.append((raw, 'feedback'))
        for raw in pr['comments']:
            if human(raw.get('author')):
                events[raw['id']] = event(raw)
                if raw['author']['login'] != author and raw['body'].strip():
                    candidates.append((raw, 'feedback'))
        for thread in pr['reviewThreads']:
            comments = thread['comments']
            if not any(human(c.get('author')) for c in comments):
                continue
            bot_root = bool(comments and not human(comments[0].get('author')))
            for index, raw in enumerate(comments):
                if human(raw.get('author')) or index == 0:
                    events[raw['id']] = event(raw, thread=thread['id'], resolved=thread['isResolved'])
                if not thread['isResolved'] and human(raw.get('author')) and raw['body'].strip():
                    if bot_root:
                        if raw['author']['login'] != author:
                            candidates.append((raw, 'adoption'))
                    elif raw['author']['login'] != author:
                        candidates.append((raw, 'feedback'))
        timeline = sorted(events.values(), key=lambda e: (e['at'], e['id']))
        # Stable shared state allows all questions for a PR to share one payload.
        context = {'head_sha': pr['headRefOid'], 'pr': {'number': pr['number'], 'author': author,
                   'reviews': {who: {'state': r['state'], 'at': r['submittedAt']} for who, r in effective.items()}},
                   'events': timeline, 'context_complete': True}
        seen = set()
        for who, review in effective.items():
            if review['state'] == 'CHANGES_REQUESTED':
                candidates.append((review, 'formal'))
        for raw, kind in candidates:
            if raw['id'] in seen:
                continue
            seen.add(raw['id'])
            who = raw['author']['login']
            at = raw.get('submittedAt') or raw.get('createdAt') or ''
            final = effective.get(who)
            if final and final['state'] == 'APPROVED' and (final['submittedAt'] or '') > at:
                continue
            oid = f'{pr["number"]}:{raw["id"]}'
            obligation = {'id': oid, 'pr': pr['number'], 'reviewer': who, 'at': at, 'url': raw['url'],
                          'formal': kind == 'formal', 'classification': None, 'handoffs': [], 'delegations': []}
            if kind != 'formal':
                cid = oid + ':feedback'
                cases.append(dict(context, id=cid, kind=kind, focal_id=raw['id'], subject=f'The request in event {raw["id"]} from {who}; assess whether a human response or fix is still owed.'))
                obligation['classification'] = cid
            # One judgment per obligation; author replies are evidence, not separate jobs.
            replies = [e for e in timeline if e['actor'] == author and e['at'] > at and e['body'].strip()]
            if replies:
                hid = oid + ':handoff'
                cases.append(dict(context, id=hid, kind='handoff', focal_id=raw['id'], subject=f'Considering all later author replies, is there a CURRENT author handoff to {who} for feedback {raw["id"]}? This focal event is the original feedback. A later request to fix more supersedes an earlier handoff.'))
                obligation['handoffs'].append(hid)
            for reply in replies:
                for person in sorted(mentioned_people(reply['body'])):
                    if person in (author, who) or not human(actors.get(person) or {'login': person}):
                        continue
                    did = oid + ':delegation:' + reply['id'] + ':' + person
                    cases.append(dict(context, id=did, kind='delegation', focal_id=reply['id'], subject=f'{person} designated to address feedback {raw["id"]} from {who}.'))
                    obligation['delegations'].append((did, person))
            obligations.append(obligation)
    return cases, obligations


def required_count(snapshot, pr):
    rule_data = snapshot['rules'].get(pr['baseRefName'], {})
    protection = rule_data.get('protection')
    rules = rule_data.get('rules')
    counts = []
    if protection is not None:
        counts.append((protection.get('required_pull_request_reviews') or {}).get('required_approving_review_count', 0))
    if rules is not None:
        counts.extend(r.get('parameters', {}).get('required_approving_review_count', 0) for r in rules if r['type'] == 'pull_request')
    # One unavailable source can conceal additional requirements.
    return max(counts, default=0), protection is not None and rules is not None


def approval_count(snapshot, pr):
    rule_data = snapshot['rules'].get(pr['baseRefName'], {})
    protection = rule_data.get('protection') or {}
    stale = bool((protection.get('required_pull_request_reviews') or {}).get('dismiss_stale_reviews'))
    stale |= any(r.get('parameters', {}).get('dismiss_stale_reviews_on_push', False) for r in rule_data.get('rules') or [])
    return sum(r['state'] == 'APPROVED' and (not stale or (r.get('commit') or {}).get('oid') == pr['headRefOid']) for r in effective_reviews(pr).values())


def revision_dates(pr):
    """Map each commit to its latest author revision; merge commits only bring in the base."""
    revisions, latest = {}, ''
    for c in sorted(pr['commits'], key=lambda c: c['commit'].get('committedDate') or ''):
        commit = c['commit']
        if (commit.get('parents') or {}).get('totalCount', 1) <= 1:
            latest = max(latest, commit.get('committedDate') or '')
        revisions[commit['oid']] = latest
    return revisions, latest


def review_rounds(pr):
    """Human reviews that assess a revision; a lone thread reply is not a round."""
    roots = {(t['comments'][0].get('pullRequestReview') or {}).get('id') for t in pr['reviewThreads'] if t['comments']}
    author = (pr.get('author') or {}).get('login')
    return sorted((r for r in pr['reviews'] if human(r.get('author')) and r['author']['login'] != author
                   and r['state'] != 'PENDING' and (r['state'] != 'COMMENTED' or (r.get('body') or '').strip() or r['id'] in roots)),
                  key=lambda r: r.get('submittedAt') or '')


def review_pass(pr):
    """Count only feedback -> later revision -> renewed human review transitions.

    Also report whether the latest revision is the one the current pass assessed,
    so an awaited re-review joins that pass rather than starting another."""
    revisions, latest = revision_dates(pr)
    reviews = review_rounds(pr)
    pass_number, feedback_at, revision_at = 1, None, ''
    pass_revision = revisions.get((reviews[0].get('commit') or {}).get('oid'), '') if reviews else ''
    for r in reviews:
        date = revisions.get((r.get('commit') or {}).get('oid'), '')
        if feedback_at and date > feedback_at and date > revision_at and (r.get('submittedAt') or '') >= date:
            pass_number += 1
            revision_at, feedback_at, pass_revision = date, None, date
        if r['state'] == 'CHANGES_REQUESTED':
            feedback_at = r.get('submittedAt') or feedback_at
    return pass_number, bool(reviews), bool(latest) and pass_revision >= latest


def collect_checks(snapshot, actions, workers, extra=()):
    gh = GitHub()
    start = time.perf_counter()
    numbers = sorted({a['pr'] for a in actions} | set(extra))
    def fetch(number):
        try:
            checks = gh.command('pr', 'checks', str(number), '--repo', REPO, '--required', '--json', 'name,state,bucket,link', acceptable=(0,1,8))
            return number, checks
        except (RuntimeError, subprocess.TimeoutExpired):
            return number, None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        checks = dict(pool.map(fetch, numbers))
    for pr in snapshot['prs']:
        if pr['number'] in checks:
            pr['required_checks'] = checks[pr['number']]
    return {'seconds':round(time.perf_counter()-start,3), 'github_commands':gh.calls}


def compose(snapshot, obligations, judgments):
    by_id = {j['id']: j for j in judgments}
    actions, uncertainties = [], []
    prs = {p['number']: p for p in snapshot['prs']}
    def status(cid):
        return by_id.get(cid, {}).get('suggestion', 'unavailable')
    def unresolved(ob, ids, reason):
        failed = [by_id.get(cid, {}) for cid in ids if status(cid) == 'unavailable']
        if failed:
            errors = sorted({j.get('error_type', 'answer_unavailable') for j in failed})
            reason = 'Interpretation unavailable (' + ', '.join(errors) + '); verify the feedback and next owner.'
        uncertainties.append({'pr': ob['pr'], 'reviewer': ob['reviewer'],
                              'evidence': ob['url'], 'reason': reason})
    for ob in obligations:
        pr = prs[ob['pr']]
        author = (pr.get('author') or {}).get('login', 'unknown')
        answer = 'yes' if ob['formal'] else status(ob['classification'])
        handoff_pending = author_handoff_pending(pr, ob['reviewer'], ob['at'])
        handoff = handoff_pending and any(status(cid) == 'yes' for cid in ob['handoffs'])
        request_at = current_request_at(pr, ob['reviewer'])
        formal_handoff = bool(request_at and request_at > ob['at'])
        if handoff and pr['isDraft'] and not formal_handoff:
            continue
        if handoff or formal_handoff:
            actions.append({'pr': ob['pr'], 'owner': ob['reviewer'], 'action': 'Review', 'at': request_at or ob['at'], 'detail': 'Re-review fixes' + (' (inferred handoff).' if handoff and not formal_handoff else '.'), 'evidence': ob['url'], 'rereview': True})
            continue
        if handoff_pending and any(status(cid) in ('uncertain', 'unavailable') for cid in ob['handoffs']):
            unresolved(ob, ob['handoffs'], 'Whether the reviewer has been asked back is uncertain.')
            if not ob['formal']:
                continue
        if answer == 'no':
            continue
        if answer != 'yes':
            unresolved(ob, [ob['classification']], 'Whether this feedback still needs a response is uncertain.')
            continue
        actors = {(r.get('author') or {}).get('login'): r.get('author') for r in body_records(pr)}
        delegations = [(cid, person) for cid, person in ob['delegations']
                       if human(actors.get(person) or {'login': person})]
        owners = {person for cid, person in delegations if status(cid) == 'yes'}
        if any(status(cid) in ('uncertain', 'unavailable') for cid, _ in delegations) or len(owners) > 1:
            unresolved(ob, [cid for cid, _ in delegations], 'Who owes the response to this feedback is uncertain.')
            continue
        owner = next(iter(owners)) if len(owners) == 1 else author
        detail = f'Address {ob["reviewer"]}\'s feedback.'
        if ob['formal'] and any(c['commit'].get('committedDate', '') > ob['at'] for c in pr['commits']):
            detail = f'Address {ob["reviewer"]}\'s feedback, or confirm fixes and request re-review.'
        actions.append({'pr': ob['pr'], 'owner': owner, 'action': 'Respond to human review', 'at': ob['at'], 'detail': detail, 'evidence': ob['url'], 'reviewer': ob['reviewer']})
    for pr in snapshot['prs']:
        present = [a for a in actions if a['pr'] == pr['number']]
        uncertain_reviewers = {u.get('reviewer') for u in uncertainties if u['pr'] == pr['number']}
        eligible_requests = []
        for request in pr['reviewRequests']:
            actor = request.get('requestedReviewer') or {}
            if actor.get('__typename') == 'Team':
                owner = 'Team review requested: ' + actor['slug']
            elif human(actor):
                owner = actor['login']
                eligible_requests.append(owner)
                requested = latest_request_at(pr, owner)
                if requested and requested <= latest_review_at(pr, owner):
                    uncertainties.append({'pr': pr['number'], 'reviewer': owner, 'evidence': pr['url'],
                                          'reason': 'Review request state and chronology disagree; verify whether re-review is still owed.'})
                    continue
                if owner in uncertain_reviewers:
                    continue
                if any(a['action'] == 'Respond to human review' and a.get('reviewer') == owner for a in present):
                    continue
            else:
                continue
            if actor.get('__typename') == 'Team':
                eligible_requests.append(owner)
            again = actor.get('__typename') != 'Team' and bool(latest_review_at(pr, owner))
            actions.append({'pr': pr['number'], 'owner': owner, 'action': 'Review', 'rereview': again,
                            'at': current_request_at(pr, owner) or pr['updatedAt'],
                            'detail': 'Re-review requested.' if again else 'Review requested.'})
        count, known = required_count(snapshot, pr)
        approvals = approval_count(snapshot, pr)
        if (not pr['isDraft'] and not eligible_requests and count > approvals and not present
                and not any(u['pr'] == pr['number'] for u in uncertainties)):
            actions.append({'pr': pr['number'], 'owner': 'Reviewer needed', 'action': 'Review', 'at': pr['updatedAt'], 'detail': 'Required human review has no named reviewer.'})
        if not known and not count and not present and not eligible_requests:
            uncertainties.append({'pr': pr['number'], 'evidence': pr['url'], 'reason': 'Cannot verify all required-review rules.'})
    blocked = {(a['pr'], a.get('reviewer')) for a in actions if a['action'] == 'Respond to human review'}
    dedup = {}
    for a in actions:
        key = (a['pr'], a['owner'], a['action'])
        if key not in dedup:
            dedup[key] = dict(a, reviewers=[a['reviewer']] if a.get('reviewer') else [])
            continue
        kept = dedup[key]
        kept['rereview'] = kept.get('rereview') or a.get('rereview')
        if a.get('reviewer') and a['reviewer'] not in kept['reviewers']:
            kept['reviewers'].append(a['reviewer'])
            confirm = ', or confirm fixes and request re-review' if 'confirm fixes' in kept['detail'] + a['detail'] else ''
            kept['detail'] = f'Address feedback from {" and ".join(kept["reviewers"])}{confirm}.'
    actions = list(dedup.values())
    blocked.update((u['pr'], u.get('reviewer')) for u in uncertainties if u.get('reviewer'))
    actions = [a for a in actions if a['action'] != 'Review' or (a['pr'], a['owner']) not in blocked]
    for pr in snapshot['prs']:
        items = [a for a in actions if a['pr'] == pr['number']]
        if not items:
            continue
        # Conservative lower bound: do not turn reviewer count or commit count into passes.
        response = any(a['action'] == 'Respond to human review' for a in items)
        rereview = any(a.get('rereview') for a in items)
        pass_number, has_history, current_reviewed = review_pass(pr)
        awaited = pass_number if current_reviewed else pass_number + 1
        approvals, (required, _) = approval_count(snapshot, pr), required_count(snapshot, pr)
        if response:
            score = 30 if pass_number == 1 else 60 if pass_number == 2 else 75
        elif rereview:
            score = 50 if awaited <= 2 else 70
        else:
            score = 90 if approvals and approvals >= required else 80 if approvals else 10
        pass_label = f'at least pass {pass_number}; conservative' if has_history else 'pass 1'
        if rereview and not response:
            pass_label = f'at least pass {awaited}; conservative'
        if pr['isDraft']:
            score = 0
        stage_score = score
        if pr.get('mergeable') == 'CONFLICTING':
            score = min(score, 60)
        if any(c.get('bucket') in ('fail', 'cancel') for c in pr.get('required_checks') or []):
            score = min(score, 60)
        for a in items:
            a.update(score=score, stage_score=stage_score, pass_label=pass_label, draft=pr['isDraft'])
    return actions, uncertainties


def blockers(pr):
    found = ['merge conflict'] if pr.get('mergeable') == 'CONFLICTING' else []
    checks = pr.get('required_checks') or []
    if any(c.get('bucket') in ('fail', 'cancel') for c in checks):
        found.append('required checks failed')
    elif any(c.get('bucket') == 'pending' for c in checks):
        found.append('required checks pending')
    return found


def priority_section(snapshot, actions, merge, format_style):
    """Urgent and High non-draft PRs, including ones with no pending human action."""
    prs = {p['number']: p for p in snapshot['prs']}
    rank = {level: index for index, level in enumerate(reversed(signals.PRIORITY))}
    top = sorted((n for n, m in merge.items() if not prs[n]['isDraft'] and m['priority']['level'] in ('Urgent', 'High')),
                 key=lambda n: (rank[merge[n]['priority']['level']], n))
    checkbox = format_style == 'checkbox'
    lines = ['**Merge priority · security first**' if checkbox else 'Merge priority (security first):', '']
    if not top:
        lines.extend(['No Urgent or High merge priorities found.', ''])
    for number in top:
        pr, entry = prs[number], merge[number]
        nexts = sorted({'reviewer needed' if a['owner'] == 'Reviewer needed'
                        else f'{a["action"]} by {a["owner"].replace("Team review requested: ", "team ")}'
                        for a in actions if a['pr'] == number})
        if nexts:
            state = 'Next: ' + '; '.join(nexts) + '.'
        else:
            count, _ = required_count(snapshot, pr)
            state = f'No pending human review action; {approval_count(snapshot, pr)}/{count} required approvals.'
        found = blockers(pr)
        if found:
            state += (' **Blocker:** ' if checkbox else ' Blocker: ') + ', '.join(found) + '.'
        if checkbox:
            lines.extend([f'- **{entry["priority"]["level"]} · #{number}** — {pr["title"]}  ',
                          f'  {signals.summary(entry, level=False)}  ', f'  {state}  ', f'  {pr["url"]}', ''])
        else:
            lines.extend([f'• {entry["priority"]["level"]}: #{number}, {pr["title"]}. {signals.summary(entry, markdown=False, level=False)} {state}', pr['url']])
    verify = sorted(n for n, m in merge.items() if not prs[n]['isDraft'] and m['priority']['security_fix'] == 'uncertain')
    if verify:
        lines.extend(['Security effect unclear, verify: ' + ', '.join(f'#{n}' for n in verify) + '.', ''])
    counts = {level: sum(not prs[n]['isDraft'] and m['priority']['level'] == level for n, m in merge.items())
              for level in signals.PRIORITY}
    lines.append(' · '.join(f'{level} {counts[level]}' for level in reversed(signals.PRIORITY)) + ' (non-draft PRs)')
    return lines


def render(snapshot, actions, uncertainties, format_style='checkbox', merge=None):
    merge = merge or {}
    prs = {p['number']: p for p in snapshot['prs']}
    scores = {a['pr']: a['score'] for a in actions if not a['draft']}
    people = {a['owner'] for a in actions if a['owner'] != 'Reviewer needed' and not a['owner'].startswith('Team review requested:')}
    average = f'{sum(scores.values())/len(scores):.0f}% ({len(scores)} non-draft PRs)' if scores else 'N/A (0 non-draft PRs)'
    pr_count = len({a['pr'] for a in actions})
    if format_style == 'checkbox':
        collected = datetime.fromisoformat(snapshot['collected_at']).strftime('%B %d, %Y · %I:%M %p')
        lines = ['**JouleMV review actions**', '', f'{collected} · America/Toronto', '',
                 f'**{pr_count} PRs · {len(people)} people · Average progress: {average}**']
    else:
        lines = ['JouleMV review actions', snapshot['collected_at'] + ' — America/Toronto',
                 f'{pr_count} PRs; {len(people)} people. Average estimated progress: {average}.']
    if snapshot['errors']:
        label = '**Partial collection:**' if format_style == 'checkbox' else 'Partial collection:'
        lines.append(f'{label} {len(snapshot["prs"])}/{snapshot["open_count"]} open PRs; ' + '; '.join(snapshot['errors']))
    interpretation = snapshot.get('interpretation_metrics') or {}
    # Older snapshots carry only aggregate counts, which were all feedback cases.
    unavailable = interpretation.get('feedback_unavailable', interpretation.get('unavailable_cases'))
    if unavailable:
        label = '**Partial interpretation:**' if format_style == 'checkbox' else 'Partial interpretation:'
        lines.append(f'{label} {unavailable}/{interpretation.get("feedback_cases", interpretation.get("cases"))} judgments unavailable; '
                     'affected feedback and next actions require verification.')
    if interpretation.get('priority_unavailable'):
        label = '**Partial security assessment:**' if format_style == 'checkbox' else 'Partial security assessment:'
        lines.append(f'{label} {interpretation["priority_unavailable"]}/{interpretation["priority_cases"]} judgments unavailable; '
                     'affected priorities say so.')
    if merge:
        if lines[-1]:
            lines.append('')
        lines.extend(priority_section(snapshot, actions, merge, format_style))
    if not actions:
        if lines[-1]:
            lines.append('')
        lines.append('No confirmed pending human review actions found.' if uncertainties or unavailable else 'No pending human review actions found.')
    for owner in sorted({a['owner'] for a in actions}, key=str.lower):
        if format_style == 'checkbox':
            if lines[-1]:
                lines.append('')
            lines.extend([f'**{owner}**', ''])
        else:
            lines.extend(['', owner])
        # Highest merge priority first; unrated entries sort last.
        rank = lambda a: signals.PRIORITY[::-1].index(merge[a['pr']]['priority']['level']) if a['pr'] in merge else len(signals.PRIORITY)
        for a in sorted((a for a in actions if a['owner'] == owner), key=lambda a: (rank(a), a['action'] != 'Respond to human review', a['at'], a['pr'])):
            pr = prs[a['pr']]
            blocker = ' Merge conflict.' if pr.get('mergeable') == 'CONFLICTING' else ''
            checks = pr.get('required_checks')
            if checks is None and format_style == 'detailed':
                blocker += ' Required checks unknown.'
            elif any(c.get('bucket') in ('fail', 'cancel') for c in checks or []):
                blocker += ' Required checks failed.'
            elif format_style == 'detailed' and any(c.get('bucket') == 'pending' for c in checks or []):
                blocker += ' Required checks pending.'
            if a.get('stage_score', a['score']) != a['score']:
                blocker += f' Review stage {a["stage_score"]}% before blocker cap.'
            detail = a['detail']
            if owner == 'Reviewer needed' and format_style == 'checkbox':
                detail = f"Created by {(pr.get('author') or {}).get('login') or 'unknown'}."
            if format_style == 'checkbox':
                pass_label = a['pass_label'].replace('; conservative', ' (conservative)').capitalize()
                if blocker:
                    blocker = blocker.replace(' Merge conflict.', ' **Blocker:** merge conflict.').replace(' Required checks failed.', ' **Blocker:** required checks failed.')
                title = f'- [ ] **{a["action"]} · #{a["pr"]}** — {pr["title"]}  '
                status = f'  **{a["score"]}%** · {pass_label}.' + (' Draft.' if a['draft'] else '') + ' ' + detail + blocker + '  '
                signal = [f'  {signals.summary(merge[a["pr"]])}  '] if a['pr'] in merge else []
                lines.extend([title, status, *signal, f'  {pr["url"]}', ''])
            else:
                entry = f'• {a["action"]}: #{a["pr"]}, {pr["title"]}. {a["pass_label"]}, {a["score"]}%.' + (' Draft.' if a['draft'] else '') + ' ' + detail + blocker
                if a['pr'] in merge:
                    entry += ' ' + signals.summary(merge[a['pr']], markdown=False)
                lines.extend([entry, pr['url']])
            if format_style == 'detailed' and a.get('evidence') and a['evidence'] != pr['url']:
                lines.append(a['evidence'])
    if uncertainties:
        label = '**Uncertain evidence**' if format_style == 'checkbox' else 'Uncertain evidence:'
        separator = ' · ' if format_style == 'checkbox' else ' '
        if lines[-1]:
            lines.append('')
        lines.append(f'{label}{separator}{len(uncertainties)} items across {len({u["pr"] for u in uncertainties})} PRs; these are not confirmed personal tasks.')
        if format_style == 'checkbox':
            lines.append('')
        for number in sorted({u['pr'] for u in uncertainties}):
            number_label = f'**#{number}**' if format_style == 'checkbox' else f'#{number}'
            lines.append(f'• {number_label}: ' + ' '.join(sorted({u['reason'] for u in uncertainties if u['pr'] == number})))
            if format_style == 'detailed':
                lines.append(prs[number]['url'])
            else:
                lines.append('')
    footer = 'Percentages estimate workflow progress conservatively; later confirmed human passes advance the score, subject to merge blockers. Detailed merge gates are not certified.'
    if merge:
        footer += (' Priority ranks merge urgency, security first (Urgent, High, Medium, Low);'
                   ' Attention ranks review care from changed files (Critical, Careful, Standard, Light).')
    if lines[-1]:
        lines.append('')
    lines.append(f'*{footer}*' if format_style == 'checkbox' else footer)
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, help='Replay a saved snapshot without contacting GitHub')
    parser.add_argument('--collect-only', action='store_true')
    parser.add_argument('--workers', type=int, choices=range(1, 9), default=8)
    parser.add_argument('--model', default='jev-1.13.0')
    parser.add_argument('--format', dest='format_style', choices=('checkbox', 'detailed'), default='checkbox',
                        help='Report layout; checkbox is the default Teams format')
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--full-collection', action='store_true', help='Fetch all discussion bodies, bypassing incremental text reuse')
    args = parser.parse_args()
    os.umask(0o077)
    start = time.perf_counter()
    args.output.mkdir(parents=True, exist_ok=True)
    snapshot = json.loads(args.snapshot.read_text()) if args.snapshot else collect(args.workers,
        args.cache_dir / 'github-bodies.json' if args.cache_dir and not args.full_collection else None)
    write_json(args.output / 'snapshot.json', snapshot)
    cases, obligations = prepare(snapshot)
    security_cases = [c for pr in snapshot['prs'] for c in signals.priority_cases(pr)]
    write_json(args.output / 'cases.json', cases + security_cases)
    write_json(args.output / 'obligations.json', obligations)
    if args.collect_only:
        print(json.dumps({'open_prs':snapshot['open_count'], 'cases':len(cases), 'security_cases':len(security_cases), **snapshot['collection_metrics']}))
        return
    payloads = [jev.build_payload(c, args.model) for c in cases + security_cases]
    output = jev.evaluate(payloads, os.environ.get('TYPESAFE_API_KEY'), min(args.workers, 4), args.cache_dir)
    feedback_results, security_results = output['results'][:len(cases)], output['results'][len(cases):]
    snapshot['interpretation_metrics'] = {**output['metrics'],
        'feedback_cases': len(cases), 'feedback_unavailable': sum(r['suggestion'] == 'unavailable' for r in feedback_results),
        'priority_cases': len(security_cases), 'priority_unavailable': sum(r['suggestion'] == 'unavailable' for r in security_results)}
    merge = signals.assess(snapshot, security_results)
    actions, uncertainties = compose(snapshot, obligations, feedback_results)
    checks_metrics = None
    if not args.snapshot:
        urgent = [n for n, m in merge.items() if m['priority']['level'] in ('Urgent', 'High')]
        checks_metrics = collect_checks(snapshot, actions, args.workers, urgent)
        write_json(args.output / 'snapshot.json', snapshot)
        actions, uncertainties = compose(snapshot, obligations, feedback_results)
    else:
        write_json(args.output / 'snapshot.json', snapshot)
    report = render(snapshot, actions, uncertainties, args.format_style, merge)
    write_json(args.output / 'judgments.json', output)
    write_json(args.output / 'actions.json', {'actions':actions, 'uncertainties':uncertainties})
    write_json(args.output / 'merge.json', {'verify': sorted(n for n, m in merge.items() if signals.needs_verification(m)),
                                            'prs': {str(n): m for n, m in merge.items()}})
    (args.output / 'report.txt').write_text(report)
    metrics = {'total_seconds':round(time.perf_counter()-start,3), 'snapshot_replay':bool(args.snapshot),
               'github': None if args.snapshot else snapshot['collection_metrics'], 'checks':checks_metrics, 'jev':output['metrics'],
               'actions':len(actions), 'uncertainties':len(uncertainties), 'agent_inference_calls_inside_pipeline':0}
    write_json(args.output / 'metrics.json', metrics)
    print(json.dumps(metrics))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f'Queue failed: {type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(2)
