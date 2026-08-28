import hashlib
import hmac
import logging
import time
import asyncio

from fastapi import APIRouter, Request, HTTPException

from .config import WEBHOOK_SECRET, MAX_MODIFIED_LINES, REVIEW_COOLDOWN_SECONDS
from .commands import handle_issue_comment, handle_review_comment
from .diff_parser import parse_diff, count_modified_lines
from .github_client import fetch_pr_diff, fetch_pr_info, fetch_bot_login, create_pr_review, create_issue_comment
from .llm_client import analyze_diff, RateLimitExceeded, get_llm_stats
from .review_comment import build_review

logger = logging.getLogger(__name__)
router = APIRouter()

_review_cooldown = {}
_cooldown_lock = asyncio.Lock()


async def _should_skip_for_cooldown(installation_id, owner, repo, pr_number):
    # Anti-storm: ignores repeated synchronize events too close together on the same PR (LLM cost)
    key = (installation_id, owner, repo, pr_number)
    now = time.monotonic()
    async with _cooldown_lock:
        last = _review_cooldown.get(key)
        if last is not None and now - last < REVIEW_COOLDOWN_SECONDS:
            logger.info("PR #%d analyzed %ds ago, skipping review (cooldown %ds)",
                        pr_number, int(now - last), REVIEW_COOLDOWN_SECONDS)
            return True
        _review_cooldown[key] = now
    return False


def verify_signature(payload, signature_header):
    # Validates the webhook HMAC-SHA256 signature to prove the request comes from GitHub
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    received = signature_header[7:]
    computed = hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, received)


async def _is_bot_comment(payload):
    # Anti-loop: ignores events sent by bots or by the agent itself
    sender = payload.get("sender", {})
    if sender.get("type") == "Bot" or sender.get("login", "").endswith("[bot]"):
        return True
    installation_id = payload.get("installation", {}).get("id")
    if installation_id:
        try:
            bot_login = await fetch_bot_login(installation_id)
            return sender.get("login") in (bot_login, f"{bot_login}[bot]")
        except Exception:
            pass
    return False


def _log_diff(parsed):
    # Logs the diff summary (files, +/-, ignored files)
    logger.info("Diff: %d files, +%d/-%d lines, %d skipped",
                len(parsed["files"]),
                sum(len(h["lines"]) for f in parsed["files"] for h in f["hunks"] for l in h["lines"] if l["kind"] == "+"),
                sum(len(h["lines"]) for f in parsed["files"] for h in f["hunks"] for l in h["lines"] if l["kind"] == "-"),
                len(parsed["skipped_files"]))
    for f in parsed["files"]:
        logger.info("  %s", f["filename"])
        for h in f["hunks"]:
            for dl in h["lines"][:10]:
                prefix = "+" if dl["kind"] == "+" else "-" if dl["kind"] == "-" else " "
                logger.info("    %s %s", prefix, dl["text"][:80])


async def _process_pr_event(payload):
    # Full PR pipeline: diff -> size -> LLM -> GitHub review (skips if too large or rate limited)
    try:
        installation_id = payload.get("installation", {}).get("id")
        if not installation_id:
            return

        owner = payload["repository"]["owner"]["login"]
        repo = payload["repository"]["name"]
        pr_number = payload["pull_request"]["number"]

        if await _should_skip_for_cooldown(installation_id, owner, repo, pr_number):
            return

        logger.info("Fetching diff for %s/%s PR #%d", owner, repo, pr_number)
        diff_text = await fetch_pr_diff(installation_id, owner, repo, pr_number)
        if not diff_text.strip():
            return

        parsed = parse_diff(diff_text)
        _log_diff(parsed)

        modified_lines = count_modified_lines(parsed)
        if modified_lines > MAX_MODIFIED_LINES:
            logger.info("PR #%d too large (%d modified lines > %d), skipping LLM",
                        pr_number, modified_lines, MAX_MODIFIED_LINES)
            try:
                await create_issue_comment(
                    installation_id, owner, repo, pr_number,
                    f"## 🤖 AI Code Review Agent\n\nThis PR exceeds the limit of {MAX_MODIFIED_LINES} "
                    f"modified lines ({modified_lines} detected). The automatic analysis was skipped "
                    f"to limit LLM API call costs."
                )
            except Exception:
                logger.exception("Could not post size-limit comment")
            return

        logger.info("Sending to LLM...")
        try:
            review = await analyze_diff(parsed)
        except RateLimitExceeded as exc:
            logger.warning("LLM rate limit reached: %s", exc)
            try:
                await create_issue_comment(
                    installation_id, owner, repo, pr_number,
                    "## 🤖 AI Code Review Agent\n\nThe automatic analysis could not be performed: "
                    "the LLM API request limit was reached. Please try again in a few moments."
                )
            except Exception:
                logger.exception("Could not post rate-limit comment")
            return
        logger.info("LLM response:\n%s", review)

        pr_info = await fetch_pr_info(installation_id, owner, repo, pr_number)
        commit_id = pr_info["head"]["sha"]
        overall_body, inline_comments, global_body, invalid = build_review(review, parsed)

        if not inline_comments and not global_body.strip():
            logger.info("No findings to post")
            return

        event = "REQUEST_CHANGES" if all(f.get("severity") == "high" for f in review.get("findings", [])) else "COMMENT"

        await create_pr_review(installation_id, owner, repo, pr_number,
                               commit_id, overall_body, event, inline_comments)
        logger.info("Review posted: %d inline, event=%s", len(inline_comments), event)

    except Exception:
        logger.exception("Failed to process PR")


async def _process_issue_comment(payload):
    # Forwards a conversation comment (mention) to the command handler
    if await _is_bot_comment(payload):
        return
    issue = payload.get("issue", {})
    if not issue.get("pull_request"):
        return
    logger.info("PR comment on #%s by %s: %s",
                issue.get("number"),
                payload.get("sender", {}).get("login"),
                payload.get("comment", {}).get("body", "")[:100])
    await handle_issue_comment(payload)


async def _process_review_comment(payload):
    # Forwards a diff line comment to the command handler (reply within the thread)
    if await _is_bot_comment(payload):
        return
    comment = payload.get("comment", {})
    pr_number = payload.get("pull_request", {}).get("number")
    logger.info("PR review comment on #%s by %s: %s",
                pr_number,
                payload.get("sender", {}).get("login"),
                comment.get("body", "")[:100])
    await handle_review_comment(payload)


@router.post("/webhook")
async def webhook_handler(request: Request):
    # Single entry point: verifies the signature then routes each GitHub event to its handler
    body = await request.body()
    sig = request.headers.get("x-hub-signature-256")

    if not verify_signature(body, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = request.headers.get("x-github-event")
    payload = await request.json()
    logger.info("Event: %s", event)

    if event == "ping":
        return {"msg": "pong"}

    if event == "pull_request" and payload.get("action") in ("opened", "synchronize"):
        await _process_pr_event(payload)

    elif event == "issue_comment" and payload.get("action") == "created":
        await _process_issue_comment(payload)

    elif event == "pull_request_review_comment" and payload.get("action") == "created":
        await _process_review_comment(payload)

    return {"status": "ok"}
