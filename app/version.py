"""The running release version.

A constant rather than something derived from git: `.dockerignore` excludes
`.git/`, so inside the image there is no tag to read. This is the only place the
version is written down.

**Bump this by hand** before tagging a release, so the tag and the source it
points at agree. Nothing edits it automatically.

Two things check that it was not forgotten. `test_version.py` compares it to the
newest tag in the local checkout, and the Publish workflow compares it to the
release tag and refuses to build an image that would misreport its own version.
"""

__version__ = '0.9.1'
