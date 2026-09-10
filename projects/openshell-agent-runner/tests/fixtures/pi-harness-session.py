# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise the real Pi harness against scripted inference, with no network egress."""

import json
import os
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCENARIO = sys.argv[1]
RUNTIME = Path("/sandbox/oar-runtime")
REQUESTS = []


class InferenceFixture(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        REQUESTS.append(request)
        turn = len(REQUESTS)
        tool = None
        arguments = None
        if SCENARIO == "schema-retry":
            tool = "submit_result"
            arguments = {"result": {} if turn == 1 else {"status": "pass"}}
        elif SCENARIO == "read-tool" and turn == 1:
            tool = "read"
            arguments = {"path": str(RUNTIME / "skills" / "qa-skill" / "SKILL.md")}
        elif SCENARIO == "read-tool" and turn == 2:
            tool = "bash"
            program = "import json,os,pathlib; print(json.dumps({'cwd':os.getcwd(),'mode':os.environ['QA_MODE'],'input':pathlib.Path('input.txt').read_text(),'support':pathlib.Path('/workspace/support.txt').read_text()}))"
            arguments = {"command": "python3 -c " + shlex.quote(program)}
        elif SCENARIO == "custom-tool" and turn == 1:
            tool = "qa_echo"
            arguments = {}
        delta = {"role": "assistant"}
        if tool:
            delta["tool_calls"] = [
                {
                    "index": 0,
                    "id": f"call_{turn}",
                    "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(arguments)},
                }
            ]
        else:
            delta["content"] = "QA plain result: café."
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for contents, finish in [(delta, None), ({}, "tool_calls" if tool else "stop")]:
            event = {
                "id": f"qa-{turn}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "qa/model",
                "choices": [{"index": 0, "delta": contents, "finish_reason": finish}],
            }
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


assert os.geteuid() != 0, "The agent must run as an unprivileged user"
RUNTIME.mkdir()
server = ThreadingHTTPServer(("127.0.0.1", 0), InferenceFixture)
threading.Thread(target=server.serve_forever, daemon=True).start()
model = json.loads(Path("/profile/models.json").read_text())
model["providers"]["openshell"]["baseUrl"] = f"http://127.0.0.1:{server.server_port}/v1"
(RUNTIME / "models.json").write_text(json.dumps(model))
(RUNTIME / "settings.json").write_bytes(Path("/profile/settings.json").read_bytes())
(RUNTIME / "prompt.md").write_text(
    "Complete this QA task. Use the supplied tools when requested.\n"
)
workspace = Path("/workspace/small project")
workspace.mkdir()
(workspace / "input.txt").write_text("uploaded input café")
Path("/workspace/support.txt").write_text("supporting evidence")
(workspace / "AGENTS.md").write_text("IMPLICIT_CONTEXT_MUST_NOT_LOAD")
implicit = workspace / ".pi"
(implicit / "extensions").mkdir(parents=True)
(implicit / "extensions" / "untrusted.ts").write_text(
    "export default function() { throw new Error('Implicit extension loaded'); }"
)
(implicit / "skills" / "untrusted").mkdir(parents=True)
(implicit / "skills" / "untrusted" / "SKILL.md").write_text(
    "---\nname: untrusted\ndescription: IMPLICIT_SKILL_MUST_NOT_LOAD\n---\nUntrusted input.\n"
)
skill = RUNTIME / "skills" / "qa-skill"
skill.mkdir(parents=True)
(skill / "SKILL.md").write_text(
    "---\nname: qa-skill\ndescription: EXPLICIT_SKILL_DESCRIPTION\n---\nEXPLICIT_SKILL_BODY: Use supporting evidence.\n"
)
arguments = [
    "--provider",
    "openshell",
    "--model",
    "qa/model",
    "--thinking",
    "off",
    "--skill",
    str(skill),
]
tools = []
if SCENARIO == "schema-retry":
    (RUNTIME / "output.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["status"],
                "additionalProperties": False,
                "properties": {"status": {"const": "pass"}},
            }
        )
    )
    arguments += ["--extension", "/extensions/submit-result.ts"]
    tools = ["read", "submit_result"]
elif SCENARIO == "read-tool":
    tools = ["read", "bash"]
elif SCENARIO == "custom-tool":
    (RUNTIME / "custom.ts").write_text("""
import { defineTool } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';
export default function(pi) {
  pi.registerTool(defineTool({name: 'qa_echo', label: 'QA echo', description: 'Return test evidence',
    parameters: Type.Object({}),
    async execute() { return {content: [{type: 'text', text: 'CUSTOM_TOOL_EXECUTED'}]}; }
  }));
}
""")
    arguments += ["--extension", str(RUNTIME / "custom.ts")]
    tools = ["read", "qa_echo"]
elif SCENARIO == "missing-tool":
    tools = ["missing_tool"]
else:
    tools = ["read"]
(RUNTIME / "tools.json").write_text(json.dumps(tools))
arguments += [
    "--extension",
    "/extensions/validate-tools.ts",
    "--tools",
    ",".join(tools),
]
environment = os.environ.copy()
environment["REPOSITORY_ROOT"] = (
    "/workspace/absent" if SCENARIO == "invalid-cwd" else str(workspace)
)
environment["QA_MODE"] = "nonsecret value=two"
completed = subprocess.run(
    ["bash", "/opt/oar/pi/exec.sh", *arguments],
    env=environment,
    capture_output=True,
    text=True,
    timeout=30,
)
result_file = Path("/sandbox/artifacts/result")
if SCENARIO in ("missing-tool", "invalid-cwd"):
    assert completed.returncode == 2, completed.stderr
    assert not REQUESTS, "Invalid configuration must stop before inference"
    assert not result_file.exists()
    assert (
        "unavailable tools" if SCENARIO == "missing-tool" else "not a directory"
    ) in completed.stderr
else:
    assert completed.returncode == 0, completed.stderr
    assert result_file.exists(), completed.stderr
    if SCENARIO == "schema-retry":
        assert json.loads(result_file.read_text()) == {"status": "pass"}
        assert len(REQUESTS) == 2
    else:
        assert result_file.read_text().strip() == "QA plain result: café."
    serialized = json.dumps(REQUESTS)
    assert "IMPLICIT_CONTEXT_MUST_NOT_LOAD" not in serialized
    assert "IMPLICIT_SKILL_MUST_NOT_LOAD" not in serialized
    assert "EXPLICIT_SKILL_DESCRIPTION" in serialized
    if SCENARIO == "read-tool":
        assert len(REQUESTS) == 3
        assert "EXPLICIT_SKILL_BODY" in serialized
        assert (
            "uploaded input caf" in serialized and "supporting evidence" in serialized
        )
        assert (
            "/workspace/small project" in serialized
            and "nonsecret value=two" in serialized
        )
    if SCENARIO == "custom-tool":
        assert len(REQUESTS) == 2
        assert "CUSTOM_TOOL_EXECUTED" in serialized
print(
    json.dumps(
        {
            "scenario": SCENARIO,
            "result": "pass",
            "inference_requests": len(REQUESTS),
            "agent_exit_code": completed.returncode,
            "inference": "scripted local fixture, not a live model",
        }
    )
)
server.shutdown()
