"""Bounded YAML parsing, source locations, annotations, and permission grouping."""

from dataclasses import dataclass
from io import StringIO
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError

from policy_review_mcp.contracts import FieldAnnotation

MAX_POLICY_BYTES = 1024 * 1024
MAX_YAML_DEPTH = 64
MAX_YAML_NODES = 16_384


class PolicyInputError(ValueError):
    """A policy or annotation is invalid for review."""


@dataclass(frozen=True)
class SourceLocation:
    pointer: str
    line: int
    column: int
    source: str = "candidate"

    def as_dict(self) -> dict[str, Any]:
        return {
            "pointer": self.pointer,
            "line": self.line,
            "column": self.column,
            "source": self.source,
        }


@dataclass(frozen=True)
class PermissionGroup:
    id: str
    kind: str
    summary: str
    pointers: tuple[str, ...]
    locations: tuple[SourceLocation, ...]
    state: dict[str, Any]


@dataclass(frozen=True)
class ParsedPolicy:
    data: dict[str, Any]
    locations: dict[str, SourceLocation]
    groups: tuple[PermissionGroup, ...]
    inventory: tuple[str, ...]
    unassessed: tuple[dict[str, Any], ...]


def parse_policy(source: str, *, source_name: str = "candidate") -> ParsedPolicy:
    if len(source.encode("utf-8")) > MAX_POLICY_BYTES:
        raise PolicyInputError(f"{source_name} policy exceeds the {MAX_POLICY_BYTES}-byte limit")
    yaml = YAML(typ="rt")
    yaml.allow_duplicate_keys = False
    try:
        document = yaml.load(StringIO(source))
    except DuplicateKeyError as error:
        raise PolicyInputError(f"duplicate YAML key: {error.problem}") from error
    except Exception as error:
        raise PolicyInputError(f"invalid YAML: {error}") from error
    if not isinstance(document, dict):
        raise PolicyInputError("policy must be a YAML mapping")
    if document.get("version") != 1:
        raise PolicyInputError("policy version must be 1")

    locations: dict[str, SourceLocation] = {
        "": SourceLocation(pointer="", line=1, column=1, source=source_name)
    }
    _collect_locations(
        document,
        "",
        locations,
        source_name,
        depth=0,
        seen_containers={},
        node_count=[1],
    )
    inventory = tuple(pointer for pointer in locations if pointer)
    groups, unassessed = _permission_groups(document, locations)
    return ParsedPolicy(
        data=document,
        locations=locations,
        groups=tuple(groups),
        inventory=inventory,
        unassessed=tuple(unassessed),
    )


def validate_annotations(
    annotations: list[FieldAnnotation],
    candidate: ParsedPolicy,
    starting: ParsedPolicy | None,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for annotation in annotations:
        if annotation.pointer in seen:
            raise PolicyInputError(f"duplicate annotation pointer: {annotation.pointer}")
        seen.add(annotation.pointer)
        current_exists, current = resolve_pointer(candidate.data, annotation.pointer)
        previous_exists, previous = (
            resolve_pointer(starting.data, annotation.pointer) if starting else (False, None)
        )
        if annotation.change == "removed":
            if starting is None:
                raise PolicyInputError("removed annotations require starting_policy")
            if not previous_exists or current_exists:
                raise PolicyInputError(
                    f"removed pointer must exist only in starting_policy: {annotation.pointer}"
                )
            derived = "removed"
            location = starting.locations.get(annotation.pointer)
        else:
            if not current_exists:
                raise PolicyInputError(f"annotation pointer not found: {annotation.pointer}")
            location = candidate.locations.get(annotation.pointer)
            derived = annotation.change
            if starting is not None:
                derived = (
                    "new" if not previous_exists else "fixed" if current == previous else "updated"
                )
                if annotation.change is not None and annotation.change != derived:
                    raise PolicyInputError(
                        f"annotation {annotation.pointer} says {annotation.change}, "
                        f"derived {derived}"
                    )
        validated.append(
            {
                **annotation.model_dump(),
                "change": derived,
                "previous_value": previous if starting is not None else None,
                "current_value": current if current_exists else None,
                "location": location.as_dict() if location else None,
            }
        )
    return validated


def resolve_pointer(root: Any, pointer: str) -> tuple[bool, Any]:
    if pointer == "":
        return True, root
    if not pointer.startswith("/"):
        return False, None
    value = root
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isdigit() and int(token) < len(value):
            value = value[int(token)]
        else:
            return False, None
    return True, value


def _escape(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _collect_locations(
    node: Any,
    pointer: str,
    output: dict[str, SourceLocation],
    source_name: str,
    *,
    depth: int,
    seen_containers: dict[int, str],
    node_count: list[int],
) -> None:
    if depth > MAX_YAML_DEPTH:
        raise PolicyInputError(f"policy nesting exceeds the depth limit of {MAX_YAML_DEPTH}")
    if isinstance(node, (dict, list)):
        identity = id(node)
        if identity in seen_containers:
            raise PolicyInputError(
                f"YAML aliases are unsupported: {pointer or '/'} reuses "
                f"{seen_containers[identity] or '/'}"
            )
        seen_containers[identity] = pointer
    if isinstance(node, dict):
        for key, value in node.items():
            node_count[0] += 1
            if node_count[0] > MAX_YAML_NODES:
                raise PolicyInputError(f"policy exceeds the node limit of {MAX_YAML_NODES}")
            child = f"{pointer}/{_escape(key)}"
            try:
                line, column = node.lc.key(key)
            except (AttributeError, KeyError, TypeError):
                line, column = 0, 0
            output[child] = SourceLocation(child, line + 1, column + 1, source_name)
            _collect_locations(
                value,
                child,
                output,
                source_name,
                depth=depth + 1,
                seen_containers=seen_containers,
                node_count=node_count,
            )
    elif isinstance(node, list):
        for index, value in enumerate(node):
            node_count[0] += 1
            if node_count[0] > MAX_YAML_NODES:
                raise PolicyInputError(f"policy exceeds the node limit of {MAX_YAML_NODES}")
            child = f"{pointer}/{index}"
            try:
                line, column = node.lc.item(index)
            except (AttributeError, KeyError, TypeError):
                line, column = 0, 0
            output[child] = SourceLocation(child, line + 1, column + 1, source_name)
            _collect_locations(
                value,
                child,
                output,
                source_name,
                depth=depth + 1,
                seen_containers=seen_containers,
                node_count=node_count,
            )


def _permission_groups(
    data: dict[str, Any], locations: dict[str, SourceLocation]
) -> tuple[list[PermissionGroup], list[dict[str, Any]]]:
    groups: list[PermissionGroup] = []
    unassessed: list[dict[str, Any]] = []
    filesystem = data.get("filesystem_policy", {})
    if isinstance(filesystem, dict):
        unknown_filesystem_fields = set(filesystem) - {
            "read_only",
            "read_write",
            "include_workdir",
        }
        if unknown_filesystem_fields:
            for key in sorted(unknown_filesystem_fields):
                unassessed.append(
                    {
                        "pointer": f"/filesystem_policy/{_escape(key)}",
                        "reason": "unsupported_nested_field",
                    }
                )
            unassessed.append(
                {
                    "pointer": "/filesystem_policy",
                    "reason": "unsupported_field_affects_filesystem_groups",
                }
            )
        else:
            for access_key in ("read_only", "read_write"):
                entries = filesystem.get(access_key, [])
                if not isinstance(entries, list):
                    unassessed.append(
                        {
                            "pointer": f"/filesystem_policy/{access_key}",
                            "reason": "unsupported_shape",
                        }
                    )
                    continue
                for index, path in enumerate(entries):
                    pointer = f"/filesystem_policy/{access_key}/{index}"
                    if not isinstance(path, str):
                        unassessed.append({"pointer": pointer, "reason": "unsupported_shape"})
                        continue
                    mode = "read" if access_key == "read_only" else "read/write"
                    groups.append(
                        PermissionGroup(
                            id=f"filesystem.{access_key}.{index}",
                            kind="filesystem",
                            summary=f"{mode} access to {path}",
                            pointers=(pointer,),
                            locations=(locations[pointer],),
                            state={"mode": access_key, "path": path},
                        )
                    )
        if "include_workdir" in filesystem:
            unassessed.append(
                {"pointer": "/filesystem_policy/include_workdir", "reason": "runtime_semantics"}
            )

    elif "filesystem_policy" in data:
        unassessed.append({"pointer": "/filesystem_policy", "reason": "unsupported_shape"})

    network = data.get("network_policies", {})
    if isinstance(network, dict):
        for name, rule in network.items():
            base = f"/network_policies/{_escape(name)}"
            if not isinstance(rule, dict):
                unassessed.append({"pointer": base, "reason": "unsupported_shape"})
                continue
            unknown_rule_fields = set(rule) - {"name", "endpoints", "binaries"}
            if unknown_rule_fields:
                unassessed.append(
                    {"pointer": base, "reason": "unsupported_field_affects_network_group"}
                )
                continue
            binaries = rule.get("binaries", [])
            binary_paths = []
            invalid_binary_selector = False
            if isinstance(binaries, list):
                for binary in binaries:
                    if (
                        isinstance(binary, dict)
                        and set(binary) == {"path"}
                        and isinstance(binary.get("path"), str)
                    ):
                        binary_paths.append(binary["path"])
                    else:
                        invalid_binary_selector = True
            else:
                invalid_binary_selector = True
            endpoints = rule.get("endpoints", [])
            if not isinstance(endpoints, list):
                unassessed.append({"pointer": f"{base}/endpoints", "reason": "unsupported_shape"})
                continue
            for index, endpoint in enumerate(endpoints):
                pointer = f"{base}/endpoints/{index}"
                reason = (
                    "unsupported_binary_selector"
                    if invalid_binary_selector
                    else _unsupported_github_endpoint(endpoint, binary_paths)
                )
                if reason:
                    unassessed.append({"pointer": pointer, "reason": reason})
                    continue
                selectors = [
                    {
                        "method": item["allow"]["method"].upper(),
                        "path": item["allow"]["path"],
                    }
                    for item in endpoint["rules"]
                ]
                related = [pointer]
                if binaries:
                    related.append(f"{base}/binaries")
                group_locations = tuple(locations[p] for p in related if p in locations)
                rendered = ", ".join(f"{item['method']} {item['path']}" for item in selectors)
                groups.append(
                    PermissionGroup(
                        id=f"network.{name}.{index}",
                        kind="github_rest",
                        summary=f"GitHub REST via {binary_paths}: {rendered}",
                        pointers=tuple(related),
                        locations=group_locations,
                        state={
                            "host": endpoint["host"],
                            "port": endpoint.get("port", 443),
                            "protocol": "rest",
                            "enforcement": "enforce",
                            "binaries": binary_paths,
                            "selectors": selectors,
                        },
                    )
                )
    elif "network_policies" in data:
        unassessed.append({"pointer": "/network_policies", "reason": "unsupported_shape"})

    for key in data:
        if key not in {"version", "filesystem_policy", "network_policies"}:
            unassessed.append(
                {"pointer": f"/{_escape(key)}", "reason": "unsupported_policy_family"}
            )
    return groups, unassessed


def _unsupported_github_endpoint(endpoint: Any, binaries: list[str]) -> str | None:
    if not isinstance(endpoint, dict):
        return "unsupported_shape"
    if endpoint.get("host") != "api.github.com":
        return "unsupported_network_family"
    if endpoint.get("protocol") != "rest" or endpoint.get("enforcement") != "enforce":
        return "unsupported_selector_semantics"
    if not binaries:
        return "missing_binary_selector"
    rules = endpoint.get("rules")
    if not isinstance(rules, list) or not rules:
        return "unsupported_selector_shape"
    for rule in rules:
        allow = rule.get("allow") if isinstance(rule, dict) else None
        if (
            not isinstance(allow, dict)
            or not isinstance(allow.get("method"), str)
            or not isinstance(allow.get("path"), str)
            or set(allow) != {"method", "path"}
        ):
            return "unsupported_selector_shape"
    if endpoint.get("deny_rules") or endpoint.get("access"):
        return "unsupported_selector_interaction"
    known = {"host", "port", "protocol", "enforcement", "rules"}
    if set(endpoint) - known:
        return "unsupported_selector_interaction"
    return None
