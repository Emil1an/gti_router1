"""PTZ subsystem lifecycle service (Story 4.5).

``PTZService`` bundles the Epic 4 pieces — one
:class:`~camera.ptz_control.PTZController` per PTZ-capable camera, the
:class:`~camera.validator.PTZCommandValidator`, the
:class:`~camera.command_executor.CommandExecutor`, and the
:class:`~camera.command_receiver.CommandReceiver` — behind a single
``async start()`` / ``async stop()`` so the orchestrator (``main.py``) wires it
without holding any PTZ logic.

Activation is **conditional** (AC#1): PTZ only comes up when
  (a) a camera is ``ptz_enabled`` and its ``PTZController.connect()`` succeeds, **and**
  (b) Supabase registration succeeded (``supabase_connected`` + a linked
      ``gateway_id`` — otherwise there is no identity to receive commands for).

When (b) is not yet met the service stays inactive and **retries** in the
background when registration recovers (Story 3.6), never blocking the Router.
A camera without PTZ logs INFO (not an error) and is skipped; any PTZ failure is
contained so capture/upload/health keep running (AC#2, #3, #5).  PTZ capabilities
+ position + receiver status are published into ``AppState.per_camera`` for the
health report (AC#6).
"""

from __future__ import annotations

import asyncio

from camera.command_executor import CommandExecutor
from camera.command_receiver import CommandReceiver
from camera.ptz_control import PTZController
from camera.validator import PTZCommandValidator
from config.loader import get_config
from config.schema import CameraConfig
from health.degraded import ptz_available
from health.state import AppState, CameraState
from health.supabase_client import SupabaseClient
from utils.errors import PTZError, PTZUnsupportedError
from utils.logging import get_logger


class PTZService:
    """Owns and lifecycles the per-Router PTZ subsystem."""

    def __init__(
        self,
        client: SupabaseClient,
        app_state: AppState,
        cameras: list[CameraConfig] | None = None,
        realtime: object | None = None,
    ) -> None:
        cfg = get_config()
        self._client = client
        self._app_state = app_state
        self._realtime = realtime
        self._cameras = cameras if cameras is not None else list(cfg.cameras)
        self._camera_ids = [c.camera_id for c in self._cameras]

        self._onvif_timeout = cfg.ptz.onvif_timeout_s
        self._cmd_retries = cfg.ptz.command_max_retries
        self._activation_retry_s = cfg.ptz.activation_retry_s

        self._controllers: dict[str, PTZController] = {}
        self._executor: CommandExecutor | None = None
        self._validator: PTZCommandValidator | None = None
        self._receiver: CommandReceiver | None = None

        self._activated = False
        self._running = False
        self._activate_task: asyncio.Task[None] | None = None
        self._logger = get_logger(__name__)

    # ── Accessors (tests / health) ───────────────────────────────────────────────

    @property
    def activated(self) -> bool:
        return self._activated

    @property
    def receiver(self) -> CommandReceiver | None:
        return self._receiver

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Activate PTZ if conditions are met; otherwise retry in the background."""
        self._running = True
        if self._registration_ok():
            await self._activate()
        else:
            self._logger.info(
                "PTZ inactive — Supabase registration not completed yet; "
                "will activate automatically once registered."
            )
            self._activate_task = asyncio.create_task(
                self._await_registration(), name="ptz-activation-retry"
            )

    async def stop(self) -> None:
        """Stop the receiver, cancel retries, and disconnect controllers cleanly."""
        self._running = False
        if self._activate_task is not None and not self._activate_task.done():
            self._activate_task.cancel()
            try:
                await self._activate_task
            except (asyncio.CancelledError, Exception):
                pass
        self._activate_task = None

        if self._receiver is not None:
            try:
                await self._receiver.stop()
            except Exception as exc:  # contained — never block shutdown
                self._logger.error("Error stopping PTZ receiver: %s", exc)

        for controller in self._controllers.values():
            try:
                await controller.disconnect()
            except Exception:
                pass
        self._logger.info("PTZ subsystem stopped")

    # ── Activation ────────────────────────────────────────────────────────────────

    def _registration_ok(self) -> bool:
        """PTZ may only activate after a successful Supabase registration."""
        return self._app_state.supabase_connected and ptz_available(self._app_state)

    async def _await_registration(self) -> None:
        try:
            while self._running and not self._activated:
                await asyncio.sleep(self._activation_retry_s)
                if self._running and self._registration_ok():
                    await self._activate()
                    return
        except asyncio.CancelledError:
            pass

    async def _activate(self) -> None:
        if self._activated:
            return

        for cam in self._cameras:
            if not cam.ptz_enabled:
                continue
            controller = PTZController.from_camera_config(
                cam, timeout_s=self._onvif_timeout, command_max_retries=self._cmd_retries
            )
            try:
                await controller.connect()
            except PTZUnsupportedError as exc:
                # Camera without PTZ is normal — INFO, not an error (AC#2).
                self._logger.info(
                    "Camera %s has no usable PTZ — skipping: %s", cam.camera_id, exc
                )
                continue
            except PTZError as exc:
                # Any other PTZ failure is contained per-camera (AC#5).
                self._logger.warning(
                    "PTZ connect failed for %s (contained): %s", cam.camera_id, exc
                )
                continue
            self._controllers[cam.camera_id] = controller
            await self._publish_health(cam.camera_id, controller)

        if not self._controllers:
            self._logger.info("No PTZ-capable cameras — PTZ receiver not started")
            self._activated = True  # nothing more to do; don't keep retrying connects
            return

        self._executor = CommandExecutor(self._controllers, self._client)
        self._validator = PTZCommandValidator(self._camera_ids)
        self._receiver = CommandReceiver(
            client=self._client,
            handler=self._executor.execute,
            camera_ids=self._camera_ids,
            app_state=self._app_state,
            realtime=self._realtime,
            validator=self._validator,
        )
        await self._receiver.start()
        self._activated = True
        self._logger.info(
            "PTZ subsystem activated", extra={"cameras": list(self._controllers)}
        )

    async def _publish_health(self, camera_id: str, controller: PTZController) -> None:
        """Publish PTZ capabilities + current position into per_camera health."""
        position = None
        try:
            position = await controller.get_position()
        except PTZError as exc:
            self._logger.debug("Initial PTZ get_position failed for %s: %s", camera_id, exc)

        cam_state = self._app_state.per_camera.get(camera_id)
        if cam_state is None:
            cam_state = CameraState(camera_id=camera_id, input_type="rtsp_ip")
            self._app_state.set_camera(cam_state)
        cam_state.ptz = {
            "supported": True,
            "capabilities": {
                "pan": controller.supports_pan,
                "tilt": controller.supports_tilt,
                "zoom": controller.supports_zoom,
                "presets": controller.supports_presets,
            },
            "position": position,
            "realtime_connected": (
                self._receiver.ptz_realtime_connected if self._receiver else False
            ),
        }
