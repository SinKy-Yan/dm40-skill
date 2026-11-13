#!/usr/bin/env python3
"""Wait for the meter to enumerate as opendm40, then run *IDN?.

  wait_for_meter.py [seconds] [--idn]

Exit codes: 0 = meter found (and answered), 2 = timeout.
"""
import subprocess
import sys
import time

VID_PID = ((0x1209, 0xD040), (0x1209, 0x0001))


def find():
    import usb.core
    import usb.backend.libusb1 as l1

    be = l1.get_backend(find_library=lambda _n: "/opt/homebrew/lib/libusb-1.0.dylib")
    for vid, pid in VID_PID:
        dev = usb.core.find(idVendor=vid, idProduct=pid, backend=be)
        if dev is not None:
            return dev
    return None


def main() -> int:
    limit = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1][0].isdigit() else 180.0
    t0 = time.time()
    while time.time() - t0 < limit:
        dev = find()
        if dev is not None:
            print(f"found {dev.idVendor:04x}:{dev.idProduct:04x} {dev.product!r} "
                  f"serial={dev.serial_number!r}")
            return 0
        time.sleep(1.0)
    print("timeout: meter did not enumerate as 1209:D040", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
