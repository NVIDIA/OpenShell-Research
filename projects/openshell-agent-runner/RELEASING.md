# Releasing openshell-agent-runner

The release process follows DataDesigner's local PyPI publishing pattern. A
version tag supplies the package version, and Twine uses the
`openshell-research` repository already configured in `~/.pypirc`.

The publishing script does not inspect or print `.pypirc`. Twine reads that file
only when an upload is performed.

## Validate a release

From this directory, run:

```bash
make publish VERSION=0.1.0 DRY_RUN=1
```

The dry run fetches `origin/main` and tags, confirms that local `main` is current,
runs the project validation suite, and builds the requested version. It verifies
one wheel and one source distribution with Twine. The temporary local tag is
removed before the command exits; nothing is pushed or uploaded.

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
3. Verifies the exact wheel and source distribution with Twine.
4. Pushes `v0.1.0`, establishing the public source commit before publication.
5. Uploads only those two artifacts through the `openshell-research` `.pypirc`
   repository.

If the upload fails after the tag is pushed, correct the cause and rerun the
same command. A matching existing tag is treated as a retry; a tag on another
commit is rejected.
