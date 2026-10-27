#!/usr/bin/env bash
# Fetches a Cognito access token (client_credentials) for the quick-mcp-demo Gateway and copies it to the clipboard.
# The token and the client secret are never printed. Paste the token into Quick Desktop -> Remote connector -> Token.
#   ./get_token.sh           copy token to clipboard (macOS pbcopy)
#   ./get_token.sh --check   fetch a token and test it against the Gateway (prints only the HTTP status and tool count)
set -euo pipefail
NAME="${NAME_PREFIX:-quick-mcp-demo}"   # resource name prefix; change it to run a second, independent copy in the same account
export AWS_PROFILE="${AWS_PROFILE:?set AWS_PROFILE}" AWS_REGION="${AWS_REGION:-us-east-1}" AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}" AWS_PAGER=""

GW_ID=$(aws bedrock-agentcore-control list-gateways --query "items[?name=='${NAME}-gw'].gatewayId | [0]" --output text)
GW_URL=$(aws bedrock-agentcore-control get-gateway --gateway-identifier "$GW_ID" --query gatewayUrl --output text)

TOKEN=$(aws secretsmanager get-secret-value --secret-id ${NAME}/cognito-client --query SecretString --output text \
 | python3 -c '
import base64, json, sys, urllib.parse, urllib.request
c = json.load(sys.stdin)
req = urllib.request.Request(c["token_url"],
  data=urllib.parse.urlencode({"grant_type": "client_credentials", "scope": c["scope"]}).encode(),
  headers={"Authorization": "Basic " + base64.b64encode((c["client_id"] + ":" + c["client_secret"]).encode()).decode(),
           "Content-Type": "application/x-www-form-urlencoded"})
r = json.load(urllib.request.urlopen(req, timeout=30))
sys.stdout.write(r["access_token"])
')

if [ "${1:-}" = "--check" ]; then
  BODY=$(curl -s -m 30 -o /tmp/gw_check.json -w "%{http_code}" -X POST "$GW_URL" \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}')
  echo "gateway: $GW_URL"
  echo "tools/list HTTP status: $BODY"
  python3 -c 'import json;d=json.load(open("/tmp/gw_check.json"));print("tools:",len(d["result"]["tools"]))' 2>/dev/null || echo "(response was not a tools list)"
  rm -f /tmp/gw_check.json
else
  printf %s "$TOKEN" | pbcopy
  echo "Token copied to clipboard (valid for about 1 hour). Gateway URL:"
  echo "$GW_URL"
fi
