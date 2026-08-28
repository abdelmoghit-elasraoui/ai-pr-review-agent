import asyncio
import unittest
from unittest.mock import patch

from app import llm_client
from app.llm_client import RateLimitExceeded, _release_slot


class TestRateLimiter(unittest.TestCase):
    def setUp(self):
        llm_client._llm_calls.clear()
        llm_client._llm_stats.update(calls=0, rejected=0, errors=0, input_tokens=0, output_tokens=0)

    def test_limit_respected(self):
        async def scenario():
            # Simulates 5 calls with a limit of 3 per 60s
            for _ in range(3):
                with patch.object(llm_client, "LLM_RATE_LIMIT", 3), \
                     patch.object(llm_client, "LLM_RATE_WINDOW_SECONDS", 60):
                    await llm_client._check_rate_limit()
            with patch.object(llm_client, "LLM_RATE_LIMIT", 3), \
                 patch.object(llm_client, "LLM_RATE_WINDOW_SECONDS", 60):
                with self.assertRaises(RateLimitExceeded):
                    await llm_client._check_rate_limit()

        asyncio.run(scenario())

    def test_window_reset(self):
        async def scenario():
            llm_client._llm_calls.clear()
            # Simulates calls older than 60s (expired)
            import time
            llm_client._llm_calls.append(time.monotonic() - 120)
            llm_client._llm_calls.append(time.monotonic() - 121)
            with patch.object(llm_client, "LLM_RATE_LIMIT", 1), \
                 patch.object(llm_client, "LLM_RATE_WINDOW_SECONDS", 60):
                await llm_client._check_rate_limit()  # must NOT raise

        asyncio.run(scenario())

    def test_release_slot_frees_space(self):
        async def scenario():
            with patch.object(llm_client, "LLM_RATE_LIMIT", 1), \
                 patch.object(llm_client, "LLM_RATE_WINDOW_SECONDS", 60):
                await llm_client._check_rate_limit()      # takes the only slot
                _release_slot()                           # releases after a Bedrock failure
                await llm_client._check_rate_limit()      # must pass again

        asyncio.run(scenario())

    def test_rejection_counter(self):
        async def scenario():
            with patch.object(llm_client, "LLM_RATE_LIMIT", 1), \
                 patch.object(llm_client, "LLM_RATE_WINDOW_SECONDS", 60):
                await llm_client._check_rate_limit()
                with self.assertRaises(RateLimitExceeded):
                    await llm_client._check_rate_limit()
            self.assertEqual(llm_client._llm_stats["rejected"], 1)

        asyncio.run(scenario())

    def test_stats_snapshot(self):
        stats = llm_client.get_llm_stats()
        self.assertEqual(set(stats), {"calls", "rejected", "errors", "input_tokens", "output_tokens"})


def run():
    unittest.main()


if __name__ == "__main__":
    run()
