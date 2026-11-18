# logi-switch-sync

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)
![Python](https://img.shields.io/badge/python-3.8%2B-blue)
[![Release](https://img.shields.io/github/v/release/burakatmaca/logi-switch-sync)](https://github.com/burakatmaca/logi-switch-sync/releases/latest)

**Make your Logitech mouse follow your keyboard when you press an Easy-Switch key — a lightweight Logitech Flow alternative for macOS and Windows, with optional clipboard sync.**

You press **1**, **2** or **3** on your MX Keys, and your MX Master switches to the same computer automatically. No pointer-at-the-screen-edge tricks, no Logitech Options+ Flow, no flaky network discovery.

> Tested with **MX Keys Mini + MX Master 3S** on a **Logi Bolt** receiver, between a MacBook (macOS) and a Windows PC — running side by side with Logitech Options+.

---

## Why?

Logitech Flow is supposed to move your mouse and keyboard between computers, but it relies on both machines finding each other over the network. VPNs, firewalls, different subnets/VLANs, sleep/wake and Options+ updates regularly break it — the pointer just won't cross over.

The Easy-Switch keys on the keyboard, on the other hand, always work. They just only switch the **keyboard**. `logi-switch-sync` closes that gap:

- **Mouse follows the keyboard** — press an Easy-Switch key and the mouse comes along.
- **No network needed for switching** — commands go straight to the devices through the USB receiver.
- **Optional clipboard sync** — copy text on one computer, switch, paste on the other.
- **Runs in the background** — launchd agent on macOS, Task Scheduler on Windows, auto-restarts on errors.
- **Tiny** — one Python file, one dependency (`hidapi`), MIT licensed.

## How it works

Logitech receivers and devices speak **HID++ 2.0**. When you press an Easy-Switch key, the keyboard disconnects from the receiver on the computer you are leaving, and that receiver sends a *device disconnected* notification. `logi-switch-sync` listens for that notification and immediately sends the mouse a **`CHANGE_HOST` (feature `0x1814`)** command, which is exactly what the mouse's own channel button does.

```
 Computer A (channel 1)                         Computer B (channel 2)
 ┌──────────────────────┐                       ┌──────────────────────┐
 │ press "2" on keyboard│                       │                      │
 │ receiver: kbd left ──┼─► CHANGE_HOST(2) ─► mouse jumps to B ──────► │
 │ clipboard text ──────┼──── TCP 47821 (optional) ──────────────────► │
 └──────────────────────┘                       └──────────────────────┘
```

Each computer runs its own copy and only needs to know **which channel the other computer is on**.

## Supported hardware

| Setup | Status |
|---|---|
| Logi **Bolt** receiver (046D:C548) on both computers | ✅ Tested |
| MX Keys Mini, MX Master 3S | ✅ Tested |
| Other Bolt devices with Easy-Switch (MX Keys, MX Keys S, MX Master 3, MX Anywhere 3/3S, ERGO, …) | 🟡 Should work — they expose `CHANGE_HOST` |
| **Unifying** receiver (046D:C52B / C532) | 🧪 Experimental |
| Bluetooth (no receiver) | ❌ Not supported (no disconnect notification to listen to) |

Run `python logi_switch.py list` to see whether your devices are detected. Please open an issue with the output if your device works (or doesn't)!

**Requirements:** Python 3.8+, both keyboard and mouse paired to the **same channel numbers** on each computer (keyboard channel 1 = mouse channel 1 = Computer A, and so on).

## Quick start

Download the **[latest release](https://github.com/burakatmaca/logi-switch-sync/releases/latest)** (Source code .zip) on **both** computers, or clone it:

```bash
git clone https://github.com/burakatmaca/logi-switch-sync.git
```

### macOS

```bash
cd logi-switch-sync
bash macos/install.sh
```

### Windows

Double-click **`windows\install.cmd`** (it asks for administrator rights), or in an elevated PowerShell:

```powershell
cd logi-switch-sync
powershell -ExecutionPolicy Bypass -File .\windows\install.ps1
```

The installer will:

1. create a private Python environment and install `hidapi`,
2. list the devices it can see on the receiver,
3. ask **which channel the mouse should follow to** when you leave this computer (the *other* computer's channel),
4. optionally set up clipboard sync (other computer's IP + a shared secret it can generate for you),
5. start the background service and show the log.

Install on the first computer, note the **IP and shared secret** it prints, then install on the second computer using those values.

**Example for two computers:**

| | Mac (channel 1) | Windows PC (channel 2) |
|---|---|---|
| Mouse follows to | `2` | `1` |
| Clipboard peer | PC's IP | Mac's IP |
| Shared secret | same | same |

## Command-line usage

```bash
python logi_switch.py list                     # devices, feature indexes, current channel
python logi_switch.py switch 2                 # move every connected device to channel 2 now
python logi_switch.py switch 2 --only mouse    # move only the mouse
python logi_switch.py follow --to 2            # mouse follows keyboard to channel 2 (foreground)
python logi_switch.py follow --to 2 --peer 192.168.1.20 --secret s3cr3t   # + clipboard
python logi_switch.py follow --config config.json --log follow.log --forever
```

`switch` is handy for binding to a hotkey or a mouse button if you prefer a single key that moves everything.

### Config file

```json
{
  "to": 2,
  "peer": "192.168.1.20",
  "secret": "0123456789abcdef0123456789abcdef",
  "port": 47821,
  "delay": 0.3
}
```

| Key | Meaning |
|---|---|
| `to` | Channel (1–3) the mouse moves to when the keyboard leaves this computer. **Required.** |
| `peer` | IP or hostname of the other computer. Enables clipboard sync. |
| `secret` | Shared secret; must be identical on both computers. |
| `port` | TCP port for clipboard sync (default `47821`). |
| `delay` | Seconds to wait after the keyboard leaves before moving the mouse (default `0.3`). |

Installed files live in `~/.logi-switch-sync/` (macOS) and `%LOCALAPPDATA%\logi-switch-sync\` (Windows).

## Clipboard sync & security

- Only **plain text** is synced (images and files are not, yet).
- Text is sent **once, at the moment you switch away**, from the computer you leave to the one you go to.
- The connection is a plain TCP socket on your LAN. The shared secret prevents others on the network from **writing** to your clipboard, but the traffic itself is **not encrypted**. Use it on trusted home/office networks, not on public Wi-Fi.
- Nothing is sent anywhere else; there is no cloud service and no telemetry.

## Troubleshooting

**"No device is connected to this receiver right now"**
The keyboard/mouse are on another channel or asleep. Bring them to this computer, move the mouse, press a key, and run `list` again. Sleeping devices are woken with a ping automatically, but a device that is on another channel cannot be reached.

**macOS: "Could not open the receiver"**
Give the Python binary (or your terminal) permission under *System Settings → Privacy & Security → Input Monitoring*, then restart the agent:
`launchctl kickstart -k gui/$(id -u)/io.github.logi-switch-sync`

**The mouse sometimes doesn't follow**
If the mouse was in deep sleep it may need a nudge. Check the log — `FAILED` means the mouse did not answer.

**The mouse jumps when I didn't press anything**
Turning the keyboard off (or a very deep sleep on some models) also looks like "keyboard left". Please open an issue with your log lines and device model.

**Clipboard: "could not reach peer"**
Check the IP (give both computers a fixed IP / DHCP reservation), and that the receiving side allows TCP `47821`. On Windows, the installer adds a firewall rule for *Private* networks — if your network is marked *Public*, switch it to Private. On macOS, click **Allow** when asked about incoming connections.

**Should I keep Logitech Options+?**
Yes, Options+ can stay installed for button customisation. Turn **Flow off** to avoid two tools fighting over the devices.

**macOS: Options+ shows my devices as disconnected while the tool runs**
By default `hidapi` opens HID devices *exclusively* on macOS, which locks Options+ out of the receiver. Since v1.0.1 the tool switches hidapi to shared mode at start-up, so both can talk to the receiver at the same time. If you still see it, update (`pip install -U hidapi`) and check the log for a "shared mode" warning.

**Logs**

- macOS: `tail -f ~/.logi-switch-sync/follow.log`
- Windows: `Get-Content "$env:LOCALAPPDATA\logi-switch-sync\follow.log" -Wait`

## Uninstall

- macOS: `./macos/uninstall.sh`
- Windows: double-click `windows\uninstall.cmd`

## FAQ

**Does this work with three computers?**
Switching works, but each computer sends the mouse to one fixed channel (`to`), so with three computers the mouse may land on the wrong one. Two computers is the sweet spot for now — contributions welcome.

**Does it need Logitech Options+ or Logi Options?**
No. It talks to the receiver directly.

**Does it work over Bluetooth?**
No — it needs a Bolt (or Unifying) receiver on each computer.

**Linux?**
The HID++ part is portable; the installer and clipboard backend are not written yet. PRs welcome.

## Contributing

Issues and pull requests are welcome — especially `list` output from other Logitech devices, Linux support, three-computer support, and image/file clipboard sync.

## License

[MIT](LICENSE)

## Disclaimer

This project is not affiliated with, endorsed by, or sponsored by Logitech. Logitech, Logi, MX Master, MX Keys, Logi Bolt, Unifying, Easy-Switch, Flow and Options+ are trademarks of Logitech. Protocol details are based on publicly available community documentation of HID++ (e.g. the Solaar project).

---

<sub>Keywords: Logitech Flow alternative, Logitech Flow not working, Easy-Switch mouse and keyboard together, MX Master 3S follow MX Keys, switch Logitech mouse and keyboard at the same time, Logi Bolt multi-computer, HID++ CHANGE_HOST, share clipboard between Mac and Windows.</sub>
