import os
from dotenv import load_dotenv

# Charge les variables d'environnement depuis le fichier .env
load_dotenv()

# Identifiants de la GitHub App (clé privée pour signer les JWT, secret pour valider les webhooks)
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
GITHUB_PRIVATE_KEY_PATH = os.getenv("GITHUB_PRIVATE_KEY_PATH", "./private-key.pem")
GITHUB_PRIVATE_KEY = os.getenv("GITHUB_PRIVATE_KEY", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PORT = int(os.getenv("PORT", "8000"))

# Connexion à Amazon Bedrock (token ABSK, région, modèle LLM)
BEDROCK_BEARER_TOKEN = os.getenv("AWS_BEARER_TOKEN_BEDROCK")
BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "qwen.qwen3-coder-next")

# Limites de coûts US 4.2 : taille max des PRs analysées et rate limiting des appels LLM
MAX_MODIFIED_LINES = int(os.getenv("MAX_MODIFIED_LINES", "500"))
LLM_RATE_LIMIT = int(os.getenv("LLM_RATE_LIMIT", "30"))
LLM_RATE_WINDOW_SECONDS = int(os.getenv("LLM_RATE_WINDOW_SECONDS", "60"))
REVIEW_COOLDOWN_SECONDS = int(os.getenv("REVIEW_COOLDOWN_SECONDS", "120"))
