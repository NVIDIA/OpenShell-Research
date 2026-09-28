from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from test_runtime import job, phases, settings

from openshell_tool_service.policy_composer import OpenShellPolicyComposer
from openshell_tool_service.policy_reviewer import PolicyReviewError
from openshell_tool_service.runtime import OpenShellCliRuntime, RuntimeExecutionError


@pytest.mark.parametrize("count", [0, 2])
def test_composer_supplies_effective_policies_and_removes_temp_file(tmp_path, caplog, count):
    caplog.set_level(logging.DEBUG)
    config = replace(settings(tmp_path), child_provider="nv-inference")
    paths = []

    def runner(command, _input, _timeout):
        path = Path(command[command.index("--policy") + 1])
        paths.append(path)
        assert path.read_text() == job().child_policy
        assert command[command.index("--provider") + 1] == "nv-inference"
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "schema_version": 1, "provider": "nv-inference",
            "parent_policy": {"version": 1}, "child_policy": {"version": 1},
            "provider_rule_count": count,
        }), "")

    prepared = OpenShellPolicyComposer(config, runner).prepare(job())
    assert json.loads(prepared.child_policy) == {"version": 1}
    assert all(not path.exists() for path in paths)
    assert "provider nv-inference is attached to parent pi-parent" in caplog.text
    assert "ms)" in caplog.text
    if count == 0:
        assert "provider adds no network rules; child policy is unchanged" in caplog.text
    else:
        assert "provider contributes 2 network rule(s)" in caplog.text


@pytest.mark.parametrize("output,code", [("not json", 0), ("{}", 0), ("", 1)])
def test_composer_fails_closed(tmp_path, output, code):
    config = replace(settings(tmp_path), child_provider="nv-inference")
    with pytest.raises(PolicyReviewError, match="no child was created"):
        OpenShellPolicyComposer(config, lambda cmd, *_: subprocess.CompletedProcess(
            cmd, code, output, "unavailable"
        )).prepare(job())


@pytest.mark.parametrize("changed", [None, "child", "parent"])
def test_provider_runtime_checks_actual_policies_before_pi(tmp_path, changed):
    config = replace(settings(tmp_path), child_provider="nv-inference")
    calls = []
    policy = {"version": 1, "network_policies": {}}

    def runner(command, _input, _timeout):
        calls.append(list(command))
        if command[1:3] == ["policy", "get"]:
            role = "child" if command[3] == job().sandbox_name else "parent"
            actual = {**policy, "version": 2} if role == changed else policy
            return subprocess.CompletedProcess(command, 0, json.dumps({"policy": actual}), "")
        return subprocess.CompletedProcess(command, 0, "DONE", "")

    runtime = OpenShellCliRuntime(config, runner)
    kwargs = dict(expected_policy=json.dumps(policy), expected_parent_policy=json.dumps(policy))
    if changed:
        with pytest.raises(RuntimeExecutionError) as caught:
            runtime.run(job(), **kwargs)
        assert caught.value.code == "policy-snapshot-changed"
        assert "exec" not in phases(calls)
    else:
        assert runtime.run(job(), **kwargs).output == "DONE"
        assert phases(calls) == ["create", "get", "get", "exec", "logs", "delete"]
    assert "--provider" in calls[0]
    assert phases(calls)[-1] == "delete"


def test_provider_runtime_requires_proof_before_create(tmp_path):
    config = replace(settings(tmp_path), child_provider="nv-inference")
    def runner(*_args):
        pytest.fail("no OpenShell commands should run")
    with pytest.raises(RuntimeExecutionError) as caught:
        OpenShellCliRuntime(config, runner).run(job())
    assert caught.value.code == "policy-composition-required"
