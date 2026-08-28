import asyncio
import unittest

from app.diff_parser import parse_diff, count_modified_lines, should_ignore_file

SAMPLE_DIFF = """diff --git a/src/auth.js b/src/auth.js
--- a/src/auth.js
+++ b/src/auth.js
@@ -10,6 +10,8 @@
 async function login(req, res) {
+  const password = req.body.password;
+  const query = \"SELECT * FROM users WHERE username = '\" + req.body.username + \"'\";
-  const old = 'x';
   const user = await db.findOne({ username: req.body.username });
"""


class TestDiffParser(unittest.TestCase):
    def test_should_ignore_file(self):
        cases = [
            ("src/main.js", False),
            ("package-lock.json", True),
            ("yarn.lock", True),
            ("dist/bundle.js", True),
            ("image.png", True),
            ("node_modules/foo.js", True),
            ("__pycache__/module.pyc", True),
            ("backend/routes/alerts.js", False),
            ("src/proto_pb2.py", True),
        ]
        for path, expected in cases:
            self.assertEqual(should_ignore_file(path), expected, path)

    def test_parse_diff_counts(self):
        parsed = parse_diff(SAMPLE_DIFF)
        self.assertEqual(len(parsed["files"]), 1)
        file_data = parsed["files"][0]
        self.assertEqual(file_data["filename"], "src/auth.js")
        self.assertEqual(len(file_data["hunks"]), 1)
        kinds = [dl["kind"] for dl in file_data["hunks"][0]["lines"]]
        self.assertIn("+", kinds)
        self.assertIn("-", kinds)

    def test_count_modified_lines(self):
        parsed = parse_diff(SAMPLE_DIFF)
        self.assertEqual(count_modified_lines(parsed), 3)  # 2 additions + 1 deletion

    def test_parse_diff_skips_locked(self):
        diff = """diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,2 +1,3 @@
+  "version": 1
"""
        parsed = parse_diff(diff)
        self.assertEqual(parsed["files"], [])
        self.assertEqual(parsed["skipped_files"], ["package-lock.json"])


def run():
    unittest.main()


if __name__ == "__main__":
    run()
