import sys
import logging
from pathlib import Path

from reachy_mini_conversation_app.config import LOCKED_PROFILE, DEFAULT_PROFILES_DIRECTORY


logger = logging.getLogger(__name__)


INSTRUCTIONS_FILENAME = "instructions.txt"
VOICE_FILENAME = "voice.txt"


def _locked_profile_path() -> Path:
    return DEFAULT_PROFILES_DIRECTORY / LOCKED_PROFILE


def get_session_instructions() -> str:
    """Load the locked profile instructions for the realtime session."""
    instructions_file = _locked_profile_path() / INSTRUCTIONS_FILENAME
    logger.info("Loading prompt from locked profile '%s'", LOCKED_PROFILE)

    try:
        if instructions_file.exists():
            instructions = instructions_file.read_text(encoding="utf-8").strip()
            if instructions:
                return instructions
            logger.error("Locked profile '%s' has empty %s", LOCKED_PROFILE, INSTRUCTIONS_FILENAME)
            sys.exit(1)
        logger.error("Locked profile '%s' has no %s", LOCKED_PROFILE, INSTRUCTIONS_FILENAME)
        sys.exit(1)
    except Exception as e:
        logger.error("Failed to load instructions from locked profile '%s': %s", LOCKED_PROFILE, e)
        sys.exit(1)


def get_session_voice(default: str = "cedar") -> str:
    """Resolve the locked profile voice."""
    try:
        voice_file = _locked_profile_path() / VOICE_FILENAME
        if voice_file.exists():
            voice = voice_file.read_text(encoding="utf-8").strip()
            return voice or default
    except Exception:
        pass
    return default
