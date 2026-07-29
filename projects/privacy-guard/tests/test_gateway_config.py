"""OpenShell gateway registration management tests."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from privacy_guard.gateway_config import (
    MAX_MIDDLEWARE_REGISTRATION_NAME_BYTES,
    GatewayConfigError,
    GatewayConfigRemoval,
    GatewayConfigUpdate,
    default_gateway_config_path,
    remove_gateway_config,
    update_gateway_config,
    validate_middleware_name,
)


def test_default_gateway_config_path_uses_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENSHELL_GATEWAY_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert default_gateway_config_path() == tmp_path / "openshell" / "gateway.toml"


def test_default_gateway_config_path_uses_home_config_without_xdg(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENSHELL_GATEWAY_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert (
        default_gateway_config_path()
        == tmp_path / ".config" / "openshell" / "gateway.toml"
    )


def test_default_gateway_config_path_honors_openshell_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured_path = tmp_path / "custom.toml"
    monkeypatch.setenv("OPENSHELL_GATEWAY_CONFIG", str(configured_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "ignored"))

    assert default_gateway_config_path() == configured_path


def test_middleware_name_validation_matches_openshell_constraints() -> None:
    longest_name = "a" * MAX_MIDDLEWARE_REGISTRATION_NAME_BYTES

    assert validate_middleware_name(longest_name) == longest_name

    for invalid_name in (
        "",
        "a" * (MAX_MIDDLEWARE_REGISTRATION_NAME_BYTES + 1),
        "privacy guard",
        "priväcy-guard",
        "openshell/privacy-guard",
    ):
        with pytest.raises(GatewayConfigError):
            validate_middleware_name(invalid_name)


def test_update_gateway_config_creates_minimal_default_config(
    tmp_path: Path,
) -> None:
    path = tmp_path / "openshell" / "gateway.toml"

    result = update_gateway_config(
        path,
        middleware_name="privacy-guard",
        host_ip="192.168.1.20",
        port=50051,
    )

    assert result is GatewayConfigUpdate.CREATED
    assert path.stat().st_mode & 0o777 == 0o600
    assert tomllib.loads(path.read_text()) == {
        "openshell": {
            "version": 1,
            "supervisor": {
                "middleware": [
                    {
                        "name": "privacy-guard",
                        "grpc_endpoint": "http://192.168.1.20:50051",
                        "max_body_bytes": 4_194_304,
                        "timeout": "5s",
                    }
                ]
            },
        }
    }


def test_update_gateway_config_appends_without_rewriting_existing_settings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.toml"
    original = (
        "# Keep this operator comment.\n"
        "[openshell]\n"
        "version = 1\n\n"
        "[openshell.gateway]\n"
        'compute_drivers = ["docker"]\n'
    )
    path.write_text(original)

    result = update_gateway_config(
        path,
        middleware_name="privacy-guard",
        host_ip="10.0.0.12",
        port=50052,
    )

    assert result is GatewayConfigUpdate.ADDED
    assert path.read_text().startswith(original.rstrip() + "\n\n")


def test_update_gateway_config_updates_only_the_named_registration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(
        "[openshell]\n"
        "version = 1\n\n"
        "[[openshell.supervisor.middleware]]\n"
        'name = "other-service"\n'
        'grpc_endpoint = "http://10.0.0.2:9000"\n'
        "max_body_bytes = 1000\n"
        'timeout = "1s"\n\n'
        "[[openshell.supervisor.middleware]]\n"
        'name = "privacy-guard"\n'
        "# Keep this registration comment.\n"
        'grpc_endpoint = "http://10.0.0.3:50051"\n'
        "max_body_bytes = 2048\n"
        'timeout = "2s"\n'
    )

    result = update_gateway_config(
        path,
        middleware_name="privacy-guard",
        host_ip="10.0.0.4",
        port=50053,
    )

    assert result is GatewayConfigUpdate.UPDATED
    contents = path.read_text()
    assert 'grpc_endpoint = "http://10.0.0.2:9000"' in contents
    assert "# Keep this registration comment." in contents
    assert 'grpc_endpoint = "http://10.0.0.4:50053"' in contents
    assert "max_body_bytes = 4194304" in contents
    assert 'timeout = "5s"' in contents

    repeated = update_gateway_config(
        path,
        middleware_name="privacy-guard",
        host_ip="10.0.0.4",
        port=50053,
    )

    assert repeated is GatewayConfigUpdate.UNCHANGED


@pytest.mark.parametrize(
    "contents",
    [
        "[openshell\n",
        "[other]\nversion = 1\n",
        "[openshell]\nversion = 2\n",
    ],
)
def test_update_gateway_config_rejects_invalid_existing_config(
    tmp_path: Path,
    contents: str,
) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(contents)

    with pytest.raises(GatewayConfigError):
        update_gateway_config(
            path,
            middleware_name="privacy-guard",
            host_ip="192.168.1.20",
            port=50051,
        )

    assert path.read_text() == contents


def test_remove_gateway_config_removes_only_the_named_registration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(
        "# Keep this operator comment.\n"
        "[openshell]\n"
        "version = 1\n\n"
        "[[openshell.supervisor.middleware]]\n"
        'name = "privacy-guard-regex"\n'
        'grpc_endpoint = "http://10.0.0.3:50051"\n'
        "max_body_bytes = 4194304\n"
        'timeout = "5s"\n\n'
        "# Keep this other-service comment.\n"
        "[[openshell.supervisor.middleware]]\n"
        'name = "other-service"\n'
        'grpc_endpoint = "http://10.0.0.2:9000"\n'
        "max_body_bytes = 1000\n"
        'timeout = "1s"\n'
    )

    result = remove_gateway_config(
        path,
        middleware_name="privacy-guard-regex",
    )

    assert result is GatewayConfigRemoval.REMOVED
    contents = path.read_text()
    assert "privacy-guard-regex" not in contents
    assert "# Keep this operator comment." in contents
    assert "# Keep this other-service comment." in contents
    assert 'name = "other-service"' in contents
    assert tomllib.loads(contents)["openshell"]["supervisor"]["middleware"] == [
        {
            "name": "other-service",
            "grpc_endpoint": "http://10.0.0.2:9000",
            "max_body_bytes": 1000,
            "timeout": "1s",
        }
    ]


@pytest.mark.parametrize("create_file", [False, True])
def test_remove_gateway_config_is_unchanged_when_registration_is_absent(
    tmp_path: Path,
    create_file: bool,
) -> None:
    path = tmp_path / "gateway.toml"
    if create_file:
        path.write_text("[openshell]\nversion = 1\n")

    result = remove_gateway_config(
        path,
        middleware_name="privacy-guard-regex",
    )

    assert result is GatewayConfigRemoval.UNCHANGED
    if create_file:
        assert path.read_text() == "[openshell]\nversion = 1\n"
    else:
        assert not path.exists()


def test_remove_gateway_config_rejects_duplicate_named_registrations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.toml"
    contents = (
        "[openshell]\n"
        "version = 1\n\n"
        "[[openshell.supervisor.middleware]]\n"
        'name = "privacy-guard-regex"\n\n'
        "[[openshell.supervisor.middleware]]\n"
        'name = "privacy-guard-regex"\n'
    )
    path.write_text(contents)

    with pytest.raises(GatewayConfigError, match="multiple middleware registrations"):
        remove_gateway_config(
            path,
            middleware_name="privacy-guard-regex",
        )

    assert path.read_text() == contents


def test_remove_gateway_config_rejects_registration_child_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.toml"
    contents = (
        "[openshell]\n"
        "version = 1\n\n"
        "[[openshell.supervisor.middleware]]\n"
        'name = "privacy-guard-regex"\n'
        'grpc_endpoint = "http://10.0.0.3:50051"\n\n'
        "[openshell.supervisor.middleware.metadata]\n"
        'owner = "privacy-team"\n'
    )
    path.write_text(contents)

    with pytest.raises(GatewayConfigError, match="Could not safely remove"):
        remove_gateway_config(
            path,
            middleware_name="privacy-guard-regex",
        )

    assert path.read_text() == contents


def test_remove_gateway_config_rejects_table_headers_inside_multiline_strings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.toml"
    contents = (
        "[openshell]\n"
        "version = 1\n"
        'note = """\n'
        "[[openshell.supervisor.middleware]]\n"
        "fake = true\n"
        "[[not.a.real.table]]\n"
        "still string\n"
        '"""\n\n'
        "[openshell.supervisor]\n"
        "middleware = [\n"
        '  { name = "privacy-guard-regex", '
        'grpc_endpoint = "http://10.0.0.3:50051" },\n'
        "]\n"
    )
    path.write_text(contents)

    with pytest.raises(GatewayConfigError, match="Could not safely remove"):
        remove_gateway_config(
            path,
            middleware_name="privacy-guard-regex",
        )

    assert path.read_text() == contents


def test_remove_gateway_config_reports_unsafe_multiline_registration_layout(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.toml"
    contents = (
        "[openshell]\n"
        "version = 1\n\n"
        "[[openshell.supervisor.middleware]]\n"
        'name = "privacy-guard-regex"\n'
        'description = """\n'
        "[looks.like.a.table]\n"
        "still string\n"
        '"""\n'
        'grpc_endpoint = "http://10.0.0.3:50051"\n'
    )
    path.write_text(contents)

    with pytest.raises(GatewayConfigError, match="Could not safely remove"):
        remove_gateway_config(
            path,
            middleware_name="privacy-guard-regex",
        )

    assert path.read_text() == contents
