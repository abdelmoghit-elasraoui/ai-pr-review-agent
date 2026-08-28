import os
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

# GitHub App credentials (private key to sign JWTs, secret to validate webhooks)
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
GITHUB_PRIVATE_KEY_PATH = os.getenv("GITHUB_PRIVATE_KEY_PATH", "./private-key.pem")
GITHUB_PRIVATE_KEY = os.getenv("GITHUB_PRIVATE_KEY", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PORT = int(os.getenv("PORT", "8000"))

# Amazon Bedrock connection (ABSK token, region, LLM model)
BEDROCK_BEARER_TOKEN = os.getenv("AWS_BEARER_TOKEN_BEDROCK")
BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "qwen.qwen3-coder-next")

# US 4.2 cost limits: max size of analyzed PRs and LLM call rate limiting
MAX_MODIFIED_LINES = int(os.getenv("MAX_MODIFIED_LINES", "500"))
LLM_RATE_LIMIT = int(os.getenv("LLM_RATE_LIMIT", "30"))
LLM_RATE_WINDOW_SECONDS = int(os.getenv("LLM_RATE_WINDOW_SECONDS", "60"))
REVIEW_COOLDOWN_SECONDS = int(os.getenv("REVIEW_COOLDOWN_SECONDS", "120"))
