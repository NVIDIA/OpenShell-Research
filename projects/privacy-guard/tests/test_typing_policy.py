"""AST-enforced type-safety policy for handwritten Python."""

from __future__ import annotations

import ast
from enum import Enum, auto
from pathlib import Path

from typing_extensions import override

_HANDWRITTEN_ROOTS = ("src", "tests", "examples")
_GENERATED_BINDINGS = Path("src/privacy_guard/bindings")
_TYPING_MODULES = frozenset({"typing", "typing_extensions"})


class _TypingOrigin(Enum):
    MODULE = auto()
    CAST = auto()
    DYNAMIC = auto()
    LITERAL = auto()
    ANNOTATED = auto()


class _TypingPolicyVisitor(ast.NodeVisitor):
    """Resolve prohibited typing symbols without matching unrelated names."""

    def __init__(self, relative_path: Path) -> None:
        self._relative_path = relative_path
        self._scopes: list[dict[str, _TypingOrigin | None]] = [{}]
        self.violations: list[str] = []

    def _record(self, node: ast.AST, message: str) -> None:
        line_number = (
            node.lineno if isinstance(node, ast.expr | ast.stmt | ast.arg) else 0
        )
        self.violations.append(
            f"{self._relative_path}:{line_number}: prohibited {message}"
        )

    def _lookup(self, name: str) -> _TypingOrigin | None:
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]
        return None

    def _expression_origin(self, node: ast.expr) -> _TypingOrigin | None:
        if isinstance(node, ast.Name):
            return self._lookup(node.id)
        if isinstance(node, ast.Attribute):
            owner_origin = self._expression_origin(node.value)
            if owner_origin is _TypingOrigin.MODULE:
                if node.attr == "cast":
                    return _TypingOrigin.CAST
                if node.attr == "Any":
                    return _TypingOrigin.DYNAMIC
                if node.attr == "Literal":
                    return _TypingOrigin.LITERAL
                if node.attr == "Annotated":
                    return _TypingOrigin.ANNOTATED
        return None

    def _bind(self, name: str, origin: _TypingOrigin | None) -> None:
        self._scopes[-1][name] = origin

    def _bind_target(
        self, target: ast.expr, origin: _TypingOrigin | None = None
    ) -> None:
        if isinstance(target, ast.Name):
            self._bind(target.id, origin)
        elif isinstance(target, ast.List | ast.Tuple):
            for element in target.elts:
                self._bind_target(element)
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value)

    def _visit_annotation(self, annotation: ast.expr | None) -> None:
        if annotation is None:
            return
        self._visit_type_expression(annotation)

    @staticmethod
    def _subscript_items(node: ast.expr) -> tuple[ast.expr, ...]:
        return tuple(node.elts) if isinstance(node, ast.Tuple) else (node,)

    def _visit_type_expression(self, node: ast.expr) -> None:
        if self._expression_origin(node) is _TypingOrigin.DYNAMIC:
            self._record(node, "explicit Any")
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                parsed = ast.parse(node.value, mode="eval")
            except SyntaxError:
                return
            if self._type_expression_contains_dynamic(parsed.body):
                self._record(node, "explicit Any annotation")
            return
        if isinstance(node, ast.Subscript):
            self.visit(node.value)
            items = self._subscript_items(node.slice)
            origin = self._expression_origin(node.value)
            if origin is _TypingOrigin.LITERAL:
                for item in items:
                    self.visit(item)
                return
            if origin is _TypingOrigin.ANNOTATED and items:
                self._visit_type_expression(items[0])
                for item in items[1:]:
                    self.visit(item)
                return
            for item in items:
                self._visit_type_expression(item)
            return
        if isinstance(node, ast.BinOp):
            self._visit_type_expression(node.left)
            self._visit_type_expression(node.right)
            return
        if isinstance(node, ast.List | ast.Tuple):
            for item in node.elts:
                self._visit_type_expression(item)
            return
        if isinstance(node, ast.Starred):
            self._visit_type_expression(node.value)
            return
        self.visit(node)

    def _inspect_type_comment(
        self,
        node: ast.stmt | ast.arg,
        type_comment: str | None,
        *,
        function: bool = False,
    ) -> None:
        if type_comment is None:
            return
        try:
            parsed = ast.parse(type_comment, mode="func_type" if function else "eval")
        except SyntaxError:
            return
        if self._type_expression_contains_dynamic(parsed):
            self._record(node, "explicit Any type comment")

    def _value_contains_dynamic(self, node: ast.AST) -> bool:
        if (
            isinstance(node, ast.expr)
            and self._expression_origin(node) is _TypingOrigin.DYNAMIC
        ):
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return False
        return any(
            self._value_contains_dynamic(child) for child in ast.iter_child_nodes(node)
        )

    def _type_expression_contains_dynamic(self, node: ast.AST, depth: int = 0) -> bool:
        if depth > 8:
            return False
        if (
            isinstance(node, ast.expr)
            and self._expression_origin(node) is _TypingOrigin.DYNAMIC
        ):
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                parsed = ast.parse(node.value, mode="eval")
            except SyntaxError:
                return False
            return self._type_expression_contains_dynamic(parsed.body, depth + 1)
        if isinstance(node, ast.Subscript):
            items = self._subscript_items(node.slice)
            origin = self._expression_origin(node.value)
            if origin is _TypingOrigin.LITERAL:
                return any(self._value_contains_dynamic(item) for item in items)
            if origin is _TypingOrigin.ANNOTATED and items:
                return self._type_expression_contains_dynamic(items[0], depth) or any(
                    self._value_contains_dynamic(item) for item in items[1:]
                )
        return any(
            self._type_expression_contains_dynamic(child, depth)
            for child in ast.iter_child_nodes(node)
        )

    def _visit_arguments(self, arguments: ast.arguments) -> None:
        all_arguments = (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        )
        for argument in all_arguments:
            self._visit_annotation(argument.annotation)
            self._inspect_type_comment(argument, argument.type_comment)
        if arguments.vararg is not None:
            self._visit_annotation(arguments.vararg.annotation)
            self._inspect_type_comment(arguments.vararg, arguments.vararg.type_comment)
        if arguments.kwarg is not None:
            self._visit_annotation(arguments.kwarg.annotation)
            self._inspect_type_comment(arguments.kwarg, arguments.kwarg.type_comment)
        for default in arguments.defaults:
            self.visit(default)
        for default in arguments.kw_defaults:
            if default is not None:
                self.visit(default)

    def _bind_arguments(self, arguments: ast.arguments) -> None:
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        ):
            self._bind(argument.arg, None)
        if arguments.vararg is not None:
            self._bind(arguments.vararg.arg, None)
        if arguments.kwarg is not None:
            self._bind(arguments.kwarg.arg, None)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_arguments(node.args)
        self._visit_annotation(node.returns)
        self._inspect_type_comment(node, node.type_comment, function=True)
        self._bind(node.name, None)
        self._scopes.append({})
        try:
            self._bind_arguments(node.args)
            for statement in node.body:
                self.visit(statement)
        finally:
            self._scopes.pop()

    @override
    def visit_Import(self, node: ast.Import) -> None:
        for imported in node.names:
            bound_name = imported.asname or imported.name.split(".", maxsplit=1)[0]
            origin = _TypingOrigin.MODULE if imported.name in _TYPING_MODULES else None
            self._bind(bound_name, origin)

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        is_typing_import = node.level == 0 and node.module in _TYPING_MODULES
        for imported in node.names:
            if imported.name == "*":
                if is_typing_import:
                    self._record(node, "typing wildcard import")
                continue
            bound_name = imported.asname or imported.name
            origin: _TypingOrigin | None = None
            if is_typing_import and imported.name == "cast":
                origin = _TypingOrigin.CAST
                self._record(node, "typing cast import")
            elif is_typing_import and imported.name == "Any":
                origin = _TypingOrigin.DYNAMIC
                self._record(node, "explicit Any import")
            elif is_typing_import and imported.name == "Literal":
                origin = _TypingOrigin.LITERAL
            elif is_typing_import and imported.name == "Annotated":
                origin = _TypingOrigin.ANNOTATED
            self._bind(bound_name, origin)

    @override
    def visit_Assign(self, node: ast.Assign) -> None:
        self._inspect_type_comment(node, node.type_comment)
        self.visit(node.value)
        origin = self._expression_origin(node.value)
        for target in node.targets:
            self._bind_target(target, origin)

    @override
    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._visit_annotation(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            origin = self._expression_origin(node.value)
        else:
            origin = None
        self._bind_target(node.target, origin)

    @override
    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_target(node.target, self._expression_origin(node.value))

    def _visit_for(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._bind_target(node.target)
        self._inspect_type_comment(node, node.type_comment)
        for statement in (*node.body, *node.orelse):
            self.visit(statement)

    @override
    def visit_For(self, node: ast.For) -> None:
        self._visit_for(node)

    @override
    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_for(node)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars)
        self._inspect_type_comment(node, node.type_comment)
        for statement in node.body:
            self.visit(statement)

    @override
    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    @override
    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    @override
    def visit_Call(self, node: ast.Call) -> None:
        if self._expression_origin(node.func) is _TypingOrigin.CAST:
            self._record(node, "typing cast call")
        else:
            self.visit(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)

    @override
    def visit_Name(self, node: ast.Name) -> None:
        origin = self._lookup(node.id)
        if origin is _TypingOrigin.CAST:
            self._record(node, "typing cast reference")
        elif origin is _TypingOrigin.DYNAMIC:
            self._record(node, "explicit Any")

    @override
    def visit_Attribute(self, node: ast.Attribute) -> None:
        origin = self._expression_origin(node)
        if origin is _TypingOrigin.CAST:
            self._record(node, "typing cast reference")
        elif origin is _TypingOrigin.DYNAMIC:
            self._record(node, "explicit Any")
        else:
            self.visit(node.value)

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    @override
    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_arguments(node.args)
        self._scopes.append({})
        try:
            self._bind_arguments(node.args)
            self.visit(node.body)
        finally:
            self._scopes.pop()

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        self._bind(node.name, None)
        self._scopes.append({})
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self._scopes.pop()


def _handwritten_python_files(project_root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for source_root_name in _HANDWRITTEN_ROOTS:
        source_root = project_root / source_root_name
        if not source_root.is_dir():
            continue
        for path in source_root.rglob("*"):
            if path.suffix not in {".py", ".pyi"}:
                continue
            relative_path = path.relative_to(project_root)
            if relative_path.is_relative_to(_GENERATED_BINDINGS):
                continue
            files.append(path)
    return tuple(sorted(files))


def _typing_policy_violations(project_root: Path) -> tuple[str, ...]:
    violations: list[str] = []
    for path in _handwritten_python_files(project_root):
        tree = ast.parse(
            path.read_text(encoding="utf-8"), filename=str(path), type_comments=True
        )
        visitor = _TypingPolicyVisitor(path.relative_to(project_root))
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return tuple(violations)


def test_handwritten_python_is_cast_free_and_has_no_explicit_any() -> None:
    project_root = Path(__file__).resolve().parents[1]

    assert _typing_policy_violations(project_root) == ()


def test_typing_policy_rejects_all_supported_typing_origins(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "bad.py").write_text(
        """
import typing
import typing as t
import typing_extensions as extensions
from typing import Annotated as TypeAnnotated
from typing import Any as DynamicType
from typing_extensions import Annotated as ExtensionAnnotated
from typing_extensions import cast as narrow

direct_dynamic: DynamicType
qualified_dynamic: t.Any
quoted_dynamic: "typing.Any"
extension_quoted: "extensions.Any"
nested_quoted: list["typing.Any"]
annotated_nested: t.Annotated["typing.Any", "metadata"]
annotated_alias_nested: TypeAnnotated["typing.Any", "metadata"]
annotated_extension_nested: extensions.Annotated["extensions.Any", "metadata"]
quoted_annotated_nested: "typing.Annotated['typing.Any', 'metadata']"
quoted_annotated_alias_nested: "TypeAnnotated['typing.Any', 'metadata']"
quoted_extension_annotated_alias_nested: (
    "ExtensionAnnotated['extensions.Any', 'metadata']"
)
literal_actual_dynamic: typing.Literal[typing.Any]
annotated_metadata_actual_dynamic: typing.Annotated[str, typing.Any]
direct_cast = narrow(str, object())
qualified_cast = extensions.cast(str, object())
rebound = t.cast
rebound_cast = rebound(str, object())
""",
        encoding="utf-8",
    )
    (source_root / "bad_argument_comments.py").write_text(
        """
import typing

def parameters(
    positional_only,  # type: typing.Any
    /,
    positional,  # type: typing.Any
    *variadic,  # type: typing.Any
    keyword_only,  # type: typing.Any
    **keywords,  # type: typing.Any
):
    return positional_only

async def async_parameter(
    value,  # type: typing.Any
):
    return value
""",
        encoding="utf-8",
    )
    (source_root / "bad_type_comments.py").write_text(
        """
import typing

assigned = None  # type: typing.Any

for item in ():  # type: typing.Any
    pass

with open(__file__) as stream:  # type: typing.Any
    pass

def commented(value):
    # type: (typing.Any) -> typing.Any
    return value
""",
        encoding="utf-8",
    )

    violations = _typing_policy_violations(tmp_path)
    regular_violations = tuple(
        violation for violation in violations if "src/bad.py:" in violation
    )
    type_comment_violations = tuple(
        violation
        for violation in violations
        if "src/bad_type_comments.py:" in violation
    )
    argument_comment_violations = tuple(
        violation
        for violation in violations
        if "src/bad_argument_comments.py:" in violation
    )

    assert any("explicit Any import" in violation for violation in regular_violations)
    assert sum("explicit Any" in violation for violation in regular_violations) == 14
    assert any("typing cast import" in violation for violation in regular_violations)
    assert sum("typing cast call" in violation for violation in regular_violations) == 3
    assert any("typing cast reference" in violation for violation in regular_violations)
    assert len(type_comment_violations) == 4
    assert all(
        "explicit Any type comment" in violation
        for violation in type_comment_violations
    )
    assert len(argument_comment_violations) == 6
    assert all(
        "explicit Any type comment" in violation
        for violation in argument_comment_violations
    )


def test_typing_policy_allows_unrelated_cast_and_any_names(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "allowed.py").write_text(
        """
import typing
import typing_extensions as extensions
from typing import Annotated as TypeAnnotated
from typing import Literal as TypeLiteral
from typing_extensions import Annotated as ExtensionAnnotated
from typing_extensions import Literal as ExtensionLiteral


class Domain:
    Any = "ordinary domain value"


class Converter:
    def cast(self, value: object) -> object:
        return value


def cast(value: object) -> object:
    return value


method_result = Converter().cast(Domain.Any)
function_result = cast(object())
ordinary_string = "typing.Any"
ordinary_comment = None  # type: object
literal_module: typing.Literal["typing.Any"]
literal_alias: TypeLiteral["typing.Any"]
literal_extension: extensions.Literal["extensions.Any"]
literal_extension_alias: ExtensionLiteral["extensions.Any"]
annotated_module: typing.Annotated[str, "typing.Any"]
annotated_alias: TypeAnnotated[str, "typing.Any"]
annotated_extension: extensions.Annotated[str, "extensions.Any"]
annotated_extension_alias: ExtensionAnnotated[str, "extensions.Any"]
quoted_literal: "typing.Literal['typing.Any']"
quoted_annotated: "extensions.Annotated[str, 'extensions.Any']"
quoted_literal_alias: "TypeLiteral['typing.Any']"
quoted_extension_literal_alias: "ExtensionLiteral['extensions.Any']"
quoted_annotated_alias: "TypeAnnotated[str, 'typing.Any']"
quoted_extension_annotated_alias: "ExtensionAnnotated[str, 'extensions.Any']"
""",
        encoding="utf-8",
    )

    assert _typing_policy_violations(tmp_path) == ()


def test_typing_policy_excludes_only_generated_bindings(tmp_path: Path) -> None:
    generated = tmp_path / _GENERATED_BINDINGS
    generated.mkdir(parents=True)
    (generated / "generated.py").write_text(
        "from typing import Any, cast\nvalue: Any = cast(Any, None)\n",
        encoding="utf-8",
    )
    handwritten = tmp_path / "src/privacy_guard/generated_elsewhere.py"
    handwritten.write_text("from typing import Any\nvalue: Any\n", encoding="utf-8")

    violations = _typing_policy_violations(tmp_path)

    assert violations
    assert all("generated_elsewhere.py" in violation for violation in violations)
