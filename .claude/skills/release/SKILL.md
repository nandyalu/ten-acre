---
name: release
description: Use when the user asks to cut, tag or publish a new version of the app on GitHub. Bumps the version, writes the release notes into docs/releases.md, tags, and creates the GitHub release with those notes before the workflow can make one with generated notes.
---

# release

**A release is a signed `v*` tag on `main`.** The tag starts `.github/workflows/release.yml`. That workflow builds the zip and the `ghcr.io` image, and attaches the zip to the release. **The workflow writes notes only when no release exists yet**, and those notes are generated. So create the release with your notes as soon as you push the tag. The build takes about a minute before it gets to that step.

## Before you start

1. Make sure that `main` is clean and the same as `origin/main`, and that the backend tests pass.
2. Pick the version. Increase the patch number for fixes and small changes. Increase the minor number when a setting, the data layout or the way to deploy changes.
3. List the changes since the last tag:

   ```bash
   git log --format='--- %s%n%b' "$(git describe --tags --abbrev=0)"..HEAD
   ```

## Write the notes

Add a section at the top of `docs/releases.md`, under the intro, named `## vX.Y.Z — YYYY-MM-DD`. Write one list item for each change, with 1 or 2 sentences each:

- Start with a bold sentence that says what changed, from the view of the person who runs the app or of the agent.
- Then say why, or what it fixes. Give exact setting names, error strings and values.
- Merge the commits that make one change into one item. Put a change that is only about tests or tooling last.

Obey the `orwell-writing` skill. Notes are prose.

## Bump, tag, publish

The version is in three places, and the tag must equal `version` in `pyproject.toml`, or the workflow fails:

```bash
V=X.Y.Z; OLD=$(git describe --tags --abbrev=0 | sed 's/^v//')
sed -i "s/^version = \"$OLD\"/version = \"$V\"/" pyproject.toml
sed -i "s/ten-acre-v$OLD/ten-acre-v$V/g" docs/deploying.md
uv lock
```

Put the notes in a scratch file: only the list, without the heading. Then:

```bash
git add pyproject.toml uv.lock docs/deploying.md docs/releases.md
git commit -m "Release v$V"
git tag -m "v$V" "v$V"          # tags are signed here: a tag with no message fails
git push origin main "v$V"
gh release create "v$V" --title "v$V" --verify-tag --notes-file <notes-file>
```

Run the `stale-check` skill before the commit, as for any commit.

## After the push

Wait for the workflow in the background (see the `wait-in-background` skill):

```bash
gh run watch "$(gh run list --workflow release.yml -L 1 --json databaseId -q '.[0].databaseId')" --exit-status
gh release view "v$V" --json assets -q '.assets[].name'   # expect ten-acre-vX.Y.Z.zip
```

Both jobs, `release` and `docker`, must pass. If the workflow made the release first, the notes are generated. Replace them with `gh release edit "v$V" --notes-file <notes-file>`.

A release does not redeploy anything. Each deployment builds `ten-acre:local` on its own host (see `.claude/rules/deployment.md`).
