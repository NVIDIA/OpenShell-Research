# Pi OpenShell Subagent POC

- Keep this project limited to one parent and one-shot child workers.
- The Python service uses the OpenShell CLI rather than the checked-in Python
  SDK because the current generated Python protobufs are stale.
- Never accept an image, provider, workspace, or command from a sandbox request.
  Those values belong to the Tool Service's trusted worker envelope.
- The parent authors the child policy and sends it with the worker request. The
  Tool Service applies that policy without attenuation checks in this POC;
  OpenShell remains responsible for schema validation and enforcement.
- A request may contain an arbitrary task prompt and at most one public GitHub
  repository. Repository metadata is retained for the demo but is not used to
  generate policy in the Tool Service.
- Use `uv` and the committed lockfile for Python work. Use `npm` only inside
  `pi-package/`.
- Do not commit populated environment files or credentials.
