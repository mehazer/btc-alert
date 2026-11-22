#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <commit-message> <path> [path ...]" >&2
  exit 2
fi

commit_message=$1
shift
paths=("$@")

git add -- "${paths[@]}"
if git diff --cached --quiet; then
  echo "No generated-data changes to persist."
  exit 0
fi

# Preserve only the generated-data patch. Never rebase or push the checked-out
# feature branch: it may contain application code that has not been merged to main.
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT
patch_file="$tmp_dir/generated-data.patch"
git -c color.ui=false diff --cached --binary -- "${paths[@]}" > "$patch_file"

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

for attempt in 1 2 3 4 5; do
  git fetch origin main
  git checkout --detach -f origin/main
  git reset --hard origin/main

  if ! git apply --3way --index "$patch_file"; then
    echo "Could not apply generated-data patch to current main (attempt $attempt/5)." >&2
    git reset --hard origin/main
    sleep $((attempt * 3))
    continue
  fi

  if git diff --cached --quiet; then
    echo "Generated data already matches main."
    exit 0
  fi

  git commit -m "$commit_message"
  if git push origin HEAD:main; then
    echo "Generated data pushed to main."
    exit 0
  fi

  echo "Main advanced before push; retrying generated-data-only update ($attempt/5)."
  sleep $((attempt * 3))
done

echo "Failed to persist generated data to main after 5 attempts." >&2
exit 1
