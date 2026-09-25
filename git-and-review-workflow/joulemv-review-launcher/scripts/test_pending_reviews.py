import unittest

from pending_reviews import build_pending_reviews, render_markdown


SHA = "a" * 40


def pr(number, **changes):
    value = {"number": number, "title": f"PR {number}",
             "url": f"https://github.com/EnerZam/JouleMV/pull/{number}",
             "headRefOid": SHA}
    value.update(changes)
    return value


def snapshot(*prs, errors=None, open_count=None):
    return {"collected_at": "2026-09-25T09:00:00-04:00", "prs": list(prs),
            "open_count": len(prs) if open_count is None else open_count,
            "errors": [] if errors is None else errors}


class PendingReviewsTest(unittest.TestCase):
    def test_personal_review_filter_dedupe_and_order(self):
        actions = {"actions": [
            {"pr": 12, "owner": "AlChambron", "action": "Review"},
            {"pr": 10, "owner": "alchambron", "action": "Review"},
            {"pr": 12, "owner": "alchambron", "action": "Review", "rereview": True},
            {"pr": 11, "owner": "someone-else", "action": "Review"},
            {"pr": 11, "owner": "alchambron", "action": "Respond to human review"},
            {"pr": 11, "owner": "Reviewer needed", "action": "Review"},
        ], "uncertainties": []}
        result = build_pending_reviews(actions, snapshot(pr(10), pr(11), pr(12)), "alchambron")
        self.assertEqual([(item["number"], item["kind"]) for item in result["reviews"]],
                         [(12, "Re-review"), (10, "Review")])
        self.assertEqual(result["reviews"][0]["headSha"], SHA)
        self.assertIn("1. **Re-review #12**", render_markdown(result))
        self.assertNotIn("#11**", render_markdown(result))

    def test_empty_personal_queue_and_partial_coverage_are_explicit(self):
        actions = {"actions": [{"pr": 21, "owner": "other", "action": "Review"}],
                   "uncertainties": [{"pr": 21, "reason": "unclear"}]}
        result = build_pending_reviews(actions, snapshot(pr(21), errors=["#22 omitted"], open_count=2),
                                       "alchambron")
        self.assertEqual(result["reviews"], [])
        self.assertEqual(len(result["coverageWarnings"]), 3)
        self.assertIn("No confirmed reviews currently assigned to you.", render_markdown(result))

    def test_selected_action_must_join_to_complete_pr(self):
        actions = {"actions": [{"pr": 32, "owner": "alchambron", "action": "Review"}]}
        with self.assertRaisesRegex(ValueError, "absent from snapshot"):
            build_pending_reviews(actions, snapshot(pr(31)), "alchambron")
        with self.assertRaisesRegex(ValueError, "headRefOid"):
            build_pending_reviews(actions, snapshot(pr(32, headRefOid="")), "alchambron")
        with self.assertRaisesRegex(ValueError, "invalid URL"):
            build_pending_reviews(actions, snapshot(pr(32, url="https://example.com/32")), "alchambron")

    def test_malformed_action_cannot_silently_disappear(self):
        with self.assertRaisesRegex(ValueError, "action 0 owner"):
            build_pending_reviews({"actions": [{"pr": 10, "action": "Review"}]},
                                  snapshot(pr(10)), "alchambron")
        with self.assertRaisesRegex(ValueError, "duplicate PR"):
            build_pending_reviews({"actions": []}, snapshot(pr(10), pr(10)), "alchambron")


if __name__ == "__main__":
    unittest.main()
