# Pi OpenShell Subagent POC

- Keep this project limited to one parent and one-shot child workers.
- The Python service uses the OpenShell CLI rather than the checked-in Python
  SDK because the current generated Python protobufs are stale.
- Never accept an image, provider, workspace, or command from a sandbox request.
  Those values belong to the Tool Service's trusted worker envelope.
- The parent authors the child policy and sends it with the worker request. The
  Tool Service runs a fail-closed LLM permission review against the live
  parent policy before asking OpenShell to create the child.
- Keep the POC limited to external-job delegation. Do not add collaboration
  messages, mailboxes, shared memory, workflow barriers, or a browser UI.
- A request contains only the caller name, Pi step index, delegated prompt, and
  child policy. Image, provider, workspace, model, command, and timeouts remain
  trusted Tool Service configuration.
- Use `uv` and the committed lockfile for Python work. Use `npm` only inside
  `pi-package/`.
- Do not commit populated environment files or credentials.
