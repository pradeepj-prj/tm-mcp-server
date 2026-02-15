#!/usr/bin/env bash
# Deploy tm-skills-mcp to Cloud Foundry.
#
# Usage:
#   ./deploy.sh           # Deploy (reuses API key from ../tm_app/.api-key)
#
# The MCP server needs the TM Skills API key to authenticate its HTTP calls.
# This script reads it from the API app's .api-key file.

set -euo pipefail

APP_NAME="tm-skills-mcp"
API_KEY_FILE="../tm_app/.api-key"

# ── Get API key ───────────────────────────────────────────────────────────
if [ -f "$API_KEY_FILE" ]; then
    API_KEY=$(cat "$API_KEY_FILE")
    echo "Using API key from $API_KEY_FILE"
else
    echo -n "Enter TM Skills API key: "
    read -rs API_KEY
    echo ""
fi

if [ -z "$API_KEY" ]; then
    echo "ERROR: No API key provided. Generate one with: cd ../tm_app && ./deploy.sh"
    exit 1
fi

# ── Deploy ────────────────────────────────────────────────────────────────
echo "Deploying $APP_NAME..."
cf push --no-start

echo "Setting secrets..."
cf set-env "$APP_NAME" TM_API_KEY "$API_KEY"

echo "Starting $APP_NAME..."
cf start "$APP_NAME"

echo ""
echo "Deployed. MCP SSE endpoint:"
echo "  https://$APP_NAME.cfapps.ap10.hana.ondemand.com/sse"
