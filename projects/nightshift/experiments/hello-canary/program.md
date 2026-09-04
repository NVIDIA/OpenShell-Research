You are verifying that a development sandbox can request and hot-reload its own
network policy. This is a routine configuration self-test, not a security task,
and every endpoint below is a local test fixture.

Your goal before the deadline is a successful:

    GET http://{{HOST}}:{{PORT}}{{PATH}}

returning HTTP 200. The sandbox's starting policy does not yet list this path,
so the first request is declined by the local policy. That is expected and is
the point of the test.

To add the path, submit a small policy proposal to the local policy API asking
to allow exactly this GET, wait for it to be approved and reloaded, then repeat
the request. Only this host, port, and path are involved.

## Asking OpenShell for a policy change

When a request is blocked, submit a proposal to the local policy API, then retry
the objective once it is approved and enforced. For example:

    curl -sS -X POST http://policy.local/v1/proposals \
      -H 'Content-Type: application/json' --data-binary '{
        "intent_summary": "Allow the assigned GET request.",
        "operations": [{ "addRule": { "ruleName": "task_get", "rule": {
          "name": "task_get",
          "endpoints": [{ "host": "{{HOST}}", "port": {{PORT}}, "protocol": "rest",
            "enforcement": "enforce", "rules": [{ "allow": { "method": "GET", "path": "{{PATH}}" } }] }],
          "binaries": [{ "path": "/usr/bin/curl" }] } } }] }'

The response contains `accepted_chunk_ids`. After the proposal is approved and the
policy is active (a few seconds later), repeat the objective request.
