#!/usr/bin/env python3
"""DM40 multimeter client for the agent skill.

Talks SCPI to an Alientek DM40 (A/B/C) running opendm40, over USBTMC
(VID:PID 1209:D040) through libusb. No MCP, no daemon: one shot per call.

Safety policy (hard-coded, not configurable):
  * read-only measurement surface: voltage, resistance, continuity, diode,
    capacitance, frequency, temperature, scope and file *reads*;
  * current functions are refused (an accidental A-range left on the meter is
    how fuses and shunts die);
  * generator output, calibration, firmware update, flash/DBG commands and
    any write to the meter's disk are refused before a byte reaches the meter;
  * the worst a caller can do is change the displayed function or the scope
    settings, both restored by the next call.

Usage:
  dm40.py idn
  dm40.py status
  dm40.py read [-n N] [--func dcv] [--interval S]
  dm40.py measure res
  dm40.py scope [--points screen|max|N] [--format word|byte] [-o wave.csv]
  dm40.py raw 'CONF:VOLT:DC;READ?'
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import time

VID_PID = ((0x1209, 0xD040), (0x1209, 0x0001))

# --------------------------------------------------------------------------
# safety
# --------------------------------------------------------------------------

DENY = re.compile(
    r"\bCURR|"
    r"\bPOW(ER)?\b|"          # power meter drives the current path
    r"\bDBG|"
    r"\bCAL|CALIB|"
    r"UPDATE|UPLOAD|RECOVER|FLASH|LOADER|"
    r"\bSOUR|OUTP|"
    r"MMEM:(DEL|COPY|MOVE|LOAD|SAVE|STORE|UPLOAD)|"
    r"\bLOG|"                 # logging writes the disk; not needed to measure
    r"\*RST|\*CLS|SYST:PRES|FACT|"
    r"\bDIAG|"
    r"\.BIN\b|DM40_"
)

# file names on the meter's disk are not commands; do not scan them for DENY
PATH_TOKEN = re.compile(r"[A-Z0-9_.\-/\\]+\.(CSV|BMP|DLG|TXT|JPG)", re.I)

ALLOW_HEADER = (
    "*IDN", "SYST:ERR", "SYST:ERR:COUN",
    "FUNC", "CONF",
    "CONF:VOLT", "CONF:RES", "CONF:FRES", "CONF:CONT", "CONF:DIOD",
    "CONF:CAP", "CONF:FREQ", "CONF:TEMP",
    "MEAS:VOLT", "MEAS:RES", "MEAS:FRES", "MEAS:CONT", "MEAS:DIOD",
    "MEAS:CAP", "MEAS:FREQ", "MEAS:TEMP",
    "READ", "FETC", "INIT",
    "SENS:FUNC", "SENS:VOLT", "SENS:RES", "SENS:CAP", "SENS:FREQ", "SENS:RATE",
    "WAV:SOUR", "WAV:FORM", "WAV:POIN", "WAV:PRE", "WAV:DATA",
    "ACQ:SRAT", "TIM:SCAL", "TIM:MAIN:SCAL",
    "CHAN1:SCAL", "CHAN1:COUP", "CHAN1:OFFS",
    "TRIG:STAT", "TRIG:LEV", "TRIG:SLOP", "TRIG:SWE", "TRIG:SOUR",
    "RUN", "STOP", "SING", "DIG",
    "MMEM:CAT", "MMEM:DATA",
    "DISP", "DBG:LCD",
)


class SafetyError(SystemExit):
    pass


def guard(cmd: str) -> str:
    """Return the command, or exit if it is outside the read-only policy."""
    text = cmd.strip()
    if not text:
        raise SafetyError("dm40 safety: empty command")
    if len(text.encode()) > 256:
        raise SafetyError("dm40 safety: message over 256 bytes (SCPI limit)")
    for part in text.split(";"):
        p = part.strip()
        if not p:
            continue
        up = p.upper()
        scan = PATH_TOKEN.sub(" ", up)
        # the only DBG command allowed: read the panel's framebuffer
        if up.startswith("DBG:LCD"):
            continue
        if DENY.search(scan):
            raise SafetyError(f"dm40 safety: refused (deny list): {p!r}")
        head = re.split(r"[\s?]", up, maxsplit=1)[0]
        if not any(head == a or head.startswith(a + ":") or head.startswith(a)
                   for a in ALLOW_HEADER):
            raise SafetyError(
                f"dm40 safety: refused (not in read-only allow list): {p!r}")
    return text


# --------------------------------------------------------------------------
# USBTMC over libusb
# --------------------------------------------------------------------------

def _backend():
    import usb.backend.libusb1 as l1

    for path in (
        "/opt/homebrew/lib/libusb-1.0.dylib",
        "/usr/local/lib/libusb-1.0.dylib",
        "/opt/homebrew/lib/libusb-1.0.so.0",
        "/usr/lib/x86_64-linux-gnu/libusb-1.0.so.0",
    ):
        if os.path.exists(path):
            be = l1.get_backend(find_library=lambda _n, p=path: p)
            if be is not None:
                return be
    return l1.get_backend()


class USBTMC:
    """Minimal USBTMC 1.0 host: one bulk pair, 12-byte headers."""

    def __init__(self, timeout: float = 5.0, serial: str | None = None):
        import usb.core
        import usb.util

        self.usb = usb
        self.timeout = timeout
        self.tag = 0
        self._rx = bytearray()
        self.dev = None
        for vid, pid in VID_PID:
            for dev in usb.core.find(find_all=True, idVendor=vid, idProduct=pid,
                                     backend=_backend()):
                if serial and getattr(dev, "serial_number", "") != serial:
                    continue
                self.dev = dev
                break
            if self.dev:
                break
        if self.dev is None:
            raise SystemExit(
                "dm40: no USBTMC device 1209:D040 found.\n"
                "  - meter on and awake?\n"
                "  - a `28e9:028f ATK DM40` device means the official loader is up\n"
                "  - `ioreg -p IOUSB -w0 | grep -i dm40` should show it"
            )
        dev = self.dev
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]
        try:
            usb.util.claim_interface(dev, intf)
        except usb.core.USBError as exc:
            raise SystemExit(f"dm40: cannot claim USBTMC interface: {exc}")
        self.intf = intf
        self.ep_in = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress) == usb.util.ENDPOINT_IN)
        self.ep_out = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
        if self.ep_in is None or self.ep_out is None:
            raise SystemExit("dm40: USBTMC interface has no bulk endpoints")

    # -- framing ---------------------------------------------------------

    def _next_tag(self) -> int:
        self.tag = self.tag % 255 + 1
        return self.tag

    def _clear(self) -> None:
        for req, ln in ((5, 2), (6, 1)):
            try:
                self.dev.ctrl_transfer(0xA1, req, 0, 0, ln, timeout=500)
            except Exception:
                pass
        self._rx.clear()

    def _more(self, timeout: float) -> bool:
        """Append one bulk-in chunk to the receive buffer."""
        try:
            chunk = self.dev.read(self.ep_in, 65536, timeout=int(timeout * 1000))
        except self.usb.core.USBTimeoutError:
            return False
        if not chunk:
            return False
        self._rx.extend(chunk)
        return True

    def _fill(self, n: int, timeout: float) -> None:
        deadline = time.time() + timeout
        while len(self._rx) < n:
            if not self._more(max(0.05, deadline - time.time())):
                if time.time() >= deadline:
                    raise TimeoutError(f"dm40: timeout, {len(self._rx)}/{n} bytes")

    def take(self, n: int, timeout: float | None = None) -> bytes:
        self._fill(n, self.timeout if timeout is None else timeout)
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    def read_block(self, cmd: str, timeout: float = 30.0,
                   max_len: int = 1 << 21) -> bytes:
        """IEEE 488.2 block query; the reply may span several USB messages."""
        guard(cmd)
        old = self.timeout
        self.timeout = timeout
        try:
            self._write(cmd.strip().encode("ascii") + b"\n")
            buf = bytearray()
            deadline = time.time() + timeout
            while time.time() < deadline:
                buf += self._read_msg(max_len)
                if len(buf) >= 2 and buf[:1] == b"#":
                    ndig = int(chr(buf[1]))
                    if len(buf) >= 2 + ndig:
                        length = int(buf[2:2 + ndig])
                        if len(buf) >= 2 + ndig + length:
                            return bytes(buf[2 + ndig:2 + ndig + length])
                elif len(buf) >= 1 and buf[:1] != b"#":
                    raise RuntimeError(f"dm40: no IEEE block, got {bytes(buf[:16])!r}")
                if len(buf) > (1 << 21):
                    raise RuntimeError("dm40: block larger than 2 MiB")
            raise TimeoutError("dm40: block not complete in time")
        finally:
            self.timeout = old

    def _write(self, data: bytes) -> None:
        tag = self._next_tag()
        pad = (-len(data)) % 4
        hdr = struct.pack("<BBBxIBxxx", 1, tag, tag ^ 0xFF, len(data), 0x01)
        self.dev.write(self.ep_out, hdr + data + b"\x00" * pad,
                       timeout=int(self.timeout * 1000))

    def _request_in(self, max_len: int = 1 << 20) -> None:
        """Send REQUEST_DEV_DEP_MSG_IN; a USBTMC device only answers after it."""
        tag = self._next_tag()
        hdr = struct.pack("<BBBxIBxxx", 2, tag, tag ^ 0xFF, max_len, 0x00)
        self.dev.write(self.ep_out, hdr, timeout=int(self.timeout * 1000))

    def _read_msg(self, max_len: int = 1 << 20) -> bytes:
        self._request_in(max_len)

        deadline = time.time() + self.timeout
        while len(self._rx) < 12:
            if not self._more(max(0.05, deadline - time.time())):
                if time.time() >= deadline:
                    self._clear()
                    raise TimeoutError("dm40: no USBTMC reply (timeout)")
        msg_id, _rtag, _inv, size, _attrs = struct.unpack(
            "<BBBxIBxxx", bytes(self._rx[:12]))
        if msg_id != 2:
            del self._rx[:]
            raise RuntimeError(f"dm40: unexpected USBTMC message id {msg_id}")

        need = 12 + size
        while len(self._rx) < need:
            if not self._more(max(0.05, deadline - time.time())):
                if time.time() >= deadline:
                    self._clear()
                    raise TimeoutError(
                        f"dm40: short USBTMC payload {len(self._rx) - 12}/{size}")
        payload = bytes(self._rx[12:need])
        del self._rx[:need]

        pad = (-size) % 4          # some devices pad the bulk transfer
        if pad:
            try:
                extra = self.dev.read(self.ep_in, pad, timeout=100)
                if extra:
                    self._rx.extend(extra)
            except Exception:
                pass
            if len(self._rx) >= pad and self._rx[:pad] == b"\x00" * pad:
                del self._rx[:pad]
        return payload

    # -- SCPI ------------------------------------------------------------

    def cmd(self, text: str) -> None:
        """Send a command that has no reply (CONF, STOP, WAV:POIN, ...)."""
        self._write(guard(text).encode("ascii") + b"\n")

    def query(self, text: str, timeout: float | None = None) -> str:
        guard(text)
        old = self.timeout
        if timeout:
            self.timeout = timeout
        try:
            self._write(text.strip().encode("ascii") + b"\n")
            raw = self._read_msg()
        finally:
            self.timeout = old
        return raw.decode("ascii", "replace").strip()

    def drain_errors(self) -> list[str]:
        errs = []
        for _ in range(4):
            text = self.query("SYST:ERR?")
            if text.startswith(("0", "+0", "-0")):
                break
            errs.append(text)
        return errs

    def close(self) -> None:
        try:
            self.usb.util.dispose_resources(self.dev)
        except Exception:
            pass


# --------------------------------------------------------------------------
# decoding
# --------------------------------------------------------------------------

UNIT = {
    "VOLT": "V", "VOLT:AC": "V", "VOLT:ACDC": "V", "VOLT:AUTO": "V",
    "RES": "ohm", "FRES": "ohm", "CONT": "ohm", "DIOD": "V",
    "CAP": "F", "FREQ": "Hz", "TEMP": "degC",
}

FUNCS = {
    "dcv": "VOLT:DC", "acv": "VOLT:AC", "acdcv": "VOLT:ACDC", "autov": "VOLT:AUTO",
    "res": "RES", "cont": "CONT", "diode": "DIOD", "cap": "CAP",
    "freq": "FREQ", "temp": "TEMP",
}


def decode_reading(text: str) -> dict:
    try:
        v = float(text)
    except ValueError:
        return {"raw": text, "value": None, "note": "unparseable"}
    if abs(v - 9.9e37) < 1e33:
        return {"raw": text, "value": None, "note": "overload"}
    if abs(v - 9.91e37) < 1e33:
        return {"raw": text, "value": None, "note": "no reading"}
    return {"raw": text, "value": v}


def parse_preamble(pre: str) -> dict:
    fields = [f.strip() for f in pre.split(",")]
    if len(fields) < 10:
        raise SystemExit(f"dm40: bad preamble {pre!r}")
    fmt, typ, npts, cnt, xinc, xorig, xref, yinc, yorig, yref = (
        int(float(fields[0])), int(float(fields[1])), int(float(fields[2])),
        int(float(fields[3])), float(fields[4]), float(fields[5]),
        float(fields[6]), float(fields[7]), float(fields[8]), float(fields[9]))
    return {"format": fmt, "type": typ, "points": npts, "count": cnt,
            "xinc": xinc, "xorig": xorig, "xref": xref,
            "yinc": yinc, "yorig": yorig, "yref": yref}


def decode_wave(fmt: int, payload: bytes) -> list[float]:
    if fmt == 0:                                  # BYTE: 10-bit code / 4
        return [c * 4.0 for c in payload]
    n = len(payload) // 2
    return list(struct.unpack(f"<{n}H", payload[: n * 2]))


def scope_capture(dmm: USBTMC, points: str, fmt: str,
                  csv_path: str | None) -> dict:
    dmm.cmd("STOP")
    deadline = time.time() + 6
    status = "RUN"
    while time.time() < deadline:
        status = dmm.query("TRIG:STAT?")
        if status == "STOP":
            break
        time.sleep(0.1)
    if status != "STOP":
        raise SystemExit(f"dm40: scope did not stop (status {status!r})")

    dmm.cmd(f"WAV:POIN {points}")
    dmm.cmd(f"WAV:FORM {fmt}")
    pre = parse_preamble(dmm.query("WAV:PRE?"))

    dmm.timeout = 30
    payload = dmm.read_block("WAV:DATA?", timeout=30)

    codes = decode_wave(pre["format"], payload)
    volts = [((c) - pre["yref"]) * pre["yinc"] + pre["yorig"] for c in codes]
    times = [(i - pre["xref"]) * pre["xinc"] + pre["xorig"]
             for i in range(len(volts))]

    if csv_path:
        with open(csv_path, "w") as fh:
            fh.write("index,time_s,volt\n")
            for i, (t, v) in enumerate(zip(times, volts)):
                fh.write(f"{i},{t:.9e},{v:.9e}\n")
    return {
        "points": len(volts),
        "format": "byte" if pre["format"] == 0 else "word",
        "xinc_s": pre["xinc"], "xorig_s": pre["xorig"],
        "yinc_v": pre["yinc"], "yorig_v": pre["yorig"],
        "sample_rate_sps": round(1.0 / pre["xinc"], 3) if pre["xinc"] else None,
        "vmin": min(volts), "vmax": max(volts),
        "vpp": max(volts) - min(volts),
        "vmean": sum(volts) / len(volts),
        "csv": csv_path,
    }


def write_png(path: str, width: int, height: int, rgb: bytearray) -> None:
    import struct as _s
    import zlib
    raw = b"".join(b"\x00" + bytes(rgb[y * width * 3:(y + 1) * width * 3])
                   for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (_s.pack(">I", len(data)) + body
                + _s.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", _s.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    open(path, "wb").write(png)


def screen_capture(dmm: USBTMC, out_path: str) -> dict:
    """Read the panel's framebuffer (RGB565) and write a PNG."""
    W, H, BAND = 480, 320, 6          # 6 rows = 5760 B, below the ~6 KB cap
    dmm._clear()
    pix = bytearray()
    y = 0
    while y < H:
        rows = min(BAND, H - y)
        data = dmm.read_block(f"DBG:LCD? 0,{y},{W},{rows},{W * rows}",
                              timeout=10, max_len=8192)
        need = W * rows * 2
        if len(data) < need:
            data += b"\x00" * (need - len(data))
        for i in range(W * rows):
            v = data[2 * i] | (data[2 * i + 1] << 8)
            r, g, b = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
            pix += bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4),
                          (b << 3) | (b >> 2)))
        y += rows
    write_png(out_path, W, H, pix)
    return {"png": out_path, "width": W, "height": H}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dm40")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--serial", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("idn")
    sub.add_parser("status")

    r = sub.add_parser("read")
    r.add_argument("-n", type=int, default=1)
    r.add_argument("--interval", type=float, default=0.0)
    r.add_argument("--func", choices=sorted(FUNCS), default=None)

    m = sub.add_parser("measure")
    m.add_argument("func", choices=sorted(FUNCS))

    s = sub.add_parser("scope")
    s.add_argument("--points", default="screen", help="screen | max | N")
    s.add_argument("--format", default="word", choices=["word", "byte"])
    s.add_argument("-o", "--out", default=None, help="write CSV here")

    sc = sub.add_parser("screen")
    sc.add_argument("-o", "--out", default="screen.png")

    w = sub.add_parser("raw")
    w.add_argument("scpi", nargs="+")

    args = ap.parse_args(argv)

    if args.cmd == "raw":
        cmd_text = ";".join(args.scpi)
        guard(cmd_text)
        is_query = any("?" in part for part in cmd_text.split(";"))
    else:
        is_query = True

    dmm = USBTMC(timeout=args.timeout, serial=args.serial)
    try:
        out: dict = {}
        if args.cmd == "idn":
            out = {"idn": dmm.query("*IDN?")}

        elif args.cmd == "status":
            out = {
                "idn": dmm.query("*IDN?"),
                "function": dmm.query("FUNC?"),
                "configure": dmm.query("CONF?"),
                "trigger": dmm.query("TRIG:STAT?"),
                "rate_sps": dmm.query("ACQ:SRAT?"),
            }
            errs = dmm.drain_errors()
            if errs:
                out["errors"] = errs

        elif args.cmd == "read":
            if args.func:
                dmm.cmd(f"CONF:{FUNCS[args.func]}")
            func = dmm.query("FUNC?")
            readings = []
            for i in range(args.n):
                if i and args.interval:
                    time.sleep(args.interval)
                dec = decode_reading(dmm.query("READ?", timeout=15))
                dec["unit"] = UNIT.get(func, "")
                readings.append(dec)
            out = {"function": func, "readings": readings}

        elif args.cmd == "measure":
            dmm.cmd(f"CONF:{FUNCS[args.func]}")
            out = {"function": FUNCS[args.func],
                   "reading": decode_reading(dmm.query("READ?", timeout=15))}
            out["reading"]["unit"] = UNIT.get(FUNCS[args.func], "")

        elif args.cmd == "scope":
            out = scope_capture(dmm, args.points, args.format, args.out)

        elif args.cmd == "screen":
            out = screen_capture(dmm, args.out)

        elif args.cmd == "raw":
            if is_query:
                out = {"reply": dmm.query(cmd_text, timeout=max(args.timeout, 10))}
            else:
                dmm.cmd(cmd_text)
                out = {"sent": cmd_text}

        if args.json:
            print(json.dumps(out, indent=2, ensure_ascii=False))
        elif "readings" in out:
            for rd in out["readings"]:
                val = rd.get("value")
                print(f"{val:.6g} {rd.get('unit','')}" if val is not None
                      else f"{rd.get('note','?')} ({rd.get('raw')})")
        elif "reading" in out:
            rd = out["reading"]
            val = rd.get("value")
            print(f"{val:.6g} {rd.get('unit','')}" if val is not None
                  else f"{rd.get('note','?')} ({rd.get('raw')})")
        elif "reply" in out:
            print(out["reply"])
        else:
            for k, v in out.items():
                print(f"{k}: {v}")
    finally:
        dmm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
