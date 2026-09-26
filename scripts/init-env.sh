#!/usr/bin/env bash
# Create .env from .env.example, filling every empty local secret with a random value.
# Model credentials are left for you to fill in. Existing .env files are never overwritten.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo ".env already exists; leaving it untouched."
  exit 0
fi

secret() { openssl rand -hex 24; }
umask 077
while IFS= read -r line; do
  case "$line" in
    POSTGRES_PASSWORD=|DB_PASSWORD_*=|AGENT_CLIENT_SECRET=|FAKE_LLM_API_KEY=) echo "${line}$(secret)" ;;
    LITELLM_MASTER_KEY=) echo "${line}sk-$(secret)" ;;
    *) echo "$line" ;;
  esac
done < .env.example > .env
echo "Created .env (mode 600) with fresh local secrets. Add your model credentials to MODEL_* when ready."
