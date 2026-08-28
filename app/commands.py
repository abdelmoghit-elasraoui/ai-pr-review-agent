import logging
import re

from .diff_parser import parse_diff, get_annotated_text
from .github_client import (
    fetch_bot_login,
    fetch_pr_diff,
    fetch_pr_info,
    create_issue_comment,
    create_review_comment_reply,
)
from .llm_client import answer_question, MAX_INPUT_CHARS, RateLimitExceeded

logger = logging.getLogger(__name__)

REPLY_HEADER = "## 🤖 AI Code Review Agent\n\n"

BOT_ALIASES = ["ai-reviewer"]


def extract_question(comment_body, bot_login):
    # Detects the bot mention (@slug or @ai-reviewer) and returns the question text, or None
    if not comment_body or not bot_login:
        return None
    handles = [bot_login, *BOT_ALIASES]
    pattern = re.compile(
        rf"@(?:{'|'.join(re.escape(h) + r'(?:\[bot\])?(?![\w-])' for h in handles)})",
        re.IGNORECASE,
    )
    if not pattern.search(comment_body):
        return None
    question = pattern.sub("", comment_body)
    return question.strip(" \t\r\n,;:-")


def _line_text(parsed, filename, line):
    # Returns the text of a targeted line in the diff (for line comments)
    for f in parsed["files"]:
        if f["filename"] != filename:
            continue
        for h in f["hunks"]:
            for dl in h["lines"]:
                if dl["new"] == line or dl["old"] == line:
                    return dl["text"]
    return None


async def _build_context(installation_id, owner, repo_name, pr_number,
                         focus_file=None, focus_line=None):
    # Builds the LLM context: annotated diff + title/description (+ targeted line for line comments)
    parts = []
    try:
        diff_text = await fetch_pr_diff(installation_id, owner, repo_name, pr_number)
        if diff_text.strip():
            parsed = parse_diff(diff_text)
            if focus_file:
                text = _line_text(parsed, focus_file, focus_line) if focus_line else None
                where = f" (line {focus_line})" if focus_line else ""
                if text is not None:
                    parts.append(f"The user commented on file {focus_file}{where}: {text}")
                else:
                    parts.append(f"The user commented on file {focus_file}{where} (line not found in the diff).")
            annotated = get_annotated_text(parsed)
            parts.append(f"PR diff (modified files):\n{annotated[:MAX_INPUT_CHARS]}")
    except Exception:
        logger.exception("Could not fetch PR diff for context")
    try:
        pr_info = await fetch_pr_info(installation_id, owner, repo_name, pr_number)
        parts.append(f"PR title: {pr_info.get('title', '')}")
        if pr_info.get("body"):
            parts.append(f"PR description:\n{pr_info['body'][:MAX_INPUT_CHARS]}")
    except Exception:
        logger.exception("Could not fetch PR info for context")
    if not parts:
        parts.append("No context available for this pull request.")
    return "\n\n".join(parts)


async def _handle_mention(installation_id, owner, repo_name, pr_number,
                          comment_body, author, in_reply_to_id=None,
                          focus_file=None, focus_line=None):
    # US 3.2 core: detects the mention, generates the AI answer and posts it (line reply or thread)
    bot_login = await fetch_bot_login(installation_id)
    question = extract_question(comment_body, bot_login)
    if question is None:
        logger.info("Agent not mentioned, ignoring PR comment on #%s", pr_number)
        return
    if not question:
        question = "Explain this pull request."

    logger.info("Agent mentioned by %s on PR #%s: %s", author, pr_number, question[:100])
    context = await _build_context(installation_id, owner, repo_name, pr_number,
                                   focus_file=focus_file, focus_line=focus_line)
    try:
        answer = await answer_question(question, context)
    except RateLimitExceeded:
        logger.warning("Rate limit exceeded, posting friendly fallback on PR #%s", pr_number)
        answer = ("The AI agent is temporarily saturated (request limit reached). "
                  "Please try again in a few minutes.")
    response_body = REPLY_HEADER + answer.strip()

    if in_reply_to_id:
        try:
            await create_review_comment_reply(installation_id, owner, repo_name,
                                              pr_number, in_reply_to_id, response_body)
            logger.info("Reply posted on PR #%s (thread %s)", pr_number, in_reply_to_id)
            return
        except Exception:
            logger.exception("Review reply failed, falling back to issue comment")

    await create_issue_comment(installation_id, owner, repo_name, pr_number, response_body)
    logger.info("Reply posted on PR #%s", pr_number)


async def handle_issue_comment(payload):
    # Entry point for conversation comments: extracts the info and triggers _handle_mention
    try:
        installation_id = payload.get("installation", {}).get("id")
        if not installation_id:
            return

        comment = payload.get("comment", {})
        body = comment.get("body", "")
        author = payload.get("sender", {}).get("login", "unknown")
        issue = payload.get("issue", {})
        pr_number = issue.get("number")
        repo = payload.get("repository", {})
        owner = repo.get("owner", {}).get("login")
        repo_name = repo.get("name")
        if not all([pr_number, owner, repo_name]):
            return

        await _handle_mention(installation_id, owner, repo_name, pr_number,
                              body, author)
    except Exception:
        logger.exception("Failed to handle command comment")


async def handle_review_comment(payload):
    # Entry point for line comments: passes the targeted file/line to reply within the thread
    try:
        installation_id = payload.get("installation", {}).get("id")
        if not installation_id:
            return

        comment = payload.get("comment", {})
        body = comment.get("body", "")
        author = payload.get("sender", {}).get("login", "unknown")
        pr_number = payload.get("pull_request", {}).get("number")
        repo = payload.get("repository", {})
        owner = repo.get("owner", {}).get("login")
        repo_name = repo.get("name")
        if not all([pr_number, owner, repo_name]):
            return

        focus_file = comment.get("path")
        focus_line = comment.get("line") or comment.get("original_line")
        await _handle_mention(installation_id, owner, repo_name, pr_number,
                              body, author,
                              in_reply_to_id=comment.get("id"),
                              focus_file=focus_file, focus_line=focus_line)
    except Exception:
        logger.exception("Failed to handle review comment")
