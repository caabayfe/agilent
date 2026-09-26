#!/usr/bin/env bash
# Activate a gateway profile: the ONLY thing a provider swap changes.
#   scripts/gateway.sh fake|fake-swapped|fake-broken|live|live-swapped|live-broken
set -euo pipefail
cd "$(dirname "$0")/.."
profile="${1:?usage: $0 <profile>}"
src="gateway/profiles/${profile}.yaml"
[[ -f "$src" ]] || { echo "unknown profile '$profile'; available: $(ls gateway/profiles | sed 's/.yaml//' | tr '\n' ' ')"; exit 1; }

if [[ "$profile" == live* ]]; then
  # shellcheck disable=SC1091
  set -a; source .env; set +a
  for var in MODEL_FAST_LITELLM_MODEL MODEL_FAST_API_BASE MODEL_FAST_API_KEY \
             MODEL_REASONING_LITELLM_MODEL MODEL_REASONING_API_BASE MODEL_REASONING_API_KEY; do
    [[ -n "$(printenv "$var" || true)" ]] || { echo "missing $var in .env - fill in the model credentials first"; exit 1; }
  done
fi

cp "$src" gateway/active.yaml
echo "$profile" > gateway/active.profile
echo "gateway profile -> $profile"
if docker compose ps --status running --services 2>/dev/null | grep -qx litellm; then
  docker compose restart litellm >/dev/null 2>&1
  printf "waiting for gateway"
  for _ in $(seq 1 60); do
    if [[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q litellm)")" == healthy ]]; then
      echo " ready"; exit 0
    fi
    printf "."; sleep 2
  done
  echo " gateway did not become healthy; see: docker compose logs litellm"; exit 1
fi
