#!/usr/bin/env python3
from __future__ import annotations

import select
import signal
import sys
import threading
import time

try:
    import gi  # noqa: F401

    from gi.repository import Gio, GLib
    from evdev import InputDevice, UInput, ecodes, list_devices
except (ImportError, ValueError) as error:
    raise SystemExit(
        "Missing dependencies. Run: sudo apt install python3-evdev python3-gi"
    ) from error


# Leave empty to detect all full keyboards. Stable explicit paths look like:
# ["/dev/input/by-id/usb-example-event-kbd"]
KEYBOARD_DEVICE_PATHS: list[str] = []

KEYS = {
    "mode_toggle": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "move_up": "KEY_I",
    "move_down": "KEY_K",
    "move_left": "KEY_J",
    "move_right": "KEY_L",
    "speed_large": "KEY_A",
    "speed_small": "KEY_S",
    "left_button": "KEY_SPACE",
    "right_button": "KEY_N",
    "middle_button": "KEY_M",
    "back": "KEY_U",
    "forward": "KEY_O",
    "scroll_down": "KEY_D",
    "scroll_up": "KEY_F",
    "control": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
}

SPEEDS = {
    "cursor_interval_seconds": 0.010,
    "cursor_base": 4,
    "cursor_large_add": 40,
    "cursor_small_add": 20,
    "scroll_interval_seconds": 0.010,
    "scroll_base": 10,
    "scroll_large_add": 64,
    "scroll_small_add": 24,
}

SMOOTH_SCROLL_ENABLED = True

# Any CSS colour GNOME Shell accepts, for example "#26a269" or "rgba(38,162,105,0.85)".
PANEL_COLOR = "#0f492f"

# Name of the virtual device this program creates. Auto-detection skips it so a
# restarting instance cannot grab the previous instance's output device.
OUTPUT_DEVICE_NAME = "Keyboard Mouse Wayland"

BUS_NAME = "org.kal.KeyboardMouse"
OBJECT_PATH = "/org/kal/KeyboardMouse"
INTERFACE_XML = f"""
<node>
  <interface name="{BUS_NAME}">
    <method name="GetState">
      <arg type="b" name="active" direction="out"/>
      <arg type="s" name="color" direction="out"/>
    </method>
    <signal name="StateChanged">
      <arg type="b" name="active"/>
      <arg type="s" name="color"/>
    </signal>
  </interface>
</node>
"""


def key_code(name: str) -> int:
    try:
        return getattr(ecodes, name)
    except AttributeError as error:
        raise ValueError(f"Unknown evdev key name: {name}") from error


def resolved_keys() -> dict[str, int | tuple[int, ...]]:
    result: dict[str, int | tuple[int, ...]] = {}
    for name, value in KEYS.items():
        if isinstance(value, tuple):
            result[name] = tuple(key_code(item) for item in value)
        else:
            result[name] = key_code(value)
    return result


class ModeService:
    """Publishes mouse mode on the session bus for the GNOME Shell extension."""

    def __init__(self) -> None:
        self.interface = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML).interfaces[0]
        self.connection: Gio.DBusConnection | None = None
        self.mouse_mode = False
        self.owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            None,
            None,
        )

    def _on_bus_acquired(self, connection: Gio.DBusConnection, _name: str) -> None:
        connection.register_object(
            OBJECT_PATH, self.interface, self._on_method_call, None, None
        )
        self.connection = connection

    def _on_method_call(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        _interface: str,
        _method: str,
        _params: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        invocation.return_value(self._state())

    def _state(self) -> GLib.Variant:
        return GLib.Variant("(bs)", (self.mouse_mode, PANEL_COLOR))

    def set_mode(self, enabled: bool) -> None:
        GLib.idle_add(self._publish, enabled)

    def _publish(self, enabled: bool) -> bool:
        self.mouse_mode = enabled
        if self.connection is not None:
            self.connection.emit_signal(
                None, OBJECT_PATH, BUS_NAME, "StateChanged", self._state()
            )
        return False


class KeyboardMouseController:
    def __init__(
        self,
        stop_event: threading.Event,
        mode_callback,
        quit_callback,
    ) -> None:
        self.stop_event = stop_event
        self.mode_callback = mode_callback
        self.quit_callback = quit_callback
        self.keys = resolved_keys()
        self.devices = self._open_keyboards()
        self.output = self._create_output()

        self.mouse_mode = False
        self.left_button_held = False
        self.ctrl_suppressed_for_scroll = False
        self.physical_down: set[int] = set()
        self.routes: dict[int, str] = {}
        self.standard_scroll_direction = 0
        self.scroll_accumulator = 0
        self.high_resolution_accumulator = 0
        self.thread: threading.Thread | None = None

        for device in self.devices:
            device.grab()
            print(f"Using keyboard: {device.path} ({device.name})")

    def _open_keyboards(self) -> list[InputDevice]:
        paths = KEYBOARD_DEVICE_PATHS or list_devices()
        devices: list[InputDevice] = []

        for path in paths:
            try:
                device = InputDevice(path)
            except (FileNotFoundError, PermissionError, OSError):
                if KEYBOARD_DEVICE_PATHS:
                    raise
                continue

            if KEYBOARD_DEVICE_PATHS or self._looks_like_full_keyboard(device):
                devices.append(device)
            else:
                device.close()

        if not devices:
            raise RuntimeError(
                "No accessible full keyboard found. Check input-group permissions or "
                "set KEYBOARD_DEVICE_PATHS explicitly."
            )

        return devices

    @staticmethod
    def _looks_like_full_keyboard(device: InputDevice) -> bool:
        if device.name == OUTPUT_DEVICE_NAME:
            return False

        available = set(device.capabilities().get(ecodes.EV_KEY, []))
        required = {
            ecodes.KEY_A,
            ecodes.KEY_Z,
            ecodes.KEY_SPACE,
            ecodes.KEY_ENTER,
            ecodes.KEY_LEFTCTRL,
        }
        return required.issubset(available)

    def _create_output(self) -> UInput:
        key_codes: set[int] = {
            ecodes.BTN_LEFT,
            ecodes.BTN_RIGHT,
            ecodes.BTN_MIDDLE,
            ecodes.BTN_SIDE,
            ecodes.BTN_EXTRA,
        }
        for device in self.devices:
            key_codes.update(device.capabilities().get(ecodes.EV_KEY, []))

        capabilities = {
            ecodes.EV_KEY: sorted(key_codes),
            ecodes.EV_REL: [
                ecodes.REL_X,
                ecodes.REL_Y,
                ecodes.REL_WHEEL,
                ecodes.REL_WHEEL_HI_RES,
            ],
        }
        return UInput(capabilities, name=OUTPUT_DEVICE_NAME)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="input-loop", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)

        self._release_left_button()
        self._restore_ctrl_after_scroll()

        for device in self.devices:
            try:
                device.ungrab()
            except OSError:
                pass
            device.close()
        self.output.close()

    def _run(self) -> None:
        pointer_interval = SPEEDS["cursor_interval_seconds"]
        scroll_interval = SPEEDS["scroll_interval_seconds"]
        next_pointer = time.monotonic()
        next_scroll = next_pointer

        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                timeout = max(0.0, min(next_pointer, next_scroll) - now)
                readable, _, _ = select.select(self.devices, [], [], min(timeout, 0.05))

                for device in readable:
                    try:
                        events = device.read()
                    except BlockingIOError:
                        continue
                    for event in events:
                        if event.type == ecodes.EV_KEY:
                            self._handle_key(event.code, event.value)

                now = time.monotonic()
                if now >= next_pointer:
                    self._update_pointer()
                    next_pointer = now + pointer_interval
                if now >= next_scroll:
                    self._update_scroll()
                    next_scroll = now + scroll_interval
        except Exception as error:
            print(f"Input loop stopped: {error}", file=sys.stderr)
            self.stop_event.set()
            GLib.idle_add(self.quit_callback)

    def _handle_key(self, code: int, value: int) -> None:
        if value == 1:
            self.physical_down.add(code)
        elif value == 0:
            self.physical_down.discard(code)

        if code in self.keys["mode_toggle"]:
            if value == 0:
                self._toggle_mouse_mode()
            return

        if value == 1:
            route = self._choose_route(code)
            self.routes[code] = route
            self._start_route(code, route)
            return

        route = self.routes.get(code)
        if value == 2:
            if route == "forward":
                self._emit_key(code, 2)
            return

        if value == 0:
            self.routes.pop(code, None)
            self._end_route(code, route)

    def _choose_route(self, code: int) -> str:
        if not self.mouse_mode:
            return "forward"

        if code in self.keys["control"] and self._is_down("scroll_up"):
            return "ctrl_suppressed"

        if (
            code == self.keys["speed_large"]
            and self._any_control_down()
            and not self.ctrl_suppressed_for_scroll
        ):
            return "forward"

        if code == self.keys["left_button"]:
            return "left_button"

        if code == self.keys["scroll_up"]:
            return "scroll_up"

        button_actions = {
            self.keys["right_button"]: ecodes.BTN_RIGHT,
            self.keys["middle_button"]: ecodes.BTN_MIDDLE,
            self.keys["back"]: ecodes.BTN_SIDE,
            self.keys["forward"]: ecodes.BTN_EXTRA,
        }
        if code in button_actions:
            self._click(button_actions[code])
            return "consume"

        suppressed = {
            self.keys["move_up"],
            self.keys["move_down"],
            self.keys["move_left"],
            self.keys["move_right"],
            self.keys["speed_large"],
            self.keys["speed_small"],
            self.keys["scroll_down"],
            self.keys["scroll_up"],
        }
        return "consume" if code in suppressed else "forward"

    def _start_route(self, code: int, route: str) -> None:
        if route == "forward":
            self._emit_key(code, 1)
        elif route == "left_button":
            self._hold_left_button()
        elif route == "scroll_up":
            self._suppress_ctrl_for_scroll()

    def _end_route(self, code: int, route: str | None) -> None:
        if route == "forward":
            self._emit_key(code, 0)
        elif route == "left_button":
            self._release_left_button()
        elif route == "scroll_up":
            self._restore_ctrl_after_scroll()

    def _toggle_mouse_mode(self) -> None:
        self.mouse_mode = not self.mouse_mode
        if not self.mouse_mode:
            self._release_left_button()
            self._restore_ctrl_after_scroll()
            self._reset_scroll()
        self.mode_callback(self.mouse_mode)

    def _is_down(self, name: str) -> bool:
        return self.keys[name] in self.physical_down

    def _any_control_down(self) -> bool:
        return any(code in self.physical_down for code in self.keys["control"])

    def _emit_key(self, code: int, value: int) -> None:
        self.output.write(ecodes.EV_KEY, code, value)
        self.output.syn()

    def _click(self, button: int) -> None:
        self._emit_key(button, 1)
        self._emit_key(button, 0)

    def _hold_left_button(self) -> None:
        if self.left_button_held:
            return
        self._emit_key(ecodes.BTN_LEFT, 1)
        self.left_button_held = True

    def _release_left_button(self) -> None:
        if not self.left_button_held:
            return
        self._emit_key(ecodes.BTN_LEFT, 0)
        self.left_button_held = False

    def _suppress_ctrl_for_scroll(self) -> None:
        if self.ctrl_suppressed_for_scroll:
            return

        for control in self.keys["control"]:
            if self.routes.get(control) == "forward":
                self._emit_key(control, 0)
                self.routes[control] = "ctrl_suppressed"
        self.ctrl_suppressed_for_scroll = True

    def _restore_ctrl_after_scroll(self) -> None:
        if not self.ctrl_suppressed_for_scroll:
            return

        self.ctrl_suppressed_for_scroll = False
        for control in self.keys["control"]:
            if control in self.physical_down:
                self._emit_key(control, 1)
                self.routes[control] = "forward"

    def _update_pointer(self) -> None:
        if not self.mouse_mode:
            return

        distance = SPEEDS["cursor_base"]
        if self._is_down("speed_large"):
            distance += SPEEDS["cursor_large_add"]
        if self._is_down("speed_small"):
            distance += SPEEDS["cursor_small_add"]

        x_distance = distance * (
            int(self._is_down("move_right")) - int(self._is_down("move_left"))
        )
        y_distance = distance * (
            int(self._is_down("move_down")) - int(self._is_down("move_up"))
        )

        if not x_distance and not y_distance:
            return

        if x_distance:
            self.output.write(ecodes.EV_REL, ecodes.REL_X, x_distance)
        if y_distance:
            self.output.write(ecodes.EV_REL, ecodes.REL_Y, y_distance)
        self.output.syn()

    def _update_scroll(self) -> None:
        if not self.mouse_mode:
            self._restore_ctrl_after_scroll()
            self._reset_scroll()
            return

        distance = SPEEDS["scroll_base"]
        if self._is_down("speed_large"):
            distance += SPEEDS["scroll_large_add"]
        if self._is_down("speed_small"):
            distance += SPEEDS["scroll_small_add"]

        scroll_up = self._is_down("scroll_up")
        scroll_down = self._is_down("scroll_down")

        if scroll_up and not scroll_down:
            self._suppress_ctrl_for_scroll()
            self._scroll(distance)
            return

        self._restore_ctrl_after_scroll()

        if scroll_down and not scroll_up:
            self._scroll(-distance)
            return

        self._reset_scroll()

    def _scroll(self, distance: int) -> None:
        if SMOOTH_SCROLL_ENABLED:
            self._smooth_scroll(distance)
            return

        direction = 1 if distance > 0 else -1
        if self.standard_scroll_direction != direction:
            self._emit_wheel_notches(direction)
            self.standard_scroll_direction = direction
            self.scroll_accumulator = 0
            return

        self.scroll_accumulator += distance
        steps = abs(self.scroll_accumulator) // 120
        if not steps:
            return

        self._emit_wheel_notches(direction * steps)
        self.scroll_accumulator -= direction * steps * 120

    def _smooth_scroll(self, distance: int) -> None:
        self.high_resolution_accumulator += distance
        legacy_steps = int(self.high_resolution_accumulator / 120)

        self.output.write(ecodes.EV_REL, ecodes.REL_WHEEL_HI_RES, distance)
        if legacy_steps:
            self.output.write(ecodes.EV_REL, ecodes.REL_WHEEL, legacy_steps)
            self.high_resolution_accumulator -= legacy_steps * 120
        self.output.syn()

    def _emit_wheel_notches(self, steps: int) -> None:
        self.output.write(ecodes.EV_REL, ecodes.REL_WHEEL_HI_RES, steps * 120)
        self.output.write(ecodes.EV_REL, ecodes.REL_WHEEL, steps)
        self.output.syn()

    def _reset_scroll(self) -> None:
        self.standard_scroll_direction = 0
        self.scroll_accumulator = 0
        self.high_resolution_accumulator = 0


def main() -> int:
    stop_event = threading.Event()
    service = ModeService()
    loop = GLib.MainLoop()
    controller: KeyboardMouseController | None = None

    def stop(*_args: object) -> bool:
        stop_event.set()
        loop.quit()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)

    try:
        controller = KeyboardMouseController(stop_event, service.set_mode, loop.quit)
        controller.start()
        loop.run()
    except (PermissionError, OSError, RuntimeError, ValueError) as error:
        print(f"Cannot start keyboard mouse: {error}", file=sys.stderr)
        return 1
    finally:
        if controller is not None:
            controller.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
