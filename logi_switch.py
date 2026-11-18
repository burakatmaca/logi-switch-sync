#!/usr/bin/env python3
"""
logi-switch-sync
================

Make your Logitech mouse follow your keyboard when you press an Easy-Switch
(1-2-3) key - and optionally carry your clipboard text along. A lightweight,
network-optional alternative to Logitech Flow for macOS and Windows.

It talks HID++ 2.0 to a Logitech Bolt (or Unifying) receiver:
  * listens for the receiver's "device disconnected" notification for the keyboard
    (that is what happens when you press an Easy-Switch key), then
  * sends CHANGE_HOST (feature 0x1814) to the mouse so it jumps to the same computer.

Commands:
  python logi_switch.py list                      show paired devices on the receiver
  python logi_switch.py switch 2                  move ALL connected devices to channel 2
  python logi_switch.py switch 2 --only mouse     move only the mouse
  python logi_switch.py follow --to 2             mouse follows the keyboard to channel 2
  python logi_switch.py follow --config config.json --log follow.log --forever

Channels are numbered 1-3, exactly like the keys on the keyboard.

License: MIT. Not affiliated with or endorsed by Logitech.
"""
import argparse
import hmac
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time

__version__ = "1.0.1"

# Redirected output on Windows defaults to cp1252; force UTF-8 so logs never crash.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import hid
except ImportError:
    sys.exit("The 'hidapi' package is missing. Install it with:  pip install hidapi")


def _macos_open_shared():
    """On macOS, hidapi opens devices EXCLUSIVELY by default (kIOHIDOptionsTypeSeizeDevice),
    which makes Logitech Options+ lose the receiver while we run. The Python binding does not
    expose the switch, so call hidapi's C function directly. hid_init() already ran when the
    module was imported, so this setting sticks for every later open."""
    if sys.platform != "darwin":
        return True
    try:
        import ctypes
        lib = ctypes.CDLL(hid.__file__)
        lib.hid_darwin_set_open_exclusive.argtypes = [ctypes.c_int]
        lib.hid_darwin_set_open_exclusive.restype = None
        lib.hid_darwin_set_open_exclusive(0)
        lib.hid_darwin_get_open_exclusive.restype = ctypes.c_int
        return lib.hid_darwin_get_open_exclusive() == 0
    except (OSError, AttributeError):
        return False


MACOS_SHARED_OK = _macos_open_shared()

LOGI_VID = 0x046D
RECEIVER_PIDS = {
    0xC548: "Bolt",
    0xC52B: "Unifying",   # experimental: should work for HID++ 2.0 devices with CHANGE_HOST
    0xC532: "Unifying",
}
SHORT, LONG = 0x10, 0x11
LONG_LEN = 20

FEAT_ROOT = 0x0000
FEAT_NAME = 0x0005
FEAT_CHANGE_HOST = 0x1814

DEVICE_TYPES = {
    0: "keyboard", 1: "remote", 2: "numpad", 3: "mouse",
    4: "trackpad", 5: "trackball", 6: "presenter", 7: "receiver",
}

CLIP_PORT = 47821
CLIP_MAX = 10 * 1024 * 1024  # 10 MB of text

LOG_FILE = None


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    if LOG_FILE:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    if sys.stdout is not None:  # None under pythonw.exe
        print(line, flush=True)


class ReceiverNotFound(Exception):
    pass


# --- HID++ receiver ---------------------------------------------------------
class Receiver:
    def __init__(self):
        infos = [
            d for d in hid.enumerate(LOGI_VID, 0)
            if d["product_id"] in RECEIVER_PIDS and d["usage_page"] == 0xFF00
        ]
        if not infos:
            raise ReceiverNotFound(
                "No Logitech Bolt/Unifying receiver found (vendor usage page 0xFF00).")

        self.kind = RECEIVER_PIDS[infos[0]["product_id"]]
        opened = {}
        self.by_usage = {}
        for info in infos:
            path = info["path"]
            if path not in opened:
                dev = hid.device()
                try:
                    dev.open_path(path)
                except OSError as e:
                    raise ReceiverNotFound(
                        f"Could not open the receiver ({e}). On macOS, grant 'Input Monitoring' "
                        f"permission to your terminal / Python, and make sure no other app holds "
                        f"the device exclusively.")
                dev.set_nonblocking(True)
                opened[path] = dev
            self.by_usage[info["usage"]] = opened[path]
        self.handles = list(opened.values())
        self.pending = []  # notifications that arrived while waiting for a reply
        self._swid = 0

    def close(self):
        for h in self.handles:
            try:
                h.close()
            except Exception:
                pass

    def next_swid(self):
        # A different software ID (1-15) per request, so late replies never match the wrong request.
        self._swid = self._swid % 15 + 1
        return self._swid

    # --- low level -----------------------------------------------------------
    def write(self, report):
        # On Windows, short (0x10) and long (0x11) reports live in separate HID collections.
        usage = 0x0001 if report[0] == SHORT else 0x0002
        dev = self.by_usage.get(usage) or self.handles[0]
        dev.write(list(report))

    def read(self, timeout):
        end = time.monotonic() + timeout
        while True:
            for h in self.handles:
                data = h.read(64)
                if data:
                    return list(data)
            if time.monotonic() >= end:
                return None
            time.sleep(0.002)

    def request(self, dev_idx, feat_idx, func, params=(), timeout=0.4):
        """Send a HID++ 2.0 request and return the reply parameters, or None on error/timeout."""
        fn = (func << 4) | self.next_swid()
        report = [LONG, dev_idx, feat_idx, fn] + list(params)
        report += [0] * (LONG_LEN - len(report))
        self.write(report)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            data = self.read(end - time.monotonic())
            if not data:
                return None
            if data[0] in (SHORT, LONG) and data[1] == dev_idx:
                if data[2] == feat_idx and data[3] == fn:
                    return data[4:]
                if data[2] in (0xFF, 0x8F) and data[3] == feat_idx and data[4] == fn:  # error reply
                    return None
                if data[3] & 0x0F:  # late reply to an older request: drop it
                    continue
            self.pending.append(data)
        return None

    # --- HID++ helpers -------------------------------------------------------
    def feature_index(self, dev_idx, feature, timeout=0.4):
        r = self.request(dev_idx, 0x00, 0, [feature >> 8, feature & 0xFF], timeout)
        if r and r[0]:
            return r[0]
        return None

    def wake(self, dev_idx, tries=3, timeout=1.0):
        """Root-feature ping; wakes a sleeping device. True if it answers."""
        for _ in range(tries):
            if self.request(dev_idx, 0x00, 1, [0, 0, 0x5A], timeout) is not None:
                return True
            time.sleep(0.1)
        return False

    def device_info(self, dev_idx):
        if not self.wake(dev_idx):
            return None
        ch = self.feature_index(dev_idx, FEAT_CHANGE_HOST)
        if ch is None:
            return None
        info = {"index": dev_idx, "change_host": ch, "name": "?", "type": "?",
                "hosts": None, "current": None}
        r = self.request(dev_idx, ch, 0)  # getHostInfo
        if r:
            info["hosts"], info["current"] = r[0], r[1] + 1
        ni = self.feature_index(dev_idx, FEAT_NAME)
        if ni:
            r = self.request(dev_idx, ni, 0)
            if r:
                length, name = r[0], ""
                while len(name) < length:
                    chunk = self.request(dev_idx, ni, 1, [len(name)])
                    if not chunk:
                        break
                    name += bytes(chunk).split(b"\x00")[0].decode("utf-8", "replace")
                    if not chunk[-1]:
                        break
                info["name"] = name[:length]
            r = self.request(dev_idx, ni, 2)
            if r:
                info["type"] = DEVICE_TYPES.get(r[0], str(r[0]))
        return info

    def scan(self):
        return [i for i in (self.device_info(n) for n in range(1, 7)) if i]

    def set_host(self, dev, host, retries=3):
        """host: 1-3. The device switches immediately, so no reply is expected."""
        for _ in range(retries):
            if not self.wake(dev["index"], tries=1):
                continue
            fn = (1 << 4) | self.next_swid()
            report = [LONG, dev["index"], dev["change_host"], fn, host - 1]
            report += [0] * (LONG_LEN - len(report))
            self.write(report)
            return True
        return False

    def enable_connection_notifications(self):
        # Read receiver register 0x00, set the "wireless notifications" bit, write it back.
        self.write([SHORT, 0xFF, 0x81, 0x00, 0, 0, 0])
        flags = [0, 0, 0]
        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            d = self.read(end - time.monotonic())
            if d and d[:4] == [SHORT, 0xFF, 0x81, 0x00]:
                flags = d[4:7]
                break
        flags[1] |= 0x01
        self.write([SHORT, 0xFF, 0x80, 0x00] + flags)


# --- clipboard --------------------------------------------------------------
def _clipboard_backend():
    if sys.platform == "darwin":
        env = dict(os.environ, LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")

        def get():
            r = subprocess.run(["pbpaste"], capture_output=True, env=env, timeout=3)
            return r.stdout.decode("utf-8", "replace")

        def put(text):
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), env=env, timeout=3)
            return True
        return get, put

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u32.OpenClipboard.argtypes = [wintypes.HWND]
        u32.OpenClipboard.restype = wintypes.BOOL
        u32.CloseClipboard.restype = wintypes.BOOL
        u32.EmptyClipboard.restype = wintypes.BOOL
        u32.GetClipboardData.argtypes = [wintypes.UINT]
        u32.GetClipboardData.restype = wintypes.HANDLE
        u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u32.SetClipboardData.restype = wintypes.HANDLE
        k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k32.GlobalAlloc.restype = wintypes.HANDLE
        k32.GlobalLock.argtypes = [wintypes.HANDLE]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalUnlock.argtypes = [wintypes.HANDLE]
        k32.GlobalUnlock.restype = wintypes.BOOL
        k32.GlobalFree.argtypes = [wintypes.HANDLE]
        k32.GlobalFree.restype = wintypes.HANDLE
        CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002

        def _open():
            for _ in range(20):  # another app may be holding the clipboard
                if u32.OpenClipboard(None):
                    return True
                time.sleep(0.05)
            return False

        def get():
            if not _open():
                return None
            try:
                h = u32.GetClipboardData(CF_UNICODETEXT)
                if not h:
                    return ""
                ptr = k32.GlobalLock(h)
                if not ptr:
                    return ""
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    k32.GlobalUnlock(h)
            finally:
                u32.CloseClipboard()

        def put(text):
            data = text.encode("utf-16-le") + b"\x00\x00"
            if not _open():
                return False
            try:
                u32.EmptyClipboard()
                h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not h:
                    return False
                ptr = k32.GlobalLock(h)
                ctypes.memmove(ptr, data, len(data))
                k32.GlobalUnlock(h)
                if not u32.SetClipboardData(CF_UNICODETEXT, h):
                    k32.GlobalFree(h)
                    return False
                return True  # on success Windows owns the memory
            finally:
                u32.CloseClipboard()
        return get, put

    # other platforms / tests: in-memory clipboard
    store = {"text": ""}
    return (lambda: store["text"]), (lambda t: store.update(text=t) or True)


clip_get, clip_put = _clipboard_backend()


def _recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf


def send_clipboard(peer, port, secret):
    try:
        text = clip_get()
    except Exception as e:
        return f"could not read clipboard ({e})"
    if not text:
        return "clipboard empty or not text, nothing sent"
    payload = json.dumps({"secret": secret, "text": text}).encode("utf-8")
    if len(payload) > CLIP_MAX:
        return "clipboard too large, nothing sent"
    try:
        with socket.create_connection((peer, port), timeout=2) as s:
            s.sendall(struct.pack(">I", len(payload)) + payload)
            ok = s.recv(2) == b"OK"
    except OSError as e:
        return f"could not reach peer {peer}:{port} ({e})"
    return f"sent {len(text)} characters" if ok else "peer rejected it (wrong secret?)"


def _handle_clip(conn, addr, secret):
    with conn:
        try:
            conn.settimeout(5)
            (length,) = struct.unpack(">I", _recv_exact(conn, 4))
            if length > CLIP_MAX:
                return
            msg = json.loads(_recv_exact(conn, length).decode("utf-8"))
            if not hmac.compare_digest(str(msg.get("secret", "")), secret):
                conn.sendall(b"NO")
                log(f"clipboard: rejected {addr[0]} (wrong secret)")
                return
            text = msg.get("text", "")
            ok = clip_put(text)
            conn.sendall(b"OK" if ok else b"NO")
            log(f"clipboard: received {len(text)} characters from {addr[0]}")
        except Exception as e:
            log(f"clipboard: receive error ({e})")


def serve_clipboard(port, secret):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("0.0.0.0", port))
    except OSError as e:
        log(f"clipboard: cannot listen on port {port} ({e}); is another instance running? "
            f"Receiving is disabled, sending still works.")
        return
    srv.listen(4)
    log(f"clipboard: listening on TCP port {port}")
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=_handle_clip, args=(conn, addr, secret), daemon=True).start()


# --- commands ---------------------------------------------------------------
def cmd_list(args):
    rx = Receiver()
    devs = rx.scan()
    print(f"Receiver: Logitech {rx.kind}")
    if not devs:
        print("No device is connected to this receiver right now "
              "(they may be on another channel, or asleep - move the mouse / press a key).")
    for d in devs:
        print(f"[{d['index']}] {d['name']:<20} type={d['type']:<9} "
              f"CHANGE_HOST=0x{d['change_host']:02X}  channel={d['current']}/{d['hosts']}")


def cmd_switch(args):
    rx = Receiver()
    devs = rx.scan()
    if args.only:
        devs = [d for d in devs if d["type"] == args.only]
    if not devs:
        sys.exit("No connected device to switch.")
    devs.sort(key=lambda d: d["type"] != "mouse")  # mouse first, keyboard last
    for d in devs:
        ok = rx.set_host(d, args.host)
        print(f"{d['name']} -> channel {args.host}: {'sent' if ok else 'FAILED (asleep?)'}")


def follow_once(args):
    rx = Receiver()
    try:
        devs = rx.scan()
        keyboards = {d["index"] for d in devs if d["type"] == "keyboard"}
        mice = {d["index"]: d for d in devs if d["type"] == "mouse"}
        if not keyboards:
            raise RuntimeError("keyboard is not connected to this computer right now "
                               "(switch it here; will retry)")
        if not mice:
            log("warning: no mouse connected right now; will pick it up when it comes back")
        rx.enable_connection_notifications()
        log(f"watching: when the keyboard leaves, the mouse goes to channel {args.to}"
            + (f", clipboard goes to {args.peer}" if args.peer else ""))

        while True:
            data = rx.pending.pop(0) if rx.pending else rx.read(1.0)
            if not data or data[0] != SHORT or data[2] != 0x41:
                continue
            idx, flags = data[1], data[4]
            connected = not (flags & 0x40)

            if idx in keyboards and not connected:
                time.sleep(args.delay)
                for m in mice.values():
                    ok = rx.set_host(m, args.to)
                    log(f"keyboard left -> {m['name']} to channel {args.to}: "
                        f"{'OK' if ok else 'FAILED'}")
                if args.peer:
                    log("clipboard: " + send_clipboard(args.peer, args.port, args.secret))
            elif connected:
                info = rx.device_info(idx)  # a device came back: refresh its details
                if info and info["type"] == "mouse":
                    mice[idx] = info
                elif info and info["type"] == "keyboard":
                    keyboards.add(idx)
    finally:
        rx.close()


def cmd_follow(args):
    if args.peer and not args.secret:
        sys.exit("--peer requires --secret (the same value on both computers).")
    if not MACOS_SHARED_OK:
        log("warning: could not switch hidapi to shared mode; Logitech Options+ may lose the "
            "receiver while this runs (upgrade with: pip install -U hidapi)")
    if args.peer:
        threading.Thread(target=serve_clipboard, args=(args.port, args.secret),
                         daemon=True).start()
    while True:
        try:
            follow_once(args)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # receiver unplugged, sleep/wake, keyboard elsewhere...
            if not args.forever:
                sys.exit(f"error: {e}")
            log(f"{e} - retrying in {args.retry}s")
            time.sleep(args.retry)


def load_config(path):
    with open(path, encoding="utf-8-sig") as f:  # -sig: tolerate the BOM PowerShell 5 writes
        return json.load(f)


def main():
    global LOG_FILE
    p = argparse.ArgumentParser(
        description="Make your Logitech mouse follow your keyboard's Easy-Switch key "
                    "(Logitech Flow alternative).")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="show devices paired to the receiver")

    s = sub.add_parser("switch", help="move connected devices to a channel now")
    s.add_argument("host", type=int, choices=[1, 2, 3])
    s.add_argument("--only", choices=["mouse", "keyboard"])

    f = sub.add_parser("follow", help="mouse (and clipboard) follow the keyboard")
    f.add_argument("--config", help="JSON file with to/peer/secret/port/delay")
    f.add_argument("--to", type=int, choices=[1, 2, 3],
                   help="channel the mouse should go to when the keyboard leaves this computer")
    f.add_argument("--peer", help="IP/hostname of the other computer, enables clipboard sync")
    f.add_argument("--secret", help="shared secret for clipboard sync (same on both computers)")
    f.add_argument("--port", type=int, help=f"clipboard TCP port (default {CLIP_PORT})")
    f.add_argument("--delay", type=float, help="seconds to wait before moving the mouse (default 0.3)")
    f.add_argument("--log", help="also append log lines to this file")
    f.add_argument("--forever", action="store_true",
                   help="never exit; retry on errors (for background services)")
    f.add_argument("--retry", type=float, default=10, help="seconds between retries (default 10)")

    args = p.parse_args()

    if args.cmd == "follow":
        cfg = load_config(args.config) if args.config else {}
        for key, default in (("to", None), ("peer", None), ("secret", None),
                             ("port", CLIP_PORT), ("delay", 0.3), ("log", None)):
            if getattr(args, key) is None:
                setattr(args, key, cfg.get(key, default))
        if args.to not in (1, 2, 3):
            p.error("follow needs --to 1|2|3 (or 'to' in the config file)")
        LOG_FILE = args.log

    try:
        {"list": cmd_list, "switch": cmd_switch, "follow": cmd_follow}[args.cmd](args)
    except ReceiverNotFound as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
