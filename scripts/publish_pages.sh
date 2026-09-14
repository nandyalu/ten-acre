#!/bin/sh
# Publishes the static public site, on a loop.
#
# The site is two things that change at very different rates. The Angular
# shell is baked into this image and changes only when the container is
# rebuilt — about once a month. The exporter's JSON
# (backend/services/snapshot_export.py) changes every 15 minutes.
#
# **R2_SNAPSHOT_BUCKET decides which of two shapes this runs in.**
#
# Unset, every round bundles the shell and the data together and deploys both
# to Cloudflare Pages. That needs no setup beyond a Pages token and is what a
# self-hosted copy gets.
#
# Set, the shell is deployed to Pages once at startup and the data goes to an
# R2 bucket on its own domain, uploading only the files whose contents
# actually changed. The reason is deployment count. Cloudflare refuses to
# delete a Pages project with more than a hundred deployments, and a deploy
# every 15 minutes makes 96 a day. Measured on this deployment: 89 files and
# 3.9 MB, all 89 rewritten every round and almost none of them different.
set -eu

PROJECT_NAME="${PAGES_PROJECT_NAME:-ten-acre}"
BUCKET="${R2_SNAPSHOT_BUCKET:-}"
DATA_DIR="/data/public_snapshot"
SITE_DIR="./site"
COMBINED_DIR="/tmp/combined"
INTERVAL="${PUBLISH_INTERVAL_SECONDS:-900}"
# Below the publish interval, so a reader never waits a whole extra round for
# a file the cache is still holding.
CACHE_CONTROL="${SNAPSHOT_CACHE_CONTROL:-public, max-age=300}"
MANIFEST=/tmp/uploaded.sha256

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ] || [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  echo "CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID must both be set — refusing to start." >&2
  exit 1
fi

# The bundle learns where its data lives when the image is built
# (SNAPSHOT_BASE_URL), and this container learns where to upload when it runs
# (R2_SNAPSHOT_BUCKET). If the two disagree, the site shows no data and no
# error. On 2026-09-12 an image was built without SNAPSHOT_BASE_URL, and the
# public site was empty for two days. Stop before a wrong bundle replaces the
# site on Pages.
SNAPSHOT_ROOT=$(grep -rhoE 'snapshotRoot:[^,}]*' "$SITE_DIR" --include='main-*.js' | head -1 | cut -d: -f2- | tr -d "\`\"'")
case "$SNAPSHOT_ROOT" in
  http://*|https://*) BUNDLE_READS_URL=1 ;;
  *) BUNDLE_READS_URL=0 ;;
esac
if [ -n "$BUCKET" ] && [ "$BUNDLE_READS_URL" -eq 0 ]; then
  echo "R2_SNAPSHOT_BUCKET is set, but the bundle reads its data from '$SNAPSHOT_ROOT'. Rebuild the image with --build-arg SNAPSHOT_BASE_URL=<the bucket's public URL>. Refusing to start." >&2
  exit 1
fi
if [ -z "$BUCKET" ] && [ "$BUNDLE_READS_URL" -eq 1 ]; then
  echo "R2_SNAPSHOT_BUCKET is not set, but the bundle reads its data from '$SNAPSHOT_ROOT'. Set R2_SNAPSHOT_BUCKET, or rebuild the image without SNAPSHOT_BASE_URL. Refusing to start." >&2
  exit 1
fi
echo "The bundle reads its data from '$SNAPSHOT_ROOT'."

wrangler() { npx --yes wrangler@4.130.0 "$@"; }

# One-time and idempotent. wrangler pages deploy does not create a project on
# its own — this fails harmlessly ("already exists") on every run after the
# first, which is simpler than tracking whether it has run before.
wrangler pages project create "$PROJECT_NAME" --production-branch=main || true

# Deploys whatever is in $1 to Pages.
deploy_pages() {
  wrangler pages deploy "$1" --project-name="$PROJECT_NAME" --branch=main --commit-dirty=true
}

# Uploads every JSON file whose contents differ from the last successful
# round. Compares hashes rather than timestamps: the exporter rewrites all 89
# files every round, and an already-graded signal's JSON is byte-identical
# every time.
sync_to_r2() {
  [ -d "$DATA_DIR" ] || { echo "No exported data yet at $DATA_DIR — nothing to upload."; return 0; }

  ( cd "$DATA_DIR" && find . -type f -name '*.json' -exec sha256sum {} + ) | sort > /tmp/current.sha256
  touch "$MANIFEST"
  # Lines in the new list that are not in the old one: new files, and files
  # whose contents changed.
  comm -23 /tmp/current.sha256 "$MANIFEST" > /tmp/changed.sha256

  count=$(wc -l < /tmp/changed.sha256)
  if [ "$count" -eq 0 ]; then
    echo "Nothing changed this round."
    return 0
  fi

  failed=0
  while IFS= read -r line; do
    key=${line#*  ./}
    if ! wrangler r2 object put "$BUCKET/$key" --file="$DATA_DIR/$key" --remote \
        --content-type=application/json --cache-control="$CACHE_CONTROL"; then
      echo "Failed to upload $key." >&2
      failed=1
    fi
  done < /tmp/changed.sha256

  # Only record the round as done when every file in it landed. A partial
  # round leaves the manifest alone, so the same files are retried next time
  # rather than being remembered as uploaded.
  if [ "$failed" -eq 0 ]; then
    mv /tmp/current.sha256 "$MANIFEST"
    echo "Uploaded $count changed file(s) to $BUCKET."
  else
    echo "Some uploads failed — retrying the whole set next round." >&2
  fi
}

if [ -n "$BUCKET" ]; then
  # The shell alone. Its /api/... calls go to the bucket's own domain, which
  # Dockerfile.pages-publisher baked into the bundle at build time.
  echo "Snapshot goes to R2 bucket '$BUCKET'. Deploying the app shell once."
  deploy_pages "$SITE_DIR"
fi

while true; do
  if [ -n "$BUCKET" ]; then
    sync_to_r2 || echo "Sync failed this round — will retry in ${INTERVAL}s." >&2
  else
    rm -rf "$COMBINED_DIR"
    mkdir -p "$COMBINED_DIR"
    cp -r "$SITE_DIR/." "$COMBINED_DIR/"

    if [ -d "$DATA_DIR" ]; then
      mkdir -p "$COMBINED_DIR/data"
      cp -r "$DATA_DIR/." "$COMBINED_DIR/data/"
    else
      # Not an error on a fresh deployment: the exporter's first run may not
      # have landed yet. Publish the app shell anyway rather than blocking on
      # it — every page already handles a fetch that comes back empty.
      echo "No exported data yet at $DATA_DIR — publishing the app shell without it."
    fi

    if deploy_pages "$COMBINED_DIR"; then
      echo "Published."
    else
      echo "Publish failed this round — will retry in ${INTERVAL}s." >&2
    fi
  fi

  echo "Next check in ${INTERVAL}s."
  sleep "$INTERVAL"
done
