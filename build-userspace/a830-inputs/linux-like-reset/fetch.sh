#!/bin/sh
# fetch.sh <owner/repo> <branch> <path>...: saves raw files under src/<repo>@<branch>/<path> and logs the URL + sha256.
set -e
repo=$1; branch=$2; shift 2
for p in "$@"; do
  out="src/$(echo "$repo" | tr '/' '_')@$(echo "$branch" | tr '/' '_')/$p"
  mkdir -p "$(dirname "$out")"
  url="https://raw.githubusercontent.com/$repo/$branch/$p"
  code=$(curl -sS -m 60 -L -o "$out" -w '%{http_code}' "$url")
  if [ "$code" != 200 ]; then rm -f "$out"; echo "MISS $code $url"; continue; fi
  echo "$(shasum -a 256 "$out" | cut -c1-16) $url" | tee -a sources.txt
done
