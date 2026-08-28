import json
import base64
import logging
import asyncio
import time
from collections import deque

from .config import BEDROCK_BEARER_TOKEN, BEDROCK_REGION, BEDROCK_MODEL_ID
from .config import LLM_RATE_LIMIT, LLM_RATE_WINDOW_SECONDS

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 180_000
MAX_RESPONSE_TOKENS = 4096

_llm_calls = deque()
_llm_lock = asyncio.Lock()

_llm_stats = {
    "calls": 0,
    "rejected": 0,
    "errors": 0,
    "input_tokens": 0,
    "output_tokens": 0,
}


class RateLimitExceeded(Exception):
    # Levée quand le nombre max d'appels LLM dans la fenêtre est dépassé
    pass


def _acquire_slot():
    # Réserve un slot de rate limit (fenêtre glissante): False si la limite est atteinte
    now = time.monotonic()
    while _llm_calls and now - _llm_calls[0] >= LLM_RATE_WINDOW_SECONDS:
        _llm_calls.popleft()
    if len(_llm_calls) >= LLM_RATE_LIMIT:
        return False
    _llm_calls.append(now)
    return True


def _release_slot():
    # Libère le slot le plus récent (appelé quand l'appel Bedrock échoue)
    if _llm_calls:
        _llm_calls.pop()


def get_llm_stats():
    # Retourne une copie des statistiques d'utilisation du LLM (pour observabilité)
    return dict(_llm_stats)


async def _check_rate_limit():
    # Vérifie atomiquement le rate limit avant chaque appel LLM, lève RateLimitExceeded sinon
    async with _llm_lock:
        if not _acquire_slot():
            _llm_stats["rejected"] += 1
            raise RateLimitExceeded(
                f"LLM rate limit exceeded: {LLM_RATE_LIMIT} calls per "
                f"{LLM_RATE_WINDOW_SECONDS}s"
            )

QUESTION_SYSTEM_PROMPT = """\
You are a senior software engineer embedded in a GitHub pull request discussion.
A developer asked a question about this pull request. Answer clearly and concisely,
based ONLY on the provided context (diff, title, description).

Rules:
- Do not invent code or details that are not in the context. If the context is
  insufficient to answer, say so explicitly and suggest what would help.
- Answer in the same language as the question (French by default).
- Keep the answer focused and practical. Use short code snippets if useful.
- Give the answer directly, without a greeting.
"""

SYSTEM_PROMPT = """\
You are a senior software engineer performing a code review on a pull request.
Analyze the provided diff and identify concrete issues in the changed code.

Focus on:
- Security: injection risks, exposed secrets, missing auth checks
- Bugs: null access, race conditions, incorrect error handling
- Performance: N+1 queries, blocking sync calls, memory leaks
- Code quality: duplicate code, missing validation, unclear naming
- Best practices: unhandled promises, missing try/catch, hardcoded credentials

Rules:
- Only report issues in added or modified lines (lines with + prefix).
- Use the "new" line number from [old=... new=N] annotations.
- Return empty findings array if no issues found.
- Be specific and actionable. Include a concrete suggestion.

Return ONLY valid JSON with this structure:
{"summary": "...", "findings": [{"file": "...", "line": 42, "severity": "high|medium|low", "category": "security|bug|performance|quality|best-practice", "message": "...", "suggestion": "..."}]}
"""


def _decode_credentials():
    # Décode le token ABSK (base64) en access key + secret key AWS
    if not BEDROCK_BEARER_TOKEN:
        return "", ""
    b64 = BEDROCK_BEARER_TOKEN[4:]
    decoded = base64.b64decode(b64).decode("utf-8")
    access_key, secret_key = decoded.split(":", 1)
    return access_key, secret_key


def _get_bedrock_client():
    # Crée le client boto3 bedrock-runtime : credentials statiques si ABSK présent,
    # sinon rôle IAM de l'instance (cloud — même région que le modèle)
    import boto3
    if not BEDROCK_BEARER_TOKEN:
        return boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    access_key, secret_key = _decode_credentials()
    return boto3.client(
        "bedrock-runtime",
        region_name=BEDROCK_REGION,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _extract_json(text):
    # Extrait et parse le JSON de la réponse LLM (tolère les blocs ``` et le texte autour)
    text = text.strip()
    if text.startswith("```"):
        text = text[text.find("\n") + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError(f"No JSON found in LLM response: {text[:200]}...")


async def _call_llm(system_prompt, user_message):
    # Appel unique à Bedrock Converse (rate limité, exécuté hors boucle d'événements)
    await _check_rate_limit()
    try:
        client = _get_bedrock_client()
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.converse(
                modelId=BEDROCK_MODEL_ID,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_message}]}],
                inferenceConfig={"maxTokens": MAX_RESPONSE_TOKENS, "temperature": 0.2},
            ),
        )
    except Exception:
        _release_slot()
        _llm_stats["errors"] += 1
        raise
    content = response["output"]["message"]["content"]
    text = next(block["text"] for block in content if "text" in block)
    usage = response.get("usage", {})
    _llm_stats["calls"] += 1
    _llm_stats["input_tokens"] += usage.get("inputTokens", 0)
    _llm_stats["output_tokens"] += usage.get("outputTokens", 0)
    logger.info("LLM: %d chars, tokens in=%d out=%d", len(text), usage.get("inputTokens", 0), usage.get("outputTokens", 0))
    return text


def _file_to_text(file_data):
    # Rend le diff d'un seul fichier au format annoté (old/new) pour analyse séparée
    hunks_text = []
    for h in file_data["hunks"]:
        hunks_text.append(f"@@ {h['header']}")
        for dl in h["lines"]:
            old = str(dl["old"]) if dl["old"] is not None else "~"
            new = str(dl["new"]) if dl["new"] is not None else "~"
            hunks_text.append(f"[old={old} new={new}] {dl['kind']} {dl['text']}")
    return "\n".join(hunks_text)


async def analyze_diff(parsed):
    # Analyse le diff via le LLM (un appel si petit, fichier par fichier sinon), retourne summary+findings
    from .diff_parser import get_annotated_text

    if not parsed["files"]:
        return {"summary": "No code changes to review.", "findings": []}

    annotated = get_annotated_text(parsed)

    if len(annotated) <= MAX_INPUT_CHARS:
        user_msg = f"Review the following pull request diff. Report every concrete issue you find.\n\n{annotated}"
        text = await _call_llm(SYSTEM_PROMPT, user_msg)
        return _extract_json(text)

    logger.info("Diff too large (%d chars), analyzing file by file", len(annotated))
    all_findings = []
    summaries = []

    for f in parsed["files"]:
        file_text = _file_to_text(f)
        if len(file_text) > MAX_INPUT_CHARS:
            file_text = file_text[:MAX_INPUT_CHARS] + "\n[... truncated ...]"

        try:
            user_msg = f"Review the following pull request diff. Report every concrete issue you find.\n\n{file_text}"
            text = await _call_llm(SYSTEM_PROMPT, user_msg)
            result = _extract_json(text)
            summaries.append(f"{f['filename']}: {result.get('summary', 'No issues')}")
            all_findings.extend(result.get("findings", []))
        except RateLimitExceeded as exc:
            logger.warning("Skipping %s: %s", f["filename"], exc)
            summaries.append(f"{f['filename']}: skipped (rate limit)")
            break
        except Exception:
            logger.exception("LLM failed for %s", f["filename"])
            summaries.append(f"{f['filename']}: analysis failed")

    return {"summary": "\n".join(summaries), "findings": all_findings}


async def answer_question(question, context):
    # Génère une réponse IA à une question posée sur la PR, basée sur le contexte fourni
    user_msg = f"Question du développeur :\n{question}\n\nContexte de la pull request :\n{context}"
    return await _call_llm(QUESTION_SYSTEM_PROMPT, user_msg)
