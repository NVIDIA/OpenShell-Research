# Releasing openshell-agent-runner

The release process publishes to PyPI from a local shell. A version tag supplies
the package version, and `uv publish` uploads the built distributions.

Set a PyPI API token in the shell before publishing:

```bash
export UV_PUBLISH_TOKEN="pypi-..."
```

The script reads the token from the environment and never prints it. It does not
use `.pypirc`. If the export is in `~/.bashrc`, open an interactive shell or
source that file before running `make publish`.

## Validate a release

From this directory, run:

```bash
make publish VERSION=0.1.0 DRY_RUN=1
```

The dry run fetches `origin/main` and tags, confirms that local `main` is current,
runs the project validation suite, and builds the requested version. It checks
one wheel and one source distribution with `uv publish --dry-run`. Trusted
publishing is disabled because this workflow is intentionally local. The
temporary local tag is removed before the command exits; nothing is pushed or
uploaded.

To validate or publish deliberately from another branch, add
`ALLOW_NON_MAIN=1`:

```bash
make publish VERSION=0.1.0 DRY_RUN=1 ALLOW_NON_MAIN=1
```

This bypasses the branch and `origin/main` commit checks. The working tree must
still be clean, an existing version tag must point to the current commit, and
every release check must pass.

## Publish a release

After the release commit is merged and checked out on a clean `main` branch:

```bash
make publish VERSION=0.1.0
```

The script:

1. Fetches `origin/main` and tags and confirms that local `main` is current.
2. Runs the same checks and builds version `0.1.0` from the local tag.
3. Checks the exact wheel and source distribution with `uv publish --dry-run`.
4. Pushes `v0.1.0`, establishing the public source commit before publication.
5. Uploads only those two artifacts to PyPI with `uv publish`.
6. Prints the version-specific PyPI project link.

If the upload fails after the tag is pushed, check the `uv publish` output or
PyPI to identify which artifacts are missing. Retry a missing artifact with:

```bash
make publish VERSION=0.1.0 RETRY_ARTIFACT=sdist
```

Use `wheel` instead of `sdist` when the wheel is missing. If neither artifact
was accepted, use `RETRY_ARTIFACT=both`. Every retry rebuilds and checks both
artifacts from the tagged commit, then uploads only the selected file or files.
This works with private indexes that reject duplicate filenames. A retry
requires the remote tag to match the current commit. If both files are already
present, there is nothing to retry.
