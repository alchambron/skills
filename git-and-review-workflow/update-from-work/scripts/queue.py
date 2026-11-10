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
    # Slice before assessing update need; never backfill from later entries.
    return queue[:limit]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory')
    parser.add_argument('--limit', type=int, choices=(2, 3), default=2)
    args = parser.parse_args()
    with open(args.inventory) as source:
        queue = build_queue(json.load(source))
    print(json.dumps(dict(queue=queue, selected=select_top(queue, args.limit)), indent=2))
