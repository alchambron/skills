import argparse
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("gh_pr_watch.py")
MODULE_SPEC = importlib.util.spec_from_file_location("gh_pr_watch", MODULE_PATH)
gh_pr_watch = importlib.util.module_from_spec(MODULE_SPEC)
assert MODULE_SPEC.loader is not None
MODULE_SPEC.loader.exec_module(gh_pr_watch)


def sample_pr():
    return {
        "number": 123,
        "url": "https://github.com/openai/codex/pull/123",
        "repo": "openai/codex",
        "head_sha": "abc123",
        "head_branch": "feature",
        "state": "OPEN",
        "merged": False,
        "closed": False,
        "mergeable": "MERGEABLE",
        "merge_state_status": "CLEAN",
        "review_decision": "",
    }


def sample_checks(**overrides):
    checks = {
        "pending_count": 0,
        "failed_count": 0,
        "passed_count": 12,
        "all_terminal": True,
    }
    checks.update(overrides)
    return checks


def test_collect_snapshot_fetches_review_items_before_ci(monkeypatch, tmp_path):
    call_order = []
    pr = sample_pr()

    monkeypatch.setattr(gh_pr_watch, "resolve_pr", lambda *args, **kwargs: pr)
    monkeypatch.setattr(gh_pr_watch, "load_state", lambda path: ({}, True))
    monkeypatch.setattr(
        gh_pr_watch,
        "get_authenticated_login",
        lambda: call_order.append("auth") or "octocat",
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "fetch_new_review_items",
        lambda *args, **kwargs: call_order.append("review") or [],
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "get_pr_checks",
        lambda *args, **kwargs: call_order.append("checks") or [],
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "summarize_checks",
        lambda checks: call_order.append("summarize") or sample_checks(),
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "get_workflow_runs_for_sha",
        lambda *args, **kwargs: call_order.append("workflow") or [],
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "failed_runs_from_workflow_runs",
        lambda *args, **kwargs: call_order.append("failed_runs") or [],
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "failed_jobs_from_workflow_runs",
        lambda *args, **kwargs: call_order.append("failed_jobs") or [],
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "recommend_actions",
        lambda *args, **kwargs: call_order.append("recommend") or ["idle"],
    )
    monkeypatch.setattr(gh_pr_watch, "save_state", lambda *args, **kwargs: None)

    args = argparse.Namespace(
        pr="123",
        repo=None,
        state_file=str(tmp_path / "watcher-state.json"),
        max_flaky_retries=3,
    )

    gh_pr_watch.collect_snapshot(args)

    assert call_order.index("review") < call_order.index("checks")
    assert call_order.index("review") < call_order.index("workflow")


def test_recommend_actions_prioritizes_review_comments():
    actions = gh_pr_watch.recommend_actions(
        sample_pr(),
        sample_checks(failed_count=1),
        [{"run_id": 99}],
        [],
        [{"kind": "review_comment", "id": "1"}],
        0,
        3,
    )

    assert actions == [
        "process_review_comment",
        "diagnose_ci_failure",
        "retry_failed_checks",
    ]


def test_pending_review_feedback_surfaces_only_after_publication(monkeypatch):
    state = {
        "seen_review_comment_ids": ["20"],
        "seen_review_ids": ["10"],
    }
    review = {
        "id": 10,
        "user": {"login": "octocat"},
        "author_association": "MEMBER",
        "state": "PENDING",
        "body": "Please rename this.",
        "created_at": "2026-06-08T10:00:00Z",
        "submitted_at": None,
        "html_url": "https://github.com/openai/codex/pull/123#pullrequestreview-10",
    }
    review_comment = {
        "id": 20,
        "pull_request_review_id": 10,
        "user": {"login": "octocat"},
        "author_association": "MEMBER",
        "body": "Please rename this.",
        "created_at": "2026-06-08T10:00:00Z",
        "path": "src/example.rs",
        "line": 7,
        "html_url": "https://github.com/openai/codex/pull/123#discussion_r20",
    }

    def fake_list(endpoint, **kwargs):
        if endpoint.endswith("/issues/123/comments"):
            return []
        if endpoint.endswith("/pulls/123/comments"):
            return [review_comment]
        if endpoint.endswith("/pulls/123/reviews"):
            return [review]
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(gh_pr_watch, "gh_api_list_paginated", fake_list)

    assert (
        gh_pr_watch.fetch_new_review_items(
            sample_pr(),
            state,
            fresh_state=True,
            authenticated_login="octocat",
        )
        == []
    )
    assert state["seen_review_comment_ids"] == []
    assert state["seen_review_ids"] == []

    review["state"] = "COMMENTED"
    review["submitted_at"] = "2026-06-08T10:05:00Z"

    published_items = gh_pr_watch.fetch_new_review_items(
        sample_pr(),
        state,
        fresh_state=False,
        authenticated_login="octocat",
    )

    assert {(item["kind"], item["id"]) for item in published_items} == {
        ("review", "10"),
        ("review_comment", "20"),
    }
    assert state["seen_review_comment_ids"] == ["20"]
    assert state["seen_review_ids"] == ["10"]


def test_run_watch_keeps_polling_open_ready_to_merge_pr(monkeypatch):
    sleeps = []
    events = []
    snapshot = {
        "pr": sample_pr(),
        "checks": sample_checks(),
        "failed_runs": [],
        "failed_jobs": [],
        "new_review_items": [],
        "actions": ["ready_to_merge"],
        "retry_state": {
            "current_sha_retries_used": 0,
            "max_flaky_retries": 3,
        },
    }

    monkeypatch.setattr(
        gh_pr_watch,
        "collect_snapshot",
        lambda args: (snapshot, Path("/tmp/codex-babysit-pr-state.json")),
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "print_event",
        lambda event, payload: events.append((event, payload)),
    )

    class StopWatch(Exception):
        pass

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise StopWatch

    monkeypatch.setattr(gh_pr_watch.time, "sleep", fake_sleep)

    with pytest.raises(StopWatch):
        gh_pr_watch.run_watch(argparse.Namespace(poll_seconds=30))

    assert sleeps == [30, 30]
    assert [event for event, _ in events] == ["snapshot", "snapshot"]


def test_failed_jobs_include_direct_logs_endpoint(monkeypatch):
    jobs_by_run = {
        99: [
            {
                "id": 555,
                "name": "unit tests",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://github.com/openai/codex/actions/runs/99/job/555",
            },
            {
                "id": 556,
                "name": "lint",
                "status": "completed",
                "conclusion": "success",
            },
        ]
    }

    monkeypatch.setattr(
        gh_pr_watch,
        "get_jobs_for_run",
        lambda repo, run_id: jobs_by_run[run_id],
    )

    failed_jobs = gh_pr_watch.failed_jobs_from_workflow_runs(
        "openai/codex",
        [
            {
                "id": 99,
                "name": "CI",
                "status": "in_progress",
                "conclusion": "",
                "head_sha": "abc123",
            }
        ],
        "abc123",
    )

    assert failed_jobs == [
        {
            "run_id": 99,
            "workflow_name": "CI",
            "run_status": "in_progress",
            "run_conclusion": "",
            "job_id": 555,
            "job_name": "unit tests",
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/openai/codex/actions/runs/99/job/555",
            "logs_endpoint": "repos/openai/codex/actions/jobs/555/logs",
        }
    ]


@pytest.mark.parametrize('login', ['chatgpt-codex-connector[bot]', 'codex[bot]', 'coderabbitai[bot]', 'copilot-pull-request-reviewer', 'copilot-pull-request-reviewer[bot]'])
def test_known_review_bots(login):
    assert gh_pr_watch.is_bot_login(login)
    assert gh_pr_watch.is_actionable_review_bot_login(login)


@pytest.mark.parametrize('login', ['fake-codex[bot]', 'coderabbitai', 'dependabot[bot]'])
def test_unlisted_review_bots_are_not_trusted(login):
    assert not gh_pr_watch.is_actionable_review_bot_login(login)


def test_cancelled_check_cannot_be_ready():
    checks = gh_pr_watch.summarize_checks([{'bucket': 'cancel', 'state': 'CANCELLED'}])
    assert checks['failed_count'] == 1
    assert 'ready_to_merge' not in gh_pr_watch.recommend_actions(sample_pr(), checks, [], [], [], 0, 3)


def test_failed_job_prevents_ready_even_before_check_summary_updates():
    actions = gh_pr_watch.recommend_actions(sample_pr(), sample_checks(), [], [{'job_id': 1}], [], 0, 3)
    assert 'ready_to_merge' not in actions
    assert 'diagnose_ci_failure' in actions


def test_no_checks_is_not_green_or_ready():
    checks = gh_pr_watch.summarize_checks([])
    assert not gh_pr_watch.is_ci_green({'checks': checks})
    assert not gh_pr_watch.is_pr_ready_to_merge(sample_pr(), checks, [])


@pytest.mark.parametrize('code', [1, 8])
def test_check_status_exit_requires_json_list(monkeypatch, code):
    def run(cmd, **kwargs):
        raise gh_pr_watch.subprocess.CalledProcessError(code, cmd, output='[{"bucket":"pending"}]', stderr='')
    monkeypatch.setattr(gh_pr_watch.subprocess, 'run', run)
    assert gh_pr_watch.get_pr_checks('123', 'openai/codex') == [{'bucket': 'pending'}]


def test_auth_error_still_fails(monkeypatch):
    def run(cmd, **kwargs):
        raise gh_pr_watch.subprocess.CalledProcessError(1, cmd, output='', stderr='Authentication required')
    monkeypatch.setattr(gh_pr_watch.subprocess, 'run', run)
    with pytest.raises(gh_pr_watch.GhCommandError, match='Authentication required'):
        gh_pr_watch.get_pr_checks('123', 'openai/codex')


def retry_snapshot(tmp_path):
    pr = sample_pr()
    return {
        'pr': pr, 'checks': sample_checks(failed_count=2),
        'failed_runs': [{'run_id': 1}, {'run_id': 2}],
        'retry_state': {'current_sha_retries_used': 0, 'max_flaky_retries': 3},
    }, tmp_path/'state.json'


def test_partial_rerun_failure_consumes_cycle(monkeypatch, tmp_path):
    snapshot, state_path = retry_snapshot(tmp_path)
    monkeypatch.setattr(gh_pr_watch, 'collect_snapshot', lambda args: (snapshot, state_path))
    monkeypatch.setattr(gh_pr_watch, 'resolve_pr', lambda *a, **k: snapshot['pr'])
    requested = []
    def rerun(args, **kwargs):
        requested.append(args)
        if len(requested) == 2:
            raise gh_pr_watch.GhCommandError('second rerun failed')
        return ''
    monkeypatch.setattr(gh_pr_watch, 'gh_text', rerun)
    with pytest.raises(gh_pr_watch.GhCommandError):
        gh_pr_watch.retry_failed_now(None)
    state, _ = gh_pr_watch.load_state(state_path)
    assert gh_pr_watch.current_retry_count(state, 'abc123') == 1
    assert gh_pr_watch.current_retry_count(state, 'new-sha') == 0


def test_changed_head_prevents_rerun(monkeypatch, tmp_path):
    snapshot, state_path = retry_snapshot(tmp_path)
    monkeypatch.setattr(gh_pr_watch, 'collect_snapshot', lambda args: (snapshot, state_path))
    monkeypatch.setattr(gh_pr_watch, 'resolve_pr', lambda *a, **k: {**snapshot['pr'], 'head_sha': 'new-sha'})
    monkeypatch.setattr(gh_pr_watch, 'gh_text', lambda *a, **k: pytest.fail('must not rerun'))
    assert gh_pr_watch.retry_failed_now(None)['reason'] == 'pr_state_changed'
    assert not state_path.exists()


def test_enterprise_host_rejected_before_api_queries(monkeypatch):
    monkeypatch.setattr(gh_pr_watch, 'gh_json', lambda *a, **k: {'url': 'https://github.example.com/org/repo/pull/123'})
    with pytest.raises(gh_pr_watch.GhCommandError, match='github.com only'):
        gh_pr_watch.resolve_pr('https://github.example.com/org/repo/pull/123')


def test_exhausted_retry_budget_prevents_rerun(monkeypatch, tmp_path):
    snapshot, state_path = retry_snapshot(tmp_path)
    snapshot['retry_state']['current_sha_retries_used'] = 3
    monkeypatch.setattr(gh_pr_watch, 'collect_snapshot', lambda args: (snapshot, state_path))
    monkeypatch.setattr(gh_pr_watch, 'gh_text', lambda *a, **k: pytest.fail('must not rerun'))
    assert gh_pr_watch.retry_failed_now(None)['reason'] == 'retry_budget_exhausted'
    assert not state_path.exists()
