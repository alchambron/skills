"""Build a read-only merge queue from normalized, complete open-PR evidence.

Input JSON list: number, author, repo, head_repo, state, draft, branch, base,
reviewDecision, reviews [{login, type, state, submittedAt}]. Reviews must be
current latest opinion per reviewer (including dismissed opinions), not history.
Output entries contain bottom-up PR numbers, URLs and qualifying approval time.
"""
import argparse
import json
from datetime import datetime


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('approval timestamps require timezone')
    return result


def approval(pr):
    if not (pr['author'] == 'alchambron' and pr['repo'] == 'EnerZam/JouleMV'
            and pr['head_repo'] == 'EnerZam/JouleMV' and pr['state'] == 'OPEN'
            and not pr['draft'] and pr['reviewDecision'] == 'APPROVED'):
        return None
    times = [stamp(r['submittedAt']) for r in pr['reviews']
             if r['state'] == 'APPROVED' and r['type'] == 'User'
             and r['login'] != 'alchambron' and not r['login'].endswith('[bot]')]
    return min(times) if times else None


def build_queue(prs):
    # Complete inventory includes ineligible parents AND children: never split a stack.
    branches = {}
    for pr in prs:
        if pr['branch'] in branches:
            raise ValueError('ambiguous head branch')
        branches[pr['branch']] = pr['number']
    nodes = {p['number']: p for p in prs}
    if len(nodes) != len(prs):
        raise ValueError('duplicate PR number')
    edges = {n: set() for n in nodes}
    parents = {}
    for n, pr in nodes.items():
        parent = branches.get(pr['base'])
        parents[n] = parent
        if parent is not None:
            edges[n].add(parent)
            edges[parent].add(n)
    result, seen = [], set()
    for n in sorted(nodes):
        if n in seen:
            continue
        component, todo = set(), [n]
        while todo:
            item = todo.pop()
            if item not in component:
                component.add(item)
                todo.extend(edges[item])
        seen.update(component)
        times = {i: approval(nodes[i]) for i in component}
        if any(t is None for t in times.values()):
            continue
        ordered = []
        remaining = set(component)
        while remaining:
            ready = sorted(i for i in remaining if parents[i] not in remaining)
            if not ready:
                raise ValueError('cyclic stack')
            ordered.extend(ready)
            remaining.difference_update(ready)
        if any(nodes[i]['base'] != 'work' and parents[i] is None for i in component):
            continue
        result.append(dict(prs=ordered, approval_time=max(times.values()).isoformat(),
                           urls=[f'https://github.com/EnerZam/JouleMV/pull/{i}' for i in ordered]))
    return sorted(result, key=lambda e: (stamp(e['approval_time']), e['prs']))


def select_top(queue, limit=2):
    if limit not in (2, 3):
        raise ValueError('limit must be 2 or 3')
    # Legacy fixed top-N slice without update-need backfill.
    return queue[:limit]


def select_candidates(queue, needs_update, explicit_requests=(), limit=2):
    """Select up to `limit` entries that need an update, plus any with explicit reviewer requests.

    Walks `queue` in oldest-approval order, backfilling past already-up-to-date
    entries so up to `limit` entries needing work (conflict or blocking
    behind-work policy) are selected. Any eligible queue entry with an
    outstanding explicit human update/rebase request is also included, even
    below rank `limit`, without duplicating entries and preserving queue order.
    """
    if limit not in (2, 3):
        raise ValueError('limit must be 2 or 3')
    needs = set(needs_update)
    explicit = set(explicit_requests)
    selected = []
    backfill_count = 0
    for entry in queue:
        members = set(entry['prs'])
        has_explicit = bool(members & explicit)
        has_need = bool(members & needs)
        if has_need and backfill_count < limit:
            selected.append(entry)
            backfill_count += 1
        elif has_explicit:
            selected.append(entry)
    return selected


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory')
    parser.add_argument('--limit', type=int, choices=(2, 3), default=2)
    parser.add_argument('--needs-update', help='JSON file listing PR numbers needing an update')
    parser.add_argument('--explicit-requests', help='JSON file listing PR numbers with explicit human update requests')
    args = parser.parse_args()
    with open(args.inventory) as source:
        queue = build_queue(json.load(source))
    if args.needs_update or args.explicit_requests:
        needs = json.load(open(args.needs_update)) if args.needs_update else []
        explicit = json.load(open(args.explicit_requests)) if args.explicit_requests else []
        selected = select_candidates(queue, needs, explicit, args.limit)
    else:
        selected = select_top(queue, args.limit)
    print(json.dumps(dict(queue=queue, selected=selected), indent=2))
