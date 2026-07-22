from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from time import monotonic

import pytest
from typing_extensions import override

import privacy_guard.scanners.regex as regex_module
from privacy_guard.errors import ErrorCode, ErrorKind, PrivacyGuardError
from privacy_guard.scanners import RegexScanner, ScanBudget, ScanBudgetExceeded


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _single(patterns: str) -> str:
    return f"""
- name: token
  patterns:
{patterns}
"""


def test_single_profile_reports_overlaps_names_confidence_and_unicode(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path / "entities.yaml",
        _single(
            """    - name: whole-token
      regex: 'aba'
      confidence: high
    - name: suffix-token
      regex: 'ba'
      confidence: medium
"""
        ),
    )

    findings = RegexScanner.from_yaml(path).scan("🐍aba")

    assert [
        (
            item.metadata["pattern_name"] if item.metadata is not None else None,
            item.start_offset,
            item.end_offset,
            item.confidence.value,
        )
        for item in findings
    ] == [("whole-token", 1, 4, "high"), ("suffix-token", 2, 4, "medium")]


def test_one_pattern_uses_overlapping_iteration(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "overlap.yaml",
        _single("    - name: pair\n      regex: 'aa'\n      confidence: high\n"),
    )

    findings = RegexScanner.from_yaml(path).scan("aaa")

    assert [(item.start_offset, item.end_offset) for item in findings] == [
        (0, 2),
        (1, 3),
    ]


def test_hyphen_normalization_coexists_with_underscore_name(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "names.yaml",
        _single(
            """    - name: same-name
      regex: 'x'
      confidence: high
    - name: same_name
      regex: 'y'
      confidence: high
"""
        ),
    )

    findings = RegexScanner.from_yaml(path).scan("xy")

    assert [
        item.metadata["pattern_name"] if item.metadata is not None else None
        for item in findings
    ] == ["same-name", "same_name"]


def test_numeric_backreferences_keep_their_group_numbers(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "backref.yaml",
        _single(
            "    - name: repeated\n      regex: '(a)\\1'\n      confidence: high\n"
        ),
    )

    finding = RegexScanner.from_yaml(path).scan("aa")[0]
    assert finding.metadata is not None
    assert finding.metadata["pattern_name"] == "repeated"


def test_profiles_require_selection_and_do_not_merge(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "profiles.yaml",
        """
profiles:
  first:
    - name: alpha
      patterns:
        - name: alpha
          regex: 'a'
          confidence: high
  second:
    - name: beta
      patterns:
        - name: beta
          regex: 'b'
          confidence: high
""",
    )

    with pytest.raises(PrivacyGuardError) as missing:
        RegexScanner.from_yaml(path)
    assert missing.value.code is ErrorCode.SCANNER_CONFIG_INVALID

    scanner = RegexScanner.from_yaml(path, "second")
    assert scanner.supported_entity_types == frozenset({"beta"})
    assert [item.entity for item in scanner.scan("ab")] == ["beta"]


@pytest.mark.parametrize(
    "document",
    [
        "- name: x\n  name: y\n  patterns: []\n",
        "- &entity\n  name: x\n  patterns: []\n",
        "- !unsafe {name: x, patterns: []}\n",
        "- name: x\n  patterns:\n"
        "    [{name: p, regex: 'x', confidence: high, extra: true}]\n",
        "- name: x\n  patterns: [{name: p, regex: '(?P<n>x)', confidence: high}]\n",
        "- name: x\n  patterns: [{name: p, regex: '(?i:x)', confidence: high}]\n",
        "- name: x\n  patterns: [{name: p, regex: 'x*', confidence: high}]\n",
    ],
)
def test_invalid_yaml_and_patterns_use_one_content_safe_error(
    tmp_path: Path, document: str
) -> None:
    path = _write(tmp_path / "sensitive-name-8472.yaml", document)

    with pytest.raises(PrivacyGuardError) as exception_info:
        RegexScanner.from_yaml(path)

    assert exception_info.value.code is ErrorCode.SCANNER_CONFIG_INVALID
    assert exception_info.value.kind is ErrorKind.INVALID_INPUT
    assert "8472" not in str(exception_info.value)


def test_configuration_read_is_bounded_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingBytesIO(BytesIO):
        def __init__(self) -> None:
            super().__init__(b"x" * 100)
            self.read_sizes: list[int | None] = []

        @override
        def read(self, size: int | None = -1, /) -> bytes:
            self.read_sizes.append(size)
            return super().read(size)

    stream = RecordingBytesIO()
    monkeypatch.setattr(regex_module, "MAX_SCANNER_CONFIG_BYTES", 64)
    monkeypatch.setattr(Path, "open", lambda path, mode: stream)

    with pytest.raises(PrivacyGuardError) as exception_info:
        RegexScanner.from_yaml("ignored.yaml")

    assert exception_info.value.code is ErrorCode.SCANNER_CONFIG_INVALID
    assert stream.read_sizes == [65]


def test_inactive_profiles_are_fully_validated(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "profiles.yaml",
        """
profiles:
  selected:
    - name: alpha
      patterns: [{name: alpha, regex: 'a', confidence: high}]
  broken:
    - name: beta
      patterns: [{name: beta, regex: '(?P<reserved>b)', confidence: high}]
""",
    )

    with pytest.raises(PrivacyGuardError) as exception_info:
        RegexScanner.from_yaml(path, "selected")

    assert exception_info.value.code is ErrorCode.SCANNER_CONFIG_INVALID
    assert exception_info.value.kind is ErrorKind.INVALID_INPUT


def test_excessive_yaml_nesting_is_rejected_safely(tmp_path: Path) -> None:
    document = "value"
    for _ in range(20):
        document = f"[{document}]"
    path = _write(tmp_path / "nested.yaml", document)

    with pytest.raises(PrivacyGuardError) as exception_info:
        RegexScanner.from_yaml(path)

    assert exception_info.value.code is ErrorCode.SCANNER_CONFIG_INVALID
    assert exception_info.value.kind is ErrorKind.INVALID_INPUT


def test_deeply_nested_regex_is_a_content_safe_configuration_error(
    tmp_path: Path,
) -> None:
    nested_expression = "(" * 1_000 + "x" + ")" * 1_000
    path = _write(
        tmp_path / "deep-regex.yaml",
        _single(
            "    - name: nested\n"
            f"      regex: '{nested_expression}'\n"
            "      confidence: high\n"
        ),
    )

    with pytest.raises(PrivacyGuardError) as exception_info:
        RegexScanner.from_yaml(path)

    assert exception_info.value.code is ErrorCode.SCANNER_CONFIG_INVALID
    assert exception_info.value.kind is ErrorKind.INVALID_INPUT


def test_contextual_zero_width_is_a_runtime_configuration_failure(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path / "contextual.yaml",
        _single("    - name: zero\n      regex: '(?=a)'\n      confidence: high\n"),
    )
    scanner = RegexScanner.from_yaml(path)

    with pytest.raises(PrivacyGuardError) as exception_info:
        scanner.scan("a")

    assert exception_info.value.code is ErrorCode.SCANNER_CONFIG_INVALID
    assert exception_info.value.kind is ErrorKind.INTERNAL


def test_standalone_scan_exposes_typed_budget_exhaustion(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "budget.yaml",
        _single("    - name: literal\n      regex: 'x'\n      confidence: high\n"),
    )
    scanner = RegexScanner.from_yaml(path)

    with pytest.raises(ScanBudgetExceeded):
        scanner.scan("x", budget=ScanBudget(deadline=monotonic() - 1))


def test_scanner_instance_supports_concurrent_calls(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "concurrent.yaml",
        _single("    - name: literal\n      regex: 'x'\n      confidence: high\n"),
    )
    scanner = RegexScanner.from_yaml(path)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(executor.map(scanner.scan, ("x",) * 16))

    assert all(len(findings) == 1 for findings in results)
