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

The dry run checks the clean `main` branch, runs the project validation suite,
builds the wheel and source distribution, and validates both with Twine. It
does not create a tag or upload anything.

## Publish a release

After the release commit is merged and checked out on a clean `main` branch:

```bash
make publish VERSION=0.1.0
```

The script:

1. Runs the same checks and builds the distributions.
2. Creates `v0.1.0` locally.
3. Rebuilds so the distributions carry version `0.1.0`.
4. Uploads only `openshell-agent-runner` through the `openshell-research`
   `.pypirc` repository.
5. Pushes the tag after the upload succeeds.

If a build or upload fails after tag creation, delete the unpushed local tag
before retrying:

```bash
git tag -d v0.1.0
```
