import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app import webhook


class TestWebhookUS42(unittest.TestCase):
    """US 4.2 — skip des grosses PRs + rate limit (avec mocks, sans réseau)."""

    def test_large_pr_skipped_with_comment(self):
        big_diff = (
            "diff --git a/src/a.js b/src/a.js\n"
            "--- a/src/a.js\n+++ b/src/a.js\n"
            "@@ -1,3 +1,5 @@\n"
            "+x\n+y\n+z\n"
        )

        payload = {
            "action": "opened",
            "installation": {"id": 1},
            "repository": {"owner": {"login": "owner"}, "name": "repo"},
            "pull_request": {"number": 42},
        }

        async def scenario():
            with patch.object(webhook, "MAX_MODIFIED_LINES", 2), \
                 patch.object(webhook, "fetch_pr_diff", AsyncMock(return_value=big_diff)), \
                 patch.object(webhook, "create_issue_comment", AsyncMock()) as mock_comment, \
                 patch.object(webhook, "analyze_diff", AsyncMock()) as mock_llm:
                await webhook._process_pr_event(payload)
                self.assertEqual(mock_comment.await_count, 1, "un commentaire explicatif posté")
                mock_llm.assert_not_awaited()
                body = mock_comment.await_args.args[4]
                self.assertIn("lignes modifiées", body)
                self.assertIn("2", body)

        asyncio.run(scenario())

    def test_small_pr_calls_llm(self):
        small_diff = (
            "diff --git a/src/a.js b/src/a.js\n"
            "--- a/src/a.js\n+++ b/src/a.js\n"
            "@@ -1,3 +1,4 @@\n"
            "+x\n"
        )

        payload = {
            "action": "opened",
            "installation": {"id": 1},
            "repository": {"owner": {"login": "owner"}, "name": "repo"},
            "pull_request": {"number": 43},
        }

        async def scenario():
            with patch.object(webhook, "MAX_MODIFIED_LINES", 500), \
                 patch.object(webhook, "fetch_pr_diff", AsyncMock(return_value=small_diff)), \
                 patch.object(webhook, "fetch_pr_info", AsyncMock(return_value={"head": {"sha": "abc"}})), \
                 patch.object(webhook, "analyze_diff", AsyncMock(return_value={"summary": "", "findings": []})), \
                 patch.object(webhook, "build_review", Mock(return_value=("body", [], "", ""))):
                await webhook._process_pr_event(payload)
                # pas de commentaire de skip posté, pas d'exception
                webhook.analyze_diff.assert_awaited()

        asyncio.run(scenario())

    def test_synchronize_storm_respects_cooldown(self):
        small_diff = (
            "diff --git a/src/a.js b/src/a.js\n"
            "--- a/src/a.js\n+++ b/src/a.js\n"
            "@@ -1,3 +1,4 @@\n"
            "+x\n"
        )

        def payload(number):
            return {
                "action": "synchronize",
                "installation": {"id": 1},
                "repository": {"owner": {"login": "owner"}, "name": "repo"},
                "pull_request": {"number": number},
            }

        async def scenario():
            webhook._review_cooldown.clear()
            with patch.object(webhook, "MAX_MODIFIED_LINES", 500), \
                 patch.object(webhook, "REVIEW_COOLDOWN_SECONDS", 60), \
                 patch.object(webhook, "fetch_pr_diff", AsyncMock(return_value=small_diff)), \
                 patch.object(webhook, "fetch_pr_info", AsyncMock(return_value={"head": {"sha": "abc"}})), \
                 patch.object(webhook, "analyze_diff", AsyncMock(return_value={"summary": "", "findings": []})), \
                 patch.object(webhook, "build_review", Mock(return_value=("body", [], "", ""))):
                await webhook._process_pr_event(payload(44))  # 1er synchronize → analyse
                await webhook._process_pr_event(payload(44))  # 2e synchronize immédiat → skip
                self.assertEqual(webhook.analyze_diff.await_count, 1, "un seul appel LLM pour la même PR dans la fenêtre")
                await webhook._process_pr_event(payload(45))  # autre PR → analysée
                self.assertEqual(webhook.analyze_diff.await_count, 2, "l'autre PR n'est pas bloquée")

        asyncio.run(scenario())


def run():
    unittest.main()


if __name__ == "__main__":
    run()
