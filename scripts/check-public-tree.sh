#!/usr/bin/env bash
set -euo pipefail

mode="${1:-staged}"
scanner_path="scripts/check-public-tree.sh"
content_pattern='(^|[^0-9])(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})([^0-9]|$)|-----BEGIN ([A-Z0-9 ]+ )?PRIVATE KEY-----|github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]+|glpat-[A-Za-z0-9_-]+|xox[baprs]-[A-Za-z0-9-]+|sk-[A-Za-z0-9_-]{20,}|[A-Za-z][A-Za-z0-9+.-]*://[^/@[:space:]]+:[^/@[:space:]]+@|(^|[^[:alnum:]_])\.ssh/[^[:space:]]+'
filename_pattern='(^|/)(\.env($|\.)|id_(rsa|ed25519)($|\.)|credentials?($|\.)|secrets?($|\.))|\.(pem|key|p12|pfx|jks)$'

failed=0

report() {
  local category="$1" path="$2"
  printf 'public-safety: %s in %s\n' "$category" "$path" >&2
  failed=1
}

scan_blob() {
  local path="$1"
  local source="$2"

  [[ "$path" == "$scanner_path" ]] && return
  [[ "$path" =~ $filename_pattern ]] && report "sensitive filename" "$path"
  if printf '%s' "$source" | grep -Iq . && printf '%s' "$source" | LC_ALL=C grep -Eiq "$content_pattern"; then
    report "private address or credential-like value" "$path"
  fi
}

scan_staged() {
  local path content
  while IFS= read -r -d '' path; do
    content="$(git show ":$path" 2>/dev/null || true)"
    scan_blob "$path" "$content"
  done < <(git diff --cached --name-only --diff-filter=ACMR -z)
}

scan_history() {
  local oid path type content
  while IFS=' ' read -r oid path; do
    [[ -z "${path:-}" ]] && continue
    type="$(git cat-file -t "$oid" 2>/dev/null || true)"
    [[ "$type" == "blob" ]] || continue
    content="$(git cat-file blob "$oid" 2>/dev/null || true)"
    scan_blob "$path" "$content"
  done < <(git rev-list --objects --all)
}

case "$mode" in
  staged) scan_staged ;;
  history) scan_history ;;
  *) printf 'usage: %s {staged|history}\n' "$0" >&2; exit 2 ;;
esac

if (( failed )); then
  printf 'public-safety: blocked; move installation-specific data to .private-ops/ and unstage it.\n' >&2
  exit 1
fi

printf 'public-safety: %s scan passed.\n' "$mode"
