"""Offline tests: safety guard + USBTMC framing + preamble decoding."""
import importlib.util
import struct
import sys

import os
MODULE = os.environ.get(
    "DM40_PY",
    os.path.expanduser("~/.agents/skills/dm40/scripts/dm40.py"))
SPEC = importlib.util.spec_from_file_location("dm40", MODULE)
dm40 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dm40)

OK = [
    "*IDN?", "CONF:VOLT:DC", "CONF:VOLT:AC", "READ?", "FETC?",
    "MEAS:RES?", "MEAS:CAP?", "WAV:DATA?", "WAV:POIN MAX", "WAV:FORM WORD",
    "STOP", "RUN", "TRIG:STAT?", "TRIG:LEV 0.5", "TIM:SCAL 1E-3",
    "CONF:VOLT:DC;READ?", "MMEM:CAT?", "MMEM:DATA? LOGS/LOG000.CSV",
    "FUNC?", "CONF?", "SYST:ERR?", "ACQ:SRAT?",
]
BAD = [
    "CONF:CURR:DC", "MEAS:CURR?", "MEAS:POW?", "CONF:POW",
    "DBG:UPLOAD", "DBG:UPDATE", "DBG:RECOVER", "MMEM:DEL LOGS",
    "LOG:CSV ON", "LOG:STATe ON", "SOUR:FREQ 1000", "OUTP ON",
    "*RST", "SYST:PRES", "CAL:VOLT", "UPDATE", "UPLOAD",
    "CONF:VOLT:DC;CONF:CURR:DC", "dm40_opendm40_v0.4.1.bin",
    "DISP:TEXT dm40.bin",
]


def test_guard():
    for c in OK:
        assert dm40.guard(c) == c, c
    for c in BAD:
        try:
            dm40.guard(c)
        except SystemExit:
            continue
        raise AssertionError(f"guard accepted {c!r}")


def test_preamble():
    pre = dm40.parse_preamble("1,2,400,1,1.000000E-06,-2.000000E-04,200,"
                              "2.000000E-03,-1.000000E+00,0")
    assert pre["format"] == 1 and pre["points"] == 400
    assert abs(pre["xinc"] - 1e-6) < 1e-15
    assert abs(pre["yinc"] - 2e-3) < 1e-15


def test_wave_decode():
    assert dm40.decode_wave(0, bytes([0, 4, 8])) == [0.0, 16.0, 32.0]
    assert dm40.decode_wave(1, struct.pack("<3H", 0, 512, 1023)) == [0, 512, 1023]


class FakeDev:
    """Minimal USBTMC device: answers a REQUEST with header + payload."""

    def __init__(self, reply: bytes):
        self.reply = reply
        self.written = []
        self.pending = b""

    def write(self, ep, data, timeout=None):
        self.written.append(bytes(data))
        if len(data) >= 12 and data[0] == 2:          # REQUEST_DEV_DEP_MSG_IN
            size = struct.unpack("<I", data[4:8])[0]
            body = self.reply[:size]
            self.pending = (struct.pack("<BBBxIBxxx", 2, data[1], data[2] ^ 0xFF,
                                        len(body), 0x01) + body)

    def read(self, ep, n, timeout=None):
        out, self.pending = self.pending[:n], self.pending[n:]
        return out


def test_usbtmc_framing():
    dev = dm40.USBTMC.__new__(dm40.USBTMC)
    dev.usb = type("U", (), {"util": None})()
    dev._rx = bytearray()
    dev.timeout = 2.0
    dev.tag = 0
    dev.dev = FakeDev(b"+1.234567E+00\n")
    dev.ep_in = dev.ep_out = 0x81
    assert dev.query("READ?") == "+1.234567E+00"
    # the command travelled as a DEV_DEP_MSG_OUT header + payload
    head = dev.dev.written[0]
    assert head[0] == 1 and head[8] == 0x01
    assert head[12:].rstrip(b"\x00") == b"READ?\n"


def test_reading_decode():
    assert dm40.decode_reading("+1.5")["value"] == 1.5
    assert dm40.decode_reading("+9.90000000E+37")["note"] == "overload"
    assert dm40.decode_reading("+9.91000000E+37")["note"] == "no reading"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok:", name)
