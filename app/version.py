"""The running release version.

A constant rather than something derived from git: `.dockerignore` excludes
`.git/`, so inside the image there is no tag to read. This is the only place the
version is written down.

Bump this by hand, in the pull request that becomes the release. Then run the
Release workflow (Actions -> Release) with the same version: it refuses unless
this constant already matches, runs the tests, and only then tags, publishes and
builds the image. It does not write here itself — see .github/workflows/release.yml
for why.

`test_version.py` fails if the constant and the newest tag disagree, so a
forgotten bump is caught before it ships.
"""

__version__ = '1.0.1-dev'
