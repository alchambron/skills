# Changelog

## 1.4.0

- `scripts/shared_slots.py` now scopes duplicate-PR reservation checks by `(repository, number)` (defaulting missing `repository` to `EnerZam/JouleMV` for backward compatibility with legacy state files and single-repo callers) while keeping the exact same combined `static: 2` and `live: 1` caps across all repositories and queues.
