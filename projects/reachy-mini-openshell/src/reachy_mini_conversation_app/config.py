import os
import sys
import logging
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


LOCKED_PROFILE = "_reachy_mini_conversation_app_locked_profile"
DEFAULT_PROFILES_DIRECTORY = Path(__file__).parent / "profiles"

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment flag."""
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False

    logger.warning("Invalid boolean value for %s=%r, using default=%s", name, raw, default)
    return default


_profile_path = DEFAULT_PROFILES_DIRECTORY / LOCKED_PROFILE
_instructions_file = _profile_path / "instructions.txt"
if not _profile_path.is_dir():
    print(f"Error: locked profile '{LOCKED_PROFILE}' does not exist in {DEFAULT_PROFILES_DIRECTORY}", file=sys.stderr)
    sys.exit(1)
if not _instructions_file.is_file():
    print(f"Error: locked profile '{LOCKED_PROFILE}' has no instructions.txt", file=sys.stderr)
    sys.exit(1)

_skip_dotenv = _env_flag("REACHY_MINI_SKIP_DOTENV", default=False)

if _skip_dotenv:
    logger.info("Skipping .env loading because REACHY_MINI_SKIP_DOTENV is set")
else:
    # Locate .env file (search upward from current working directory)
    dotenv_path = find_dotenv(usecwd=True)

    if dotenv_path:
        # Load .env and override environment variables
        load_dotenv(dotenv_path=dotenv_path, override=True)
        logger.info(f"Configuration loaded from {dotenv_path}")
    else:
        logger.warning("No .env file found, using environment variables")


class Config:
    """Configuration class for the conversation app."""

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    MODEL_NAME = os.getenv("MODEL_NAME", "gpt-realtime")
    HF_HOME = os.getenv("HF_HOME", "./cache")
    LOCAL_VISION_MODEL = os.getenv("LOCAL_VISION_MODEL", "HuggingFaceTB/SmolVLM2-2.2B-Instruct")
    HF_TOKEN = os.getenv("HF_TOKEN")

    logger.debug(f"Model: {MODEL_NAME}, HF_HOME: {HF_HOME}, Vision Model: {LOCAL_VISION_MODEL}")
    logger.debug(f"Locked profile: {LOCKED_PROFILE}")

    def __init__(self) -> None:
        """Initialize the configuration."""
        logger.info("Using locked profile '%s' from %s", LOCKED_PROFILE, DEFAULT_PROFILES_DIRECTORY)


config = Config()
