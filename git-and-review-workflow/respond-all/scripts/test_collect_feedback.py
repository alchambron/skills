"""Offline regression fixtures for queue eligibility and partial collection."""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import collect_feedback as collector


def date(day):
    return f"2026-10-{day:02d}T12:00:00Z"


def pr(number=1, repository="example/repo"):
    return {
        "id": f"PR_{repository}_{number}", "number": number, "title": "Example",
        "url": f"https://github.com/{repository}/pull/{number}",
        "state": "OPEN", "author": {"login": "owner"},
        "headRefOid": "abc123", "reviewDecision": None,
        "repository": {"nameWithOwner": repository},
    }


def comment(author="reviewer", created=20, edited=None, actor_type="User"):
    return {
        "id": f"{author}_{created}", "body": "Please handle this case",
        "url": f"https://github.com/example/repo/pull/1#issuecomment-{author}",
        "author": {"login": author, "__typename": actor_type},
        "createdAt": date(created), "updatedAt": date(edited or created),
        "lastEditedAt": date(edited) if edited else None,
    }


def review(state="COMMENTED", submitted=20, edited=None, author="reviewer"):
    result = comment(author, submitted, edited)
    result.update(state=state, submittedAt=date(submitted))
    return result


def detail():
    return {"id": "PR_1", **{key: [] for key in collector.CONNECTIONS}}


def connection(nodes):
    return {
        "nodes": nodes,
        "pageInfo": {"hasNextPage": False, "hasPreviousPage": False,
                     "startCursor": None, "endCursor": None},
    }


class EditedFeedbackTests(unittest.TestCase):
    def test_reviewer_edit_after_reply_reopens_top_level_and_inline_feedback(self):
        for kind in ("comment", "inline"):
            with self.subTest(kind=kind):
                data = detail()
                data["comments"] = [comment("owner", 21)]
                edited = comment(edited=22)
                if kind == "comment":
                    data["comments"].append(edited)
                else:
                    data["reviewThreads"] = [{"isResolved": False, "comments": [edited]}]
                match, reason = collector.evaluate(pr(), data, "owner")
                self.assertIsNone(reason)
                self.assertEqual(match["feedback"]["date"], date(22))
                self.assertEqual(match["feedback"]["kind"], kind)

    def test_later_author_reply_or_edit_still_excludes(self):
        for author_reply in (comment("owner", 23), comment("owner", 21, 23)):
            with self.subTest(author_reply=author_reply):
                data = detail()
                data["comments"] = [comment(edited=22), author_reply]
                match, reason = collector.evaluate(pr(), data, "owner")
                self.assertIsNone(match)
                self.assertEqual(reason, "author_acted_or_branch_updated_after_feedback")

    def test_non_edit_update_does_not_refresh_reviewer_feedback(self):
        data = detail()
        feedback = comment()
        feedback["updatedAt"] = date(23)
        data["comments"] = [feedback, comment("owner", 21)]
        match, reason = collector.evaluate(pr(), data, "owner")
        self.assertIsNone(match)
        self.assertEqual(reason, "author_acted_or_branch_updated_after_feedback")

    def test_resolved_and_bot_edits_are_not_new_human_feedback(self):
        data = detail()
        data["comments"] = [comment(edited=22, actor_type="Bot")]
        data["reviewThreads"] = [{"isResolved": True, "comments": [comment(edited=23)]}]
        self.assertEqual(collector.evaluate(pr(), data, "owner"), (None, "no_human_feedback"))

    def test_submitted_review_body_edit_after_reply_is_feedback(self):
        for state in ("COMMENTED", "CHANGES_REQUESTED"):
            with self.subTest(state=state):
                data = detail()
                data["reviews"] = [review(state, edited=22)]
                data["comments"] = [comment("owner", 21)]
                match, reason = collector.evaluate(pr(), data, "owner")
                self.assertIsNone(reason)
                self.assertEqual(match["feedback"]["date"], date(22))

    def test_pending_review_edit_is_ignored(self):
        data = detail()
        data["reviews"] = [review("PENDING", edited=22)]
        self.assertEqual(collector.evaluate(pr(), data, "owner"), (None, "no_human_feedback"))

    def test_author_review_edit_counts_as_action(self):
        data = detail()
        data["comments"] = [comment(created=21)]
        data["reviews"] = [review(edited=22, author="owner")]
        match, reason = collector.evaluate(pr(), data, "owner")
        self.assertIsNone(match)
        self.assertEqual(reason, "author_acted_or_branch_updated_after_feedback")

    def test_edit_does_not_reorder_review_decisions(self):
        for earlier, later, expected in (
            ("APPROVED", "CHANGES_REQUESTED", None),
            ("CHANGES_REQUESTED", "APPROVED", "approved"),
        ):
            with self.subTest(earlier=earlier):
                data = detail()
                data["reviews"] = [review(later, submitted=21), review(earlier, edited=23)]
                match, reason = collector.evaluate(pr(), data, "owner")
                self.assertEqual(reason, expected)
                self.assertEqual(match is None, expected is not None)


NO_FINDINGS = "Reviewed this layer against the PR description; no actionable findings. Tests were not run."


class SignalTests(unittest.TestCase):
    def evaluate(self, *items):
        data = detail()
        for kind, item in items:
            if kind == "thread":
                data["reviewThreads"].append({"isResolved": False, "comments": [item]})
            else:
                data[kind].append(item)
        match, reason = collector.evaluate(pr(), data, "owner")
        self.assertIsNone(reason)
        return match

    def body(self, item, text):
        item["body"] = text
        return item

    def test_no_findings_review_body_is_low_signal(self):
        for text in (NO_FINDINGS, "No verified actionable findings in this layer.", "LGTM"):
            with self.subTest(text=text):
                match = self.evaluate(("reviews", self.body(review(), text)))
                self.assertEqual(match["signal"], "low")

    def test_requests_questions_and_cross_references_stay_actionable(self):
        for text in (
            NO_FINDINGS + " Please rebase.", NO_FINDINGS + " Why skip the cache?",
            NO_FINDINGS + " The issue raised on #1786 also affects reports.",
            NO_FINDINGS + " The migration number needs a collision check.",
            "Handle the null case.",
        ):
            with self.subTest(text=text):
                match = self.evaluate(("reviews", self.body(review(), text)))
                self.assertEqual(match["signal"], "actionable")

    def test_own_pr_number_is_not_a_cross_reference(self):
        match = self.evaluate(("reviews", self.body(review(), NO_FINDINGS + " No threads existed on #1.")))
        self.assertEqual(match["signal"], "low")
        match = self.evaluate(("reviews", self.body(review(), NO_FINDINGS + " See #12.")))
        self.assertEqual(match["signal"], "actionable")

    def test_change_request_with_no_findings_body_is_actionable(self):
        match = self.evaluate(("reviews", self.body(review("CHANGES_REQUESTED"), NO_FINDINGS)))
        self.assertEqual(match["signal"], "actionable")

    def test_untouched_inline_comment_beside_no_findings_review_is_actionable(self):
        match = self.evaluate(("reviews", self.body(review(submitted=21), NO_FINDINGS)),
                              ("thread", comment(created=20)))
        self.assertEqual(match["signal"], "actionable")
        self.assertEqual([item["kind"] for item in match["untouched"]], ["inline", "COMMENTED"])

    def test_feedback_answered_before_last_author_action_is_not_untouched(self):
        match = self.evaluate(("comments", comment(created=20)), ("comments", comment("owner", 21)),
                              ("reviews", self.body(review(submitted=22), NO_FINDINGS)))
        self.assertEqual(match["signal"], "low")
        self.assertEqual([item["date"] for item in match["untouched"]], [date(22)])

    def test_feedback_body_is_trimmed(self):
        match = self.evaluate(("comments", self.body(comment(), "x" * 2000)))
        self.assertEqual(len(match["feedback"]["body"]), collector.BODY_LIMIT + 1)

    def test_processing_order_is_priority_then_oldest_feedback(self):
        def item(number, labels, day):
            return {"number": number, "labels": labels, "feedback": {"date": date(day)}}
        items = [item(1, [], 20), item(2, ["priority:low"], 10), item(3, ["priority:high"], 25),
                 item(4, [], 15), item(5, ["Urgent Ticket"], 22)]
        self.assertEqual([i["number"] for i in sorted(items, key=collector.processing_order)],
                         [5, 3, 4, 1, 2])


class ExplicitCollectionTests(unittest.TestCase):
    def run_collection(self, urls, unavailable=None, bodies=None, extra=()):
        unavailable = unavailable or {}
        bodies = bodies or {}

        def graphql(query, variables=None):
            if "viewer { login }" in query:
                return {"viewer": {"login": "owner"}}
            if "number" in (variables or {}):
                number = variables["number"]
                if number in unavailable:
                    error = unavailable[number]
                    if error is not None:
                        raise error
                    return {"repository": None}
                return {"repository": {"pullRequest": pr(number)}}
            if "ids" in (variables or {}):
                nodes = []
                for node_id in variables["ids"]:
                    data = detail()
                    data["comments"] = [comment()]
                    number = int(node_id.rsplit("_", 1)[1])
                    if number in bodies:
                        data["comments"][0]["body"] = bodies[number]
                    nodes.append({"id": node_id, **{
                        key: connection(data[key]) for key in collector.CONNECTIONS
                    }})
                return {"nodes": nodes}
            raise AssertionError(f"Unexpected GraphQL call: {query}")

        arguments = ["collect_feedback.py", *extra]
        for url in urls:
            arguments.extend(["--pr", url])
        output = io.StringIO()
        with patch.object(collector, "graphql", side_effect=graphql), \
                patch.object(collector.sys, "argv", arguments), contextlib.redirect_stdout(output):
            code = collector.main()
        return code, json.loads(output.getvalue())

    def test_missing_and_malformed_urls_do_not_drop_valid_results(self):
        urls = [pr(1)["url"], "not-a-pr-url", pr(2)["url"], pr(3)["url"]]
        code, report = self.run_collection(urls, {1: None})
        self.assertEqual(code, 2)
        self.assertFalse(report["complete"])
        self.assertEqual(report["scanned"], 4)
        self.assertEqual([item["number"] for item in report["matches"]], [2, 3])
        self.assertEqual([item["url"] for item in report["unavailable"]], urls[:2])
        self.assertTrue(all(item["error"] for item in report["unavailable"]))

    def test_all_unavailable_still_returns_structured_partial_report(self):
        urls = [pr(1)["url"], pr(2)["url"], pr(3)["url"]]
        code, report = self.run_collection(urls, {
            1: None, 2: RuntimeError("API failure"),
            3: subprocess.TimeoutExpired("gh", 120),
        })
        self.assertEqual(code, 2)
        self.assertFalse(report["complete"])
        self.assertEqual(report["scanned"], 3)
        self.assertEqual(report["matches"], [])
        self.assertEqual(report["excluded"], [])
        self.assertEqual([item["url"] for item in report["unavailable"]], urls)

    def test_low_signal_prs_are_split_unless_included(self):
        urls = [pr(1)["url"], pr(2)["url"]]
        code, report = self.run_collection(urls, bodies={2: "LGTM"})
        self.assertEqual(code, 0)
        self.assertEqual([item["number"] for item in report["matches"]], [1])
        self.assertEqual([item["number"] for item in report["lowSignal"]], [2])
        code, report = self.run_collection(urls, bodies={2: "LGTM"},
                                           extra=["--include-low-signal"])
        self.assertEqual([(item["number"], item["signal"]) for item in report["matches"]],
                         [(1, "actionable"), (2, "low")])
        self.assertEqual(report["lowSignal"], [])

    def test_out_writes_report_and_prints_compact_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            arguments = ["collect_feedback.py", "--out", str(path), "--pr", pr(1)["url"]]

            def graphql(query, variables=None):
                if "viewer { login }" in query:
                    return {"viewer": {"login": "owner"}}
                if "number" in (variables or {}):
                    return {"repository": {"pullRequest": pr(1)}}
                data = detail()
                data["comments"] = [comment()]
                return {"nodes": [{"id": "PR_1", **{
                    key: connection(data[key]) for key in collector.CONNECTIONS}}]}

            output = io.StringIO()
            with patch.object(collector, "graphql", side_effect=graphql), \
                    patch.object(collector.sys, "argv", arguments), contextlib.redirect_stdout(output):
                self.assertEqual(collector.main(), 0)
            lines = output.getvalue().splitlines()
            self.assertIn("matches=1", lines[0])
            self.assertTrue(lines[1].startswith("matches\texample/repo#1\t"))
            self.assertEqual(json.loads(path.read_text())["matches"][0]["number"], 1)

    def test_successful_collection_remains_complete(self):
        code, report = self.run_collection([pr()["url"]])
        self.assertEqual(code, 0)
        self.assertTrue(report["complete"])
        self.assertEqual(report["scanned"], 1)
        self.assertEqual(len(report["matches"]), 1)
        self.assertEqual(report["unavailable"], [])


class DiscoveryTests(unittest.TestCase):
    def page(self, nodes, next_cursor=None):
        page = connection(nodes)
        page["pageInfo"].update(hasNextPage=next_cursor is not None, endCursor=next_cursor)
        return {"viewer": {"pullRequests": page}}

    def test_default_includes_same_number_from_different_repositories(self):
        first = pr(1, "team/one")
        second = pr(1, "other/two")
        with patch.object(collector, "graphql", side_effect=[
            self.page([first], "page2"), self.page([first, second]),
        ]) as graphql:
            found = collector.discover("owner", [])
        self.assertEqual(found, [first, second])
        self.assertEqual(graphql.call_args_list[1].args[1], {"cursor": "page2"})

    def test_discovers_more_than_one_thousand_prs(self):
        prs = [pr(number) for number in range(1, 1102)]
        pages = [self.page(prs[start:start + 100], str(start + 100)
                           if start + 100 < len(prs) else None)
                 for start in range(0, len(prs), 100)]
        with patch.object(collector, "graphql", side_effect=pages) as graphql:
            found = collector.discover("owner", [])
        self.assertEqual(found, prs)
        self.assertEqual(graphql.call_count, 12)

    def test_repo_restrictions_are_case_insensitive_or_filters(self):
        first = pr(1, "Team/One")
        second = pr(1, "Other/Two")
        with patch.object(collector, "graphql", side_effect=[
            self.page([first, pr(2, "unrelated/repo")], "page2"),
            self.page([second]),
        ]):
            found = collector.discover("owner", ["team/ONE", "OTHER/two"])
        self.assertEqual(found, [first, second])

    def test_only_authored_open_prs_are_retained(self):
        closed = pr(2)
        closed["state"] = "CLOSED"
        other = pr(3)
        other["author"] = {"login": "someone_else"}
        with patch.object(collector, "graphql", return_value=self.page([closed, other, pr()])):
            self.assertEqual(collector.discover("owner", []), [pr()])

    def test_missing_or_cyclic_next_cursor_fails(self):
        missing_cursor = self.page([])
        missing_cursor["viewer"]["pullRequests"]["pageInfo"]["hasNextPage"] = True
        cases = [
            [missing_cursor],
            [self.page([], "a"), self.page([], "a")],
            [self.page([], "a"), self.page([], "b"), self.page([], "a")],
        ]
        for pages in cases:
            with self.subTest(pages=len(pages)), \
                    patch.object(collector, "graphql", side_effect=pages):
                with self.assertRaisesRegex(RuntimeError, "pagination did not advance"):
                    collector.discover("owner", [])

    def test_inaccessible_node_does_not_claim_complete_discovery(self):
        with patch.object(collector, "graphql", return_value=self.page([None])):
            with self.assertRaisesRegex(RuntimeError, "inaccessible"):
                collector.discover("owner", [])

    def test_main_without_repo_option_reports_multiple_repositories(self):
        prs = [pr(1, "team/one"), pr(1, "other/two")]

        def graphql(query, variables=None):
            if "viewer { login }" in query:
                return {"viewer": {"login": "owner"}}
            if "viewer { pullRequests" in query:
                return self.page(prs)
            if "ids" in (variables or {}):
                nodes = []
                for node_id in variables["ids"]:
                    data = detail()
                    data["comments"] = [comment()]
                    nodes.append({"id": node_id, **{
                        key: connection(data[key]) for key in collector.CONNECTIONS
                    }})
                return {"nodes": nodes}
            raise AssertionError(f"Unexpected GraphQL call: {query}")

        output = io.StringIO()
        with patch.object(collector, "graphql", side_effect=graphql), \
                patch.object(collector.sys, "argv", ["collect_feedback.py"]), \
                contextlib.redirect_stdout(output):
            code = collector.main()
        report = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(report["complete"])
        self.assertEqual(report["scanned"], 2)
        self.assertEqual([item["url"] for item in report["matches"]], [item["url"] for item in prs])


if __name__ == "__main__":
    unittest.main()
