# Keyboard mouse for Ubuntu 24.04 Wayland

This is the native Wayland equivalent of the AutoHotkey script. It reads a physical keyboard with `evdev`, suppresses mouse-mode keys, and emits a virtual keyboard and mouse with Linux `uinput`.

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

Ctrl+A passes through to the active application. The tray indicator is green when mouse mode is on and gray when it is off.

## Install dependencies

```bash
sudo apt update
sudo apt install python3-evdev python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1
```

Ubuntu 24.04 provides `python3-evdev` and Ayatana AppIndicator packages in its repositories. The implementation follows the Linux `uinput` interface for virtual input devices.

## Grant input access

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

Log out and back in so the new group membership takes effect.

## Test interactively

```bash
python3 keyboard_mouse_wayland.py
```

The program automatically grabs every accessible device that looks like a full keyboard. If detection selects the wrong device, list stable keyboard paths:

```bash
ls -l /dev/input/by-id/*-event-kbd
```

Then set `KEYBOARD_DEVICE_PATHS` near the top of `keyboard_mouse_wayland.py`.

Stop the program from its tray menu or press Ctrl+C in its terminal. Linux releases grabbed devices when the process exits.

## Start automatically

```bash
mkdir -p ~/.local/share/keyboard-mouse ~/.config/systemd/user
cp keyboard_mouse_wayland.py mouse-mode-on.svg mouse-mode-off.svg ~/.local/share/keyboard-mouse/
cp keyboard-mouse-wayland.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now keyboard-mouse-wayland.service
```

Inspect logs with:

```bash
journalctl --user -u keyboard-mouse-wayland.service -f
```

## Configuration

Edit the values near the top of `keyboard_mouse_wayland.py`, then restart the program.

- `KEYS` contains every binding.
- `SPEEDS` contains cursor and scrolling distances and intervals.
- `SMOOTH_SCROLL_ENABLED` chooses high-resolution or stepped scrolling.
- `KEYBOARD_DEVICE_PATHS` overrides automatic device detection.

Smooth scrolling depends on GNOME, libinput, and the active application's support for high-resolution wheel input.
