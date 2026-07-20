from __future__ import annotations
import abc
import json
import asyncio
import inspect
import logging
import importlib
from typing import TYPE_CHECKING, Any, Dict, List
from pathlib import Path
from dataclasses import dataclass

from reachy_mini_conversation_app.config import LOCKED_PROFILE, DEFAULT_PROFILES_DIRECTORY
from reachy_mini_conversation_app.tools.tool_constants import SystemTool


if TYPE_CHECKING:
    from reachy_mini_conversation_app.tools.background_tool_manager import BackgroundToolManager


logger = logging.getLogger(__name__)


DEFAULT_PROFILES_MODULE = "reachy_mini_conversation_app.profiles"


if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s:%(lineno)d | %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


ALL_TOOLS: Dict[str, "Tool"] = {}
ALL_TOOL_SPECS: List[Dict[str, Any]] = []
_TOOLS_INITIALIZED = False


def get_concrete_subclasses(base: type[Tool]) -> List[type[Tool]]:
    """Recursively find all concrete (non-abstract) subclasses of a base class."""
    result: List[type[Tool]] = []
    for cls in base.__subclasses__():
        if not inspect.isabstract(cls):
            result.append(cls)
        result.extend(get_concrete_subclasses(cls))
    return result


@dataclass
class ToolDependencies:
    """External dependencies injected into tools."""

    reachy_mini: Any | None = None
    movement_manager: Any | None = None
    camera_worker: Any | None = None
    vision_manager: Any | None = None
    vision_router: Any | None = None
    head_wobbler: Any | None = None
    motion_duration_s: float = 1.0
    capture_directory: Path | None = None

    def require_reachy_mini(self) -> Any:
        """Return the local robot or fail clearly in a hardware-free process."""
        if self.reachy_mini is None:
            raise RuntimeError("This tool requires a local ReachyMini connection")
        return self.reachy_mini

    def require_movement_manager(self) -> Any:
        """Return the local movement manager or fail clearly in remote mode."""
        if self.movement_manager is None:
            raise RuntimeError("This tool requires a local movement manager")
        return self.movement_manager


class Tool(abc.ABC):
    """Base abstraction for tools used in function-calling."""

    name: str
    description: str
    parameters_schema: Dict[str, Any]

    def spec(self) -> Dict[str, Any]:
        """Return the function spec for LLM consumption."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
        }

    @abc.abstractmethod
    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Async tool execution entrypoint."""
        raise NotImplementedError


def _try_import_tool(module_path: str) -> bool:
    """Import a tool module, returning False only when that module does not exist."""
    try:
        importlib.import_module(module_path)
        return True
    except ModuleNotFoundError as exc:
        if exc.name != module_path:
            raise
        return False


def _format_error(error: Exception) -> str:
    """Format an exception for logging."""
    if isinstance(error, ModuleNotFoundError):
        return f"Missing dependency: {error}"
    if isinstance(error, ImportError):
        return f"Import error: {error}"
    return f"{type(error).__name__}: {error}"


def _load_profile_tools() -> None:
    """Load tools from the locked profile's tools.txt file."""
    profile = LOCKED_PROFILE
    logger.info("Loading tools for locked profile: %s", profile)

    profile_module_path = DEFAULT_PROFILES_DIRECTORY / profile
    tools_txt_path = profile_module_path / "tools.txt"

    if not tools_txt_path.exists():
        raise RuntimeError(f"tools.txt not found at {tools_txt_path}")

    try:
        lines = tools_txt_path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        raise RuntimeError(f"Failed to read tools.txt: {e}") from e

    tool_names = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tool_names.append(line)

    tool_names.extend({tool.value for tool in SystemTool})

    logger.info("Found %d tools to load: %s", len(tool_names), tool_names)

    for tool_name in tool_names:
        profile_import_path = f"{DEFAULT_PROFILES_MODULE}.{profile}.{tool_name}"
        shared_module_path = f"reachy_mini_conversation_app.tools.{tool_name}"

        try:
            if _try_import_tool(profile_import_path):
                logger.info("Loaded profile tool: %s", tool_name)
                continue
        except Exception as e:
            raise RuntimeError(f"Failed to load profile tool '{tool_name}': {_format_error(e)}") from e

        try:
            if _try_import_tool(shared_module_path):
                logger.info("Loaded built-in tool: %s", tool_name)
                continue
        except Exception as e:
            raise RuntimeError(f"Failed to load built-in tool '{tool_name}': {_format_error(e)}") from e

        raise RuntimeError(f"Tool '{tool_name}' was not found in the locked profile or built-in tools")


def _initialize_tools() -> None:
    """Populate registry once, even if module is imported repeatedly."""
    global ALL_TOOLS, ALL_TOOL_SPECS, _TOOLS_INITIALIZED

    if _TOOLS_INITIALIZED:
        logger.debug("Tools already initialized; skipping reinitialization.")
        return

    _load_profile_tools()

    ALL_TOOLS = {cls.name: cls() for cls in get_concrete_subclasses(Tool)}  # type: ignore[type-abstract]
    ALL_TOOL_SPECS = [tool.spec() for tool in ALL_TOOLS.values()]

    for tool_name, tool in ALL_TOOLS.items():
        logger.info(f"tool registered: {tool_name} - {tool.description}")

    _TOOLS_INITIALIZED = True


def get_tool_specs(exclusion_list: list[str] | None = None) -> list[Dict[str, Any]]:
    """Get tool specs, optionally excluding some tools."""
    _initialize_tools()
    exclusion_list = exclusion_list or []
    return [spec for spec in ALL_TOOL_SPECS if spec.get("name") not in exclusion_list]


def get_tool_specs_for_dependencies(deps: ToolDependencies) -> list[Dict[str, Any]]:
    """Return only tools whose runtime dependencies are available."""
    exclusions: list[str] = []
    if deps.camera_worker is None:
        exclusions.extend(("camera", "head_tracking", "scan_scene"))
    return get_tool_specs(exclusions)


# Dispatcher
def _safe_load_obj(args_json: str) -> Dict[str, Any]:
    try:
        parsed_args = json.loads(args_json or "{}")
        return parsed_args if isinstance(parsed_args, dict) else {}
    except Exception:
        logger.warning("bad args_json=%r", args_json)
        return {}


async def _dispatch_tool_call(tool_name: str, args: Dict[str, Any], deps: ToolDependencies) -> Dict[str, Any]:
    tool = ALL_TOOLS.get(tool_name)
    if not tool:
        return {"error": f"unknown tool: {tool_name}"}
    try:
        return await tool(deps, **args)
    except asyncio.CancelledError:
        logger.info("Tool cancelled: %s", tool_name)
        return {"error": "Tool cancelled"}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        logger.exception("Tool error in %s: %s", tool_name, msg)
        return {"error": msg}


async def dispatch_tool_call(tool_name: str, args_json: str, deps: ToolDependencies) -> Dict[str, Any]:
    """Dispatch a tool call by name with JSON args and dependencies."""
    _initialize_tools()
    return await _dispatch_tool_call(tool_name, _safe_load_obj(args_json), deps)


async def dispatch_tool_call_with_manager(
    tool_name: str, args_json: str, deps: ToolDependencies, tool_manager: "BackgroundToolManager"
) -> Dict[str, Any]:
    """Dispatch a tool call, injecting a BackgroundToolManager into the args."""
    _initialize_tools()
    args = _safe_load_obj(args_json)
    args["tool_manager"] = tool_manager
    return await _dispatch_tool_call(tool_name, args, deps)
