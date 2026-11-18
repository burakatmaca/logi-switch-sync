# Changelog

## v1.0.1 — 2026-11-18

- **macOS:** open the receiver in shared (non-exclusive) mode, so Logitech Options+ keeps working while logi-switch-sync runs. `hidapi` opens devices exclusively on macOS by default, which made Options+ show the devices as disconnected.

## v1.0.0 — 2026-10-20

First public release.

- Mouse follows the keyboard when an Easy-Switch key is pressed (HID++ `CHANGE_HOST`, Logi Bolt receiver).
- Optional clipboard text sync between the two computers over the LAN, protected by a shared secret.
- `list`, `switch` and `follow` commands; JSON config file; auto-retry background mode.
- One-step installers: launchd agent on macOS, Task Scheduler task on Windows.
