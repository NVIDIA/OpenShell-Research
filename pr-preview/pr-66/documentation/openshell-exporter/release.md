---
title: "Source releases"
description: "OpenShell Event Exporter — source releases."
agent_markdown: true
---

# Source releases

Current version: [v0.0.4](https://github.com/NVIDIA/OpenShell-Research/releases).
This project releases source. Users [build their own OCI images](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/deploy/README.md)
and optionally publish them to their own registry. The Helm chart is installed
from the checkout. No exporter registry or image-publishing credentials are required.

`VERSION` must match both Collector builders, local module references, Helm
versions, local image examples, the compatibility manifest, and release notes.
Before tagging reviewed `main`:

```sh
./scripts/check-version.sh
mise run pre-commit
./scripts/build-images.sh all
```

Use `projects/openshell-exporter/vX.Y.Z` tags so Go can resolve the nested module.
Create a source release in OpenShell Research using the matching
`release/notes/vX.Y.Z.md`. Never move a published tag. The shared repository owns
its CI; this project does not publish container images.

Use a tagged Git checkout for builds and demos. Users own image scanning, signing,
registry access, and runtime compatibility. These source releases are experimental,
provided as-is, and used at your own risk. See [limitations](compatibility.md).
