#!/usr/bin/env bash
# One-shot setup on a fresh Ubuntu VM (e.g. Oracle Cloud free tier, Ampere/ARM or x86).
#   bash setup.sh
# Asks for a Tailscale auth key and (optionally) AnkiWeb login, starts everything,
# and prints the URL to paste into Claude as a custom connector.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  curl -fsSL https://get.docker.com | sh
fi
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then DOCKER="sudo docker"; fi

if [ ! -f .env ]; then
  echo
  echo "Tailscale auth key: https://login.tailscale.com/admin/settings/keys  (Generate auth key)"
  read -rp "Paste it (tskey-auth-...): " TS_AUTHKEY
  echo
  echo "Optional: AnkiWeb login, so the deck also shows up in Anki on your phone/desktop."
  read -rp "AnkiWeb email (leave blank to skip): " ANKIWEB_USERNAME
  ANKIWEB_PASSWORD=""
  if [ -n "$ANKIWEB_USERNAME" ]; then
    read -rsp "AnkiWeb password (used once to get a login key, then erased): " ANKIWEB_PASSWORD; echo
  fi
  echo
  echo "Optional: ElevenLabs key for the good voice + speech-to-text (https://elevenlabs.io → Developers → API keys)."
  read -rsp "ElevenLabs API key (leave blank to skip): " ELEVENLABS_API_KEY; echo
  echo "Optional: Anthropic API key for talking with the tutor (https://console.anthropic.com → API keys)."
  read -rsp "Anthropic API key (leave blank to skip): " ANTHROPIC_API_KEY; echo
  umask 077
  cat > .env <<EOF
TS_AUTHKEY=$TS_AUTHKEY
TS_HOSTNAME=jp-tutor
ANKIWEB_USERNAME=$ANKIWEB_USERNAME
ANKIWEB_PASSWORD=$ANKIWEB_PASSWORD
JP_TUTOR_DECK=Japanese Tutor
JP_TUTOR_PRODUCTION_CARDS=false
ELEVENLABS_API_KEY=$ELEVENLABS_API_KEY
ELEVENLABS_MODEL=eleven_v4_turbo
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
EOF
fi

echo "Building and starting (first build takes a couple of minutes)..."
$DOCKER compose up -d --build

echo -n "Waiting for the tutor to come up"
for _ in $(seq 1 90); do
  if $DOCKER compose exec -T tutor python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" >/dev/null 2>&1; then
    echo " ok"; break
  fi
  echo -n "."; sleep 2
done

# The AnkiWeb password was only needed for the first login (a key is saved in the data volume).
if grep -q '^ANKIWEB_PASSWORD=.\+' .env; then
  sed -i 's/^ANKIWEB_PASSWORD=.*/ANKIWEB_PASSWORD=/' .env
  $DOCKER compose up -d tutor >/dev/null 2>&1 || true
fi

DOMAIN=$($DOCKER compose exec -T tailscale tailscale status --json 2>/dev/null \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)
PATH_PART=$($DOCKER compose exec -T tutor anki-jp-tutor url | tr -d '\r')
APP_PART=$($DOCKER compose exec -T tutor anki-jp-tutor app-url | tr -d '\r')

echo
$DOCKER compose logs tutor 2>/dev/null | grep -E "sync|login" | tail -3 || true
echo
if [ -n "$DOMAIN" ]; then
  echo "App (open on your phone, then Share → Add to Home Screen):"
  echo "  https://$DOMAIN$APP_PART"
  echo
  echo "Claude connector URL (Customize → Connectors → Add custom connector):"
  echo "  https://$DOMAIN$PATH_PART"
  echo
  echo "Both links work like passwords. Keep them private."
  echo
  echo "Quick check (should print {\"ok\":true}; Funnel can take a minute the first time):"
  echo "  curl https://$DOMAIN/health"
else
  echo "Tailscale isn't up yet. Check:  $DOCKER compose logs tailscale"
  echo "Your connector path is: $PATH_PART   (URL = https://<tailscale-name>$PATH_PART)"
fi
