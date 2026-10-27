#!/usr/bin/env bash
# Renders the IAM policy templates (policies/, pg/policies/, kb/policies/) into .rendered/ with this account's values.
# Placeholders: __NAME__ (NAME_PREFIX, default quick-mcp-demo) __ACCT__ __REGION__ __WG_ID__ (Redshift Serverless workgroup id) __KB_ID__ __KB_REGION__
# Inputs (env): AWS_REGION (default us-east-1), WG_ID, KB_ID, KB_REGION (default = AWS_REGION). ACCT is looked up.
# A placeholder that is still unresolved is only an error if a template actually contains it AND you ask for it:
# setup.sh needs WG_ID, setup_kb.sh needs KB_ID. Unset values are rendered as UNSET_<NAME> so the file is visibly wrong.
set -euo pipefail
cd "$(dirname "$0")"
REGION="${AWS_REGION:-us-east-1}"
NAME="${NAME_PREFIX:-quick-mcp-demo}"
ACCT="${ACCT:-$(aws sts get-caller-identity --query Account --output text)}"
rm -rf .rendered && mkdir -p .rendered
for f in policies/*.json pg/policies/*.json kb/policies/*.json; do
  mkdir -p ".rendered/$(dirname "$f")"
  sed -e "s/__NAME__/${NAME}/g" -e "s/__ACCT__/${ACCT}/g" -e "s/__REGION__/${REGION}/g" \
      -e "s/__WG_ID__/${WG_ID:-UNSET_WG_ID}/g" -e "s/__KB_ID__/${KB_ID:-UNSET_KB_ID}/g" \
      -e "s/__KB_REGION__/${KB_REGION:-$REGION}/g" "$f" > ".rendered/$f"
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' ".rendered/$f"   # must be valid JSON
done
echo "rendered policies for account ${ACCT} region ${REGION} -> agentcore-verify/.rendered/"
