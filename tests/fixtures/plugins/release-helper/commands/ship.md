---
description: Cut a release
argument-hint: <version>
allowed-tools: Bash(git tag:*)
---
Prepare release $1: update the changelog with the release-helper:changelog skill, run the
tests, then ask the release-helper:release-reviewer agent to review.
