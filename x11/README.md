# Keyboard mouse for Ubuntu 24.04 X11

This is the native X11 equivalent of the AutoHotkey script. It reads a physical keyboard with `evdev`, suppresses mouse-mode keys, and emits a virtual keyboard and mouse with Linux `uinput`.

## Bindings

Tap either Super key to toggle mouse mode.

| Key | Action |
|---|---|
| I, K, J, L | Move up, down, left, right |
| A | Large additive speed modifier |
| S | Small additive speed modifier |
| Space | Hold left mouse button |
| N | Right click |
| M | Middle click |
| U | Mouse4, Back |
| O | Mouse5, Forward |
| D | Scroll down |
| F | Scroll up with Ctrl suppressed |

Ctrl+A passes through to the active application. The Super key is consumed as the toggle and never reaches the desktop, so it no longer opens the GNOME Activities overview. Use the Activities corner or a different shortcut for that.

While mouse mode is on the GNOME Shell top bar is tinted. It returns to the theme colour when mouse mode is off, when the program stops, and when the extension is disabled.

## How the top bar tint works

Only code running inside `gnome-shell` can restyle the panel, so this is split across two processes.

The program owns the name `org.kal.KeyboardMouse` on the session bus, exposes `GetState() -> (bool active, string color)`, and emits `StateChanged(bool, string)` on every toggle. The GNOME Shell extension watches for that name, reads the current state when it appears, then follows the signal and applies the colour as an inline style on `Main.panel`.

The extension is the watcher, so the two can start, stop, and restart in any order without losing sync. The colour travels over the bus with every state change, which keeps all configuration in `keyboard_mouse_x11.py` instead of a separate settings schema.

## Install

### 1. Dependencies

```bash
sudo apt update
sudo apt install python3-evdev python3-gi
```

Ubuntu 24.04 provides `python3-evdev` in its repositories. The implementation follows the Linux `uinput` interface for virtual input devices.

### 2. Input access

Access to the `input` group allows software to read every key and inject input. Only grant it to trusted accounts.

From this directory, run:

```bash
sudo install -m 0644 99-keyboard-mouse-uinput.rules /etc/udev/rules.d/
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
sudo modprobe uinput
sudo udevadm control --reload-rules
sudo udevadm trigger
sudo usermod -aG input "$USER"
```

Log out and back in so the new group membership takes effect. Confirm with:

```bash
id -nG | tr ' ' '\n' | grep -x input
ls -l /dev/uinput
```

The device should be owned by `root:input` with mode `0660`.

### 3. GNOME Shell extension

```bash
cp -r gnome-extension/keyboard-mouse-panel@kal ~/.local/share/gnome-shell/extensions/
```

A running shell does not notice a newly installed extension directory, so reload it. On X11 press Alt+F2, type `r`, press Enter, which restarts the shell in place and keeps your windows. On Wayland log out and back in. Then enable it:

```bash
gnome-extensions enable keyboard-mouse-panel@kal
gnome-extensions info keyboard-mouse-panel@kal
```

The second command should report `State: ACTIVE`.

The extension tints every top bar on screen, including the copies that panel-duplicating extensions such as Multi Monitor Bar put on secondary monitors. It finds them structurally rather than by knowing about any particular extension: each bar is an actor named `panel` inside a container named `panelBox`, parented to `uiGroup` by `Main.layoutManager.addChrome`. Matching on the actor name is deliberate, because GNOME's own panel is styled through its `#panel` id and carries no `panel` style class.

All bars are restyled in a single synchronous pass inside one signal handler, so they change together in the same frame instead of rippling across monitors. Panels are looked up fresh on each change, so a bar that appears later is picked up, and the extension re-applies on `monitors-changed` when a display is plugged in and the duplicate bars are rebuilt.

The tint also stays visible in the overview, where the theme normally makes the panel transparent.

### 4. Start at login

The unit is wanted by `graphical-session.target`, so it starts at graphical login and stops when the session ends. No terminal has to stay open.

```bash
mkdir -p ~/.local/share/keyboard-mouse ~/.config/systemd/user
ln -sfn "$PWD/keyboard_mouse_x11.py" ~/.local/share/keyboard-mouse/keyboard_mouse_x11.py
cp keyboard-mouse-x11.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now keyboard-mouse-x11.service
```

The symlink keeps this checkout as the only copy of the script, so edits here take effect on the next restart. Moving or deleting the checkout breaks the link.

## Everyday use

Manage the service:

```bash
systemctl --user status keyboard-mouse-x11.service     # is it running
systemctl --user restart keyboard-mouse-x11.service    # apply config edits
systemctl --user stop keyboard-mouse-x11.service       # release the keyboards now
systemctl --user start keyboard-mouse-x11.service
systemctl --user disable --now keyboard-mouse-x11.service   # stop starting at login
```

Follow its output:

```bash
journalctl --user -u keyboard-mouse-x11.service -f
```

On a healthy start it prints one `Using keyboard:` line per grabbed device.

Ask the running program for its current state without touching the keyboard:

```bash
gdbus call --session --dest org.kal.KeyboardMouse \
  --object-path /org/kal/KeyboardMouse \
  --method org.kal.KeyboardMouse.GetState
```

It answers `(false, '#26a269')` with mouse mode off, `true` with it on. Watch toggles live:

```bash
gdbus monitor --session --dest org.kal.KeyboardMouse
```

Turn the tint off without stopping mouse mode:

```bash
gnome-extensions disable keyboard-mouse-panel@kal
```

## Test interactively

The service and a hand-started copy cannot both run, because grabbing an input device is exclusive. Stop the service first:

```bash
systemctl --user stop keyboard-mouse-x11.service
python3 keyboard_mouse_x11.py
```

Stop the program with Ctrl+C in its terminal. Linux releases grabbed devices when the process exits. Start the service again when you are done.

The program automatically grabs every accessible device that looks like a full keyboard, and skips its own virtual device. If detection selects the wrong device, list stable keyboard paths:

```bash
ls -l /dev/input/by-id/*-event-kbd
```

Then set `KEYBOARD_DEVICE_PATHS` near the top of `keyboard_mouse_x11.py`.

## Configuration

Edit the values near the top of `keyboard_mouse_x11.py`, then run `systemctl --user restart keyboard-mouse-x11.service`.

- `KEYS` contains every binding.
- `SPEEDS` contains cursor and scrolling distances and intervals.
- `SMOOTH_SCROLL_ENABLED` chooses high-resolution or stepped scrolling.
- `KEYBOARD_DEVICE_PATHS` overrides automatic device detection.
- `PANEL_COLOR` is the top bar colour, sent to the extension with every state change. Any CSS colour works, including `rgba(38,162,105,0.85)` for a translucent bar.
- `OUTPUT_DEVICE_NAME` names the virtual device and is what auto-detection excludes.

Changing `PANEL_COLOR` needs only a service restart. The extension reads the colour off the bus and does not need reloading.

Smooth scrolling depends on GNOME, libinput, and the active application's support for high-resolution wheel input.

## Troubleshooting

**Only some monitors change colour.** The bars on other monitors are created by a separate extension, so they exist only while that extension is enabled and has finished building them. Check it is active, and note that bars added by extensions which do not follow the `panelBox` / `panel` naming used by GNOME will not be found.

**The top bar does not change colour.** Check the program is publishing with the `gdbus call` above. If that answers, the problem is on the shell side: confirm `gnome-extensions info keyboard-mouse-panel@kal` says `ACTIVE`, and check for extension errors with `journalctl --user -u org.gnome.Shell@x11 -f` or `journalctl --user -b | grep -i keyboard-mouse-panel`. A freshly copied extension needs a shell reload before it can be enabled.

**`Cannot start keyboard mouse: [Errno 16] Device or resource busy`.** Something else already holds an exclusive grab on a keyboard, almost always a second copy of this program. Find it with `pgrep -af keyboard_mouse_x11.py` and stop it, then check `systemctl --user status keyboard-mouse-x11.service`.

**`No accessible full keyboard found`.** Group membership has not taken effect. Log out and back in, then verify with `id -nG | grep input`.

**The keyboard stops responding.** Stop the program from another machine over SSH, or switch to a text console with Ctrl+Alt+F3 and run `systemctl --user stop keyboard-mouse-x11.service`. Grabs are released when the process exits, and the kernel drops them if it is killed.

**Mouse mode is stuck on.** The state lives in the running process. Restart the service to clear it.
