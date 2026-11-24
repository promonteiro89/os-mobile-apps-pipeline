#!/usr/bin/env bash
# ==============================================================================
# Configure the GitHub Actions secrets & variables used by the release pipeline.
# ==============================================================================
# Usage:
#   ./scripts/setup-secrets.sh [--repo <owner/repo>] [--env <environment>]
#
#   --repo  Target repository (default: the repository of the current directory)
#   --env   Store the *secrets* in a GitHub environment (e.g. store-production)
#           instead of at repository level. See docs/SETUP.md.
#
# Requirements: GitHub CLI (gh) authenticated with admin rights on the target repo.
# Secret values are read without echo and passed to gh on stdin (never as arguments).
# ==============================================================================

set -euo pipefail

TARGET_REPO=""
TARGET_ENV=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --repo|--env)
      if [[ $# -lt 2 || -z "$2" ]]; then echo "$1 needs a value" >&2; exit 1; fi
      if [[ $1 == --repo ]]; then TARGET_REPO="$2"; else TARGET_ENV="$2"; fi
      shift 2 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$TARGET_REPO" ]; then
  TARGET_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
fi

# Always non-empty, so "${SECRET_FLAGS[@]}" is safe under `set -u` on macOS bash 3.2.
VAR_FLAGS=(--repo "$TARGET_REPO")
SECRET_FLAGS=(--repo "$TARGET_REPO")
if [ -n "$TARGET_ENV" ]; then
  SECRET_FLAGS+=(--env "$TARGET_ENV")
fi

echo "=========================================================="
echo " Store release pipeline: secrets setup"
echo " Repository : $TARGET_REPO"
echo " Secrets in : ${TARGET_ENV:-repository level}"
echo "=========================================================="
echo ""

echo "--> Variables (not sensitive; press Enter to skip any value that is already set)"
read -r -p "LifeTime host (e.g. mycompany-lt.outsystemsenterprise.com): " LT_HOST
if [ -n "$LT_HOST" ]; then
  gh variable set LIFETIME_HOST "${VAR_FLAGS[@]}" --body "$LT_HOST"
  echo "  ✓ LIFETIME_HOST"
fi
read -r -p "Google Play release notes locales, comma-separated (Enter = keep; en-US when never set): " RN_LANGS
if [ -n "$RN_LANGS" ]; then
  gh variable set RELEASE_NOTES_LANGUAGES "${VAR_FLAGS[@]}" --body "$RN_LANGS"
  echo "  ✓ RELEASE_NOTES_LANGUAGES"
fi
echo ""

echo "--> Secrets (press Enter to skip any value that is already set)"

read -r -p "App Store Connect Key ID: " ASC_KEY_ID
if [ -n "$ASC_KEY_ID" ]; then
  printf '%s' "$ASC_KEY_ID" | gh secret set ASC_KEY_ID "${SECRET_FLAGS[@]}"
  echo "  ✓ ASC_KEY_ID"
fi

read -r -p "App Store Connect Issuer ID: " ASC_ISSUER_ID
if [ -n "$ASC_ISSUER_ID" ]; then
  printf '%s' "$ASC_ISSUER_ID" | gh secret set ASC_ISSUER_ID "${SECRET_FLAGS[@]}"
  echo "  ✓ ASC_ISSUER_ID"
fi

read -rs -p "LifeTime API token (input hidden): " LT_TOKEN
echo ""
if [ -n "$LT_TOKEN" ]; then
  printf '%s' "$LT_TOKEN" | gh secret set LIFETIME_API_TOKEN "${SECRET_FLAGS[@]}"
  echo "  ✓ LIFETIME_API_TOKEN"
fi
unset LT_TOKEN

read -r -p "Path to the Google Play service account JSON key: " GP_FILE
if [ -n "$GP_FILE" ] && [ -f "$GP_FILE" ]; then
  gh secret set GOOGLE_PLAY_JSON_KEY "${SECRET_FLAGS[@]}" < "$GP_FILE"
  echo "  ✓ GOOGLE_PLAY_JSON_KEY (from $GP_FILE)"
elif [ -n "$GP_FILE" ]; then
  echo "  ! File not found: $GP_FILE (skipped)"
fi

read -r -p "Path to the App Store Connect AuthKey_<KEY_ID>.p8 file: " ASC_FILE
if [ -n "$ASC_FILE" ] && [ -f "$ASC_FILE" ]; then
  gh secret set ASC_KEY_P8 "${SECRET_FLAGS[@]}" < "$ASC_FILE"
  echo "  ✓ ASC_KEY_P8 (from $ASC_FILE)"
elif [ -n "$ASC_FILE" ]; then
  echo "  ! File not found: $ASC_FILE (skipped)"
fi

echo ""
echo "Done. Verify with: gh secret list ${SECRET_FLAGS[*]}"
echo "Then delete the key files from your disk."
