"""Entrypoint for the Reachy Mini conversation app."""

from __future__ import annotations
import os
import sys
import argparse
import threading
from typing import Any, Dict, List, Callable, Optional
from pathlib import Path

from reachy_mini_conversation_app.utils import (
    parse_args,
    setup_logger,
    log_connection_troubleshooting,
)


def update_chatbot(chatbot: List[Dict[str, Any]], response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Update the chatbot with AdditionalOutputs."""
    chatbot.append(response)
    return chatbot


def _shutdown_step(logger: Any, name: str, callback: Any) -> None:
    """Preserve the original cleanup helper for callers and regression tests."""
    try:
        callback()
    except KeyboardInterrupt:
        logger.warning("Shutdown interrupted while stopping %s; continuing cleanup.", name)
    except Exception as exc:
        logger.debug("Error while stopping %s: %s", name, exc)


def _build_tool_transport_factory(
    mode: str,
    dependencies: Any,
    *,
    rest_base_url: str | None = None,
    camera_base_url: str | None = None,
    rest_timeout_seconds: float = 5.0,
    motion_duration_seconds: float = 1.0,
    motion_poll_interval_seconds: float = 0.1,
    motion_completion_timeout_seconds: float = 10.0,
) -> Callable[[], Any]:
    """Build per-conversation transports without opening a network connection yet."""
    from reachy_mini_conversation_app.tool_transport import (
        LocalToolTransport,
        RoutedToolTransport,
        ConversationUtilityTransport,
    )
    from reachy_mini_conversation_app.rest_tool_transport import RestToolTransport, RestTransportSettings

    if mode == "local":
        return lambda: LocalToolTransport(dependencies)
    if mode != "rest":
        raise ValueError(f"Unsupported tool transport: {mode!r}")
    if not rest_base_url or rest_base_url.strip().lower() in {"", "<set-me>", "set-me"}:
        raise ValueError("REACHY_REST_BASE_URL must be set when REACHY_TOOL_TRANSPORT=rest")

    settings = RestTransportSettings(
        base_url=rest_base_url,
        camera_base_url=camera_base_url,
        request_timeout_seconds=rest_timeout_seconds,
        motion_duration_seconds=motion_duration_seconds,
        poll_interval_seconds=motion_poll_interval_seconds,
        completion_timeout_seconds=motion_completion_timeout_seconds,
    )

    def create_rest_transport() -> Any:
        return RoutedToolTransport(
            remote=RestToolTransport(settings),
            local=ConversationUtilityTransport(),
        )

    return create_rest_transport


def main() -> None:
    """Entrypoint for the Reachy Mini conversation app."""
    args, _ = parse_args()
    run(args)


def run(
    args: argparse.Namespace,
    robot: Any | None = None,
    app_stop_event: Optional[threading.Event] = None,
    settings_app: Optional[Any] = None,
    instance_path: Optional[str] = None,
) -> None:
    """Run the Reachy Mini conversation app."""
    try:
        import gradio as gr
        from fastapi import FastAPI
        from fastrtc import Stream
    except ImportError as exc:
        raise RuntimeError(
            "Reachy OpenShell conversation dependencies are not installed. "
            "Run `uv sync` or `pip install -e .` from this project directory."
        ) from exc

    # Putting these dependencies here makes the dashboard faster to load when the conversation app is installed
    from reachy_mini_conversation_app.config import (
        TOOL_TRANSPORT_REST,
        config,
        load_dotenv_file,
    )
    from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
    from reachy_mini_conversation_app.conversation_stream import ConversationStreamHandler

    logger = setup_logger(args.debug)
    logger.info("Starting Reachy Mini Conversation App")

    if instance_path:
        try:
            load_dotenv_file(Path(instance_path) / ".env")
        except Exception as exc:
            logger.debug("Instance .env loading skipped: %s", exc)

    tool_transport_mode = getattr(args, "tool_transport", None) or config.REACHY_TOOL_TRANSPORT
    runtime: Any | None = None

    if tool_transport_mode == TOOL_TRANSPORT_REST:
        logger.info("Using direct Reachy REST tool transport; local robot SDK workers are disabled")
        if args.head_tracker is not None or args.local_vision:
            logger.warning("--head-tracker and --local-vision are ignored in REST mode")
        if not args.gradio:
            logger.info("REST mode has no in-process robot audio device; automatically enabling Gradio")
            args.gradio = True
        dependencies = ToolDependencies(
            capture_directory=Path(os.getenv("REACHY_CAPTURE_DIR", "captures")).expanduser(),
        )
        robot = None
    else:
        from reachy_mini_conversation_app.robot_runtime import ReachyRuntime

        if args.no_camera and args.head_tracker is not None:
            logger.warning(
                "Head tracking disabled: --no-camera flag is set. Remove --no-camera to enable head tracking."
            )
        try:
            runtime = ReachyRuntime.connect(
                robot_name=args.robot_name,
                robot=robot,
                no_camera=args.no_camera,
                head_tracker=args.head_tracker,
                local_vision=args.local_vision,
                capture_directory=Path(os.getenv("REACHY_CAPTURE_DIR", "captures")),
                log=logger,
            )
        except TimeoutError as e:
            logger.error(f"Connection timeout: Failed to connect to Reachy Mini daemon. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)
        except ConnectionError as e:
            logger.error(f"Connection failed: Unable to establish connection to Reachy Mini. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)
        except Exception as e:
            logger.error(f"Unexpected error during robot initialization: {type(e).__name__}: {e}")
            logger.error("Please check your configuration and try again.")
            sys.exit(1)

        robot = runtime.robot
        dependencies = runtime.dependencies

        # Auto-enable Gradio in simulation mode (both MuJoCo for daemon and mockup-sim for desktop app)
        if runtime.is_simulation and not args.gradio:
            logger.info("Simulation mode detected. Automatically enabling gradio flag.")
            args.gradio = True

    try:
        tool_transport_factory = _build_tool_transport_factory(
            tool_transport_mode,
            dependencies,
            rest_base_url=config.REACHY_REST_BASE_URL,
            camera_base_url=config.REACHY_CAMERA_BASE_URL,
            rest_timeout_seconds=config.REACHY_REST_TIMEOUT_SECONDS,
            motion_duration_seconds=config.REACHY_MOTION_DURATION_SECONDS,
            motion_poll_interval_seconds=config.REACHY_MOTION_POLL_INTERVAL_SECONDS,
            motion_completion_timeout_seconds=config.REACHY_MOTION_COMPLETION_TIMEOUT_SECONDS,
        )
    except ValueError as e:
        logger.error("Invalid tool transport configuration: %s", e)
        sys.exit(1)

    media_result_processor = None
    if tool_transport_mode != TOOL_TRANSPORT_REST:
        from reachy_mini_conversation_app.media_result_processor import MediaResultProcessor

        try:
            media_result_processor = MediaResultProcessor(
                vision_router=dependencies.vision_router,
                capture_directory=dependencies.capture_directory or Path("captures"),
                require_routed_vision=config.REQUIRE_ROUTED_VISION,
            )
        except ValueError as e:
            logger.error("Invalid routed vision configuration: %s", e)
            sys.exit(1)

    current_file_path = os.path.dirname(os.path.abspath(__file__))
    logger.debug(f"Current file absolute path: {current_file_path}")
    chatbot = gr.Chatbot(
        type="messages",
        resizable=True,
        avatar_images=(
            os.path.join(current_file_path, "images", "user_avatar.png"),
            os.path.join(current_file_path, "images", "reachymini_avatar.png"),
        ),
    )
    logger.debug(f"Chatbot avatar images: {chatbot.avatar_images}")

    handler = ConversationStreamHandler(
        dependencies,
        gradio_mode=args.gradio,
        instance_path=instance_path,
        model_logs=args.model_logs,
        tool_transport_factory=tool_transport_factory,
        media_result_processor=media_result_processor,
    )

    stream_manager: Any | None = None

    if args.gradio:
        stream = Stream(
            handler=handler,
            mode="send-receive",
            modality="audio",
            additional_inputs=[
                chatbot,
            ],
            additional_outputs=[chatbot],
            additional_outputs_handler=update_chatbot,
            ui_args={"title": "Talk with Reachy Mini"},
        )
        stream_manager = stream.ui

        with stream_manager:
            input_mode = gr.Radio(
                choices=["Microphone", "Text"],
                value="Microphone",
                label="Input",
            )
            with gr.Row():
                text_input = gr.Textbox(
                    label="Message",
                    lines=2,
                    max_lines=5,
                    visible=False,
                )
                send_button = gr.Button("Send", variant="primary", visible=False)

            def switch_input_mode(mode: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
                text_mode = mode == "Text"
                return (
                    gr.update(visible=not text_mode),
                    gr.update(visible=text_mode),
                    gr.update(visible=text_mode),
                )

            async def send_text_message(
                message: str,
                chatbot_messages: List[Dict[str, Any]] | None,
            ) -> tuple[List[Dict[str, Any]], str]:
                updated_chatbot = list(chatbot_messages or [])
                updated_chatbot.extend(await handler.send_text_message(message))
                return updated_chatbot, ""

            input_mode.change(
                fn=switch_input_mode,
                inputs=input_mode,
                outputs=[stream.webrtc_component, text_input, send_button],
            )
            text_input.submit(
                fn=send_text_message,
                inputs=[text_input, chatbot],
                outputs=[chatbot, text_input],
            )
            send_button.click(
                fn=send_text_message,
                inputs=[text_input, chatbot],
                outputs=[chatbot, text_input],
            )

        if not settings_app:
            app = FastAPI()
        else:
            app = settings_app

        app = gr.mount_gradio_app(app, stream.ui, path="/")
    else:
        from reachy_mini_conversation_app.console import LocalStream

        # In headless mode, wire settings_app + instance_path to console LocalStream
        if robot is None:
            raise RuntimeError("Headless mode requires the local Reachy tool transport")
        stream_manager = LocalStream(
            handler,
            robot,
            settings_app=settings_app,
            instance_path=instance_path,
        )

    def poll_stop_event() -> None:
        """Poll the stop event to allow graceful shutdown."""
        if app_stop_event is not None:
            app_stop_event.wait()

        logger.info("App stop event detected, shutting down...")
        try:
            stream_manager.close()
        except Exception as e:
            logger.error(f"Error while closing stream manager: {e}")

    if app_stop_event:
        threading.Thread(target=poll_stop_event, daemon=True).start()

    try:
        # Each robot service owns its own thread/loop behind the shared runtime.
        if runtime is not None:
            runtime.start()
        stream_manager.launch()
    except KeyboardInterrupt:
        logger.info("Keyboard interruption in main thread... closing server.")
    finally:
        if runtime is not None:
            runtime.stop()


if __name__ == "__main__":
    main()
