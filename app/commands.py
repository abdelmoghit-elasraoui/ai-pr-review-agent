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
    # Détecte la mention du bot (@slug ou @ai-reviewer) et retourne le texte de la question, sinon None
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
    # Retourne le texte d'une ligne ciblée dans le diff (pour les commentaires de ligne)
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
    # Construit le contexte pour le LLM: diff annoté + titre/description (+ ligne ciblée si commentaire de ligne)
    parts = []
    try:
        diff_text = await fetch_pr_diff(installation_id, owner, repo_name, pr_number)
        if diff_text.strip():
            parsed = parse_diff(diff_text)
            if focus_file:
                text = _line_text(parsed, focus_file, focus_line) if focus_line else None
                where = f" (ligne {focus_line})" if focus_line else ""
                if text is not None:
                    parts.append(f"L'utilisateur a commenté le fichier {focus_file}{where} : {text}")
                else:
                    parts.append(f"L'utilisateur a commenté le fichier {focus_file}{where} (ligne introuvable dans le diff).")
            annotated = get_annotated_text(parsed)
            parts.append(f"Diff de la PR (fichiers modifiés) :\n{annotated[:MAX_INPUT_CHARS]}")
    except Exception:
        logger.exception("Could not fetch PR diff for context")
    try:
        pr_info = await fetch_pr_info(installation_id, owner, repo_name, pr_number)
        parts.append(f"Titre de la PR : {pr_info.get('title', '')}")
        if pr_info.get("body"):
            parts.append(f"Description de la PR :\n{pr_info['body'][:MAX_INPUT_CHARS]}")
    except Exception:
        logger.exception("Could not fetch PR info for context")
    if not parts:
        parts.append("Aucun contexte disponible pour cette pull request.")
    return "\n\n".join(parts)


async def _handle_mention(installation_id, owner, repo_name, pr_number,
                          comment_body, author, in_reply_to_id=None,
                          focus_file=None, focus_line=None):
    # Cœur US 3.2: détecte la mention, génère la réponse IA et la poste (reply de ligne ou fil)
    bot_login = await fetch_bot_login(installation_id)
    question = extract_question(comment_body, bot_login)
    if question is None:
        logger.info("Agent not mentioned, ignoring PR comment on #%s", pr_number)
        return
    if not question:
        question = "Explique-moi cette pull request."

    logger.info("Agent mentioned by %s on PR #%s: %s", author, pr_number, question[:100])
    context = await _build_context(installation_id, owner, repo_name, pr_number,
                                   focus_file=focus_file, focus_line=focus_line)
    try:
        answer = await answer_question(question, context)
    except RateLimitExceeded:
        logger.warning("Rate limit exceeded, posting friendly fallback on PR #%s", pr_number)
        answer = ("L'agent IA est temporairement saturé (limite de requêtes atteinte). "
                  "Merci de réessayer dans quelques minutes.")
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
    # Point d'entrée des commentaires de conversation: extrait les infos et déclenche _handle_mention
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
    # Point d'entrée des commentaires de ligne: passe fichier/ligne ciblés pour répondre dans le fil
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
