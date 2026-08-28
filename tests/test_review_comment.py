import unittest

from app.review_comment import _find_file, _is_line_in_diff, _build_position_map, build_review

SAMPLE_DIFF = """diff --git a/src/auth.js b/src/auth.js
--- a/src/auth.js
+++ b/src/auth.js
@@ -10,6 +10,8 @@
 async function login(req, res) {
+  const password = req.body.password;
+  const query = \"SELECT * FROM users WHERE username = '\" + req.body.username + \"'\";
   const user = await db.findOne({ username: req.body.username });
"""


class TestReviewComment(unittest.TestCase):
    def _parsed(self):
        from app.diff_parser import parse_diff
        return parse_diff(SAMPLE_DIFF)

    def test_find_file(self):
        parsed = self._parsed()
        self.assertIsNotNone(_find_file("src/auth.js", parsed))
        self.assertIsNone(_find_file("src/other.js", parsed))

    def test_is_line_in_diff(self):
        parsed = self._parsed()
        self.assertTrue(_is_line_in_diff("src/auth.js", 11, parsed))  # ligne + (new)
        self.assertTrue(_is_line_in_diff("src/auth.js", 10, parsed))  # contexte
        self.assertFalse(_is_line_in_diff("src/auth.js", 99, parsed))
        self.assertFalse(_is_line_in_diff("src/other.js", 11, parsed))

    def test_position_map(self):
        parsed = self._parsed()
        pm = _build_position_map(parsed)
        self.assertIsNotNone(pm.get(("src/auth.js", 11)))
        self.assertIsNotNone(pm.get(("src/auth.js", 12)))
        self.assertIsNone(pm.get(("src/auth.js", 99)))

    def test_build_review_valid_and_invalid(self):
        parsed = self._parsed()
        review = {
            "summary": "Critical SQL injection found.",
            "findings": [
                {"file": "src/auth.js", "line": 12, "severity": "high",
                 "category": "security", "message": "SQL injection",
                 "suggestion": "Use parameterized queries"},
                {"file": "src/auth.js", "line": 11, "severity": "medium",
                 "category": "quality", "message": "Password handling"},
                {"file": "src/nonexistent.js", "line": 5, "severity": "low",
                 "category": "quality", "message": "Invalid finding"},
            ],
        }
        overall_body, inline_comments, global_body, invalid = build_review(review, parsed)
        self.assertEqual(len(inline_comments), 2)
        self.assertEqual(len(invalid), 1)
        self.assertIn("SQL injection", overall_body)
        self.assertEqual(inline_comments[0]["path"], "src/auth.js")
        self.assertIn("position", inline_comments[0])
        self.assertIn("suggestion", inline_comments[0]["body"])

    def test_build_review_no_findings(self):
        parsed = self._parsed()
        review = {"summary": "No issues found.", "findings": []}
        body, inline, global_body, invalid = build_review(review, parsed)
        self.assertEqual(inline, [])
        self.assertEqual(global_body, "")
        self.assertIn("No issues found.", body)


def run():
    unittest.main()


if __name__ == "__main__":
    run()
