#!/usr/bin/env bash
# Fails if the tree contains anything that looks like a credential or a personal identifier.
# Run before every push.
set -euo pipefail
cd "$(dirname "$0")/.."
patterns=(
  'sk-[A-Za-z0-9_-]{20,}'            # OpenAI-style keys
  'sk-ant-[A-Za-z0-9_-]{20,}'        # Anthropic keys
  'ghp_[A-Za-z0-9]{30,}'             # GitHub tokens
  'xox[bpas]-[A-Za-z0-9-]{10,}'      # Slack tokens
  'AKIA[0-9A-Z]{16}'                 # AWS access keys
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'op://'                            # 1Password references
  '[a-z0-9-]+\.tail[0-9a-f]{6}\.ts\.net'   # a real tailnet hostname
  '\b100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}\b'  # tailnet IPs
  '[0-9]{17,20}'                     # Discord/Telegram-style IDs
)
extra_file="${SPEAKEASY_PRIVATE_TERMS:-}"   # optional local file of personal terms, one per line
status=0
for p in "${patterns[@]}"; do
  if git grep -nIE -e "$p" -- . ':!scripts/scan-secrets.sh' >/tmp/speakeasy-scan.$$ 2>/dev/null; then
    echo "FOUND pattern: $p"; cat /tmp/speakeasy-scan.$$; status=1
  fi
done
if [[ -n "$extra_file" && -f "$extra_file" ]]; then
  while IFS= read -r term; do
    [[ -z "$term" ]] && continue
    if git grep -nIiF -e "$term" -- . >/tmp/speakeasy-scan.$$ 2>/dev/null; then
      echo "FOUND private term"; cut -d: -f1,2 /tmp/speakeasy-scan.$$; status=1
    fi
  done < "$extra_file"
fi
rm -f /tmp/speakeasy-scan.$$
[[ $status -eq 0 ]] && echo "scan-secrets: clean"
exit $status
