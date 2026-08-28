import time
import logging
from pathlib import Path

import httpx
import jwt

from .config import GITHUB_APP_ID, GITHUB_PRIVATE_KEY, GITHUB_PRIVATE_KEY_PATH

logger = logging.getLogger(__name__)

_token_cache = {}
_bot_login = None


def _load_private_key():
    # Clé privée PEM de la GitHub App : env var prioritaire (cloud), sinon fichier local
    if GITHUB_PRIVATE_KEY:
        return GITHUB_PRIVATE_KEY
    path = Path(GITHUB_PRIVATE_KEY_PATH)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.read_text()


def _generate_jwt():
    # Signe un JWT RS256 (10 min) avec l'ID de l'app pour authentifier les appels
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 600, "iss": GITHUB_APP_ID}
    return jwt.encode(payload, _load_private_key(), algorithm="RS256")


def _headers(installation_id=None):
    # Construit les headers d'auth: token d'installation si fourni, sinon JWT de l'app
    if installation_id:
        token = _get_token(installation_id)
    else:
        token = _generate_jwt()
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def _get_token(installation_id):
    # Récupère/cache le token d'installation (55 min) pour un repo donné
    now = time.time()
    if installation_id in _token_cache:
        token, expires_at = _token_cache[installation_id]
        if now < expires_at - 120:
            return token

    resp = httpx.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers=_headers(),
    )
    resp.raise_for_status()
    token = resp.json()["token"]
    _token_cache[installation_id] = (token, now + 3300)
    return token


async def fetch_pr_diff(installation_id, owner, repo, pr_number):
    # Récupère le diff brut d'une PR (header Accept diff obligatoire)
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}.diff"
    headers = {**_headers(installation_id), "Accept": "application/vnd.github.v3.diff"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text


async def fetch_pr_info(installation_id, owner, repo, pr_number):
    # Récupère les métadonnées de la PR (titre, description, head.sha)
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(installation_id))
        resp.raise_for_status()
        return resp.json()


async def fetch_bot_login(installation_id):
    # Récupère (en cache) le slug de l'app pour détecter les mentions du bot
    global _bot_login
    if _bot_login:
        return _bot_login
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.github.com/app", headers=_headers())
        resp.raise_for_status()
        data = resp.json()
        _bot_login = data.get("slug") or data["owner"]["login"]
        logger.info("Bot login: %s", _bot_login)
        return _bot_login


async def create_pr_review(installation_id, owner, repo, pr_number,
                           commit_id, body, event, comments):
    # Crée une review GitHub (COMMENT/REQUEST_CHANGES) avec commentaires inline
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    payload = {"commit_id": commit_id, "body": body, "event": event, "comments": comments}
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=_headers(installation_id))
        resp.raise_for_status()
        data = resp.json()
        logger.info("Review created: id=%s, event=%s, %d comments", data.get("id"), event, len(comments))
        return data


async def create_issue_comment(installation_id, owner, repo, issue_number, body):
    # Poste un commentaire dans la conversation de la PR (issues/comments)
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json={"body": body}, headers=_headers(installation_id))
        resp.raise_for_status()
        data = resp.json()
        logger.info("Issue comment created: id=%s on issue #%d", data.get("id"), issue_number)
        return data


async def create_review_comment_reply(installation_id, owner, repo, pr_number, in_reply_to_id, body):
    # Répond dans un fil de commentaire de ligne existant (in_reply_to)
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments"
    payload = {"in_reply_to": in_reply_to_id, "body": body}
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=_headers(installation_id))
        resp.raise_for_status()
        data = resp.json()
        logger.info("Review comment reply created: id=%s in thread %s", data.get("id"), in_reply_to_id)
        return data
