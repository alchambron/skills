"""Serialize queue reservations against both durable Mac ledgers."""

from contextlib import contextmanager
import fcntl
import json


PHASES = {
    'respond-queue': {'analysis': 'static', 'fix': 'live'},
    'review-queue': {'static': 'static', 'live': 'live', 'waiting_live': None},
    'update-from-work': {'update': 'live'},
}
CAPACITY = {'static': 2, 'live': 1}


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root.parent / 'queue-slots.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def active_peer_jobs(path, queue):
    try:
        state = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise ValueError(f'unreadable shared slot state {path}: {exc}') from exc
    jobs = state.get('jobs') if isinstance(state, dict) else None
    if not isinstance(jobs, dict):
        raise ValueError(f'invalid shared slot state {path}: missing jobs object')

    active = []
    for token, job in jobs.items():
        if not isinstance(job, dict) or job.get('status') not in ('active', 'finished'):
            raise ValueError(f'invalid shared slot state {path}: malformed job {token}')
        if job['status'] == 'active':
            number = job.get('number')
            phase = job.get('phase')
            if type(number) is not int or number < 1 or not isinstance(phase, str) or phase not in PHASES[queue]:
                raise ValueError(f'invalid shared slot state {path}: malformed active job {token}')
            active.append(job)
    return active


def check_capacity(root, queue, phase, number, jobs):
    """Call with the shared lock held and this queue's current active jobs."""
    slot = PHASES[queue][phase]
    counts = dict.fromkeys(CAPACITY, 0)
    for owner, phases in PHASES.items():
        active = jobs if owner == queue else active_peer_jobs(root.parent / owner / 'state.json', owner)
        for job in active:
            if job['number'] == number:
                raise ValueError(f'PR already reserved by {owner}')
            category = phases[job['phase']]
            if category:
                counts[category] += 1
    if slot is not None and counts[slot] >= CAPACITY[slot]:
        raise ValueError(f'shared {slot} resource cap reached')
