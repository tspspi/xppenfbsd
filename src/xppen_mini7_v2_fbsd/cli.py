from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from typing import Iterable, List, Optional

import usb.core
import usb.util

from . import __version__, constants
from .socket_forwarder import SocketForwarder
from .stylus import StylusSample, decode_stylus
from .uinput_forwarder import UInputForwarder
from . import usbio

LOG = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="specific ugenX.Y path to bind (default: scan)")
    parser.add_argument("--scan", action="store_true", help="continuously scan for the tablet")
    parser.add_argument("--scan-interval", type=float, default=5.0, help="seconds between scan attempts")
    parser.add_argument("--daemonize", action="store_true", help="run in background")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    parser.add_argument("--no-uinput", action="store_true", help="disable uinput bridge")
    parser.add_argument("--socket-path", help="unix domain socket for forwarding events")
    parser.add_argument("--event-mode", default="660", help="File mode (octal) for new /dev/input nodes")
    parser.add_argument("--event-group", default=constants.DEFAULT_EVENT_GROUP, help="group for event node")
    parser.add_argument("--timeout", type=int, default=100, help="USB read timeout in ms")
    parser.add_argument("--force-detach", action="store_true", help="detach kernel driver from interface")
    parser.add_argument("--skip-set-alt", action="store_true", help="skip usbconfig set_alt recovery")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        args.event_mode = int(str(args.event_mode), 8)
    except ValueError as exc:
        parser.error(f"Invalid --event-mode: {exc}")
    if not args.scan and not args.device:
        args.scan = True
    return args


def daemonize() -> None:
    # Double fork (not using daemonize)
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)

    os.umask(0)
    sys.stdout.flush()
    sys.stderr.flush()

    # Create new stdout and stderr pipes
    with open(os.devnull, "rb", buffering=0) as read_null, open(os.devnull, "ab", buffering=0) as write_null:
        os.dup2(read_null.fileno(), sys.stdin.fileno())
        os.dup2(write_null.fileno(), sys.stdout.fileno())
        os.dup2(write_null.fileno(), sys.stderr.fileno())


class TabletDaemon:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.should_run = True
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)

    def _handle_stop(self, signum, frame) -> None:  # type: ignore[override]
        LOG.info("Received signal %s, stopping", signum)
        self.should_run = False

    def run(self) -> None:
        if self.args.device:
            self._serve_explicit(self.args.device)
            return
        while self.should_run:
            try:
                dev = usbio.find_device()
            except usbio.TabletNotFoundError:
                LOG.debug("Tablet not found; rescanning in %.1fs", self.args.scan_interval)
                time.sleep(self.args.scan_interval)
                continue
            self._serve_device(dev)
            time.sleep(1)

    def _serve_explicit(self, ugen: str) -> None:
        while self.should_run:
            try:
                dev = usbio.find_device(ugen)
            except usbio.TabletNotFoundError:
                LOG.error("Device %s not present; sleeping %.1fs", ugen, self.args.scan_interval)
                time.sleep(self.args.scan_interval)
                continue
            self._serve_device(dev)
            time.sleep(1)

    def _serve_device(self, dev: usb.core.Device) -> None:
        LOG.info("Binding to XP-Pen Deco Mini7 V2 (bus=%s addr=%s)", getattr(dev, "bus", "?"), getattr(dev, "address", "?"))
        detached = False
        claimed = False
        forwarders: List[object] = []
        try:
            usbio.unlock_interfaces(dev, verbose=self.args.verbose)
            if self.args.force_detach:
                detached = usbio.detach_kernel_driver(dev, constants.STYLUS_INTERFACE)
            usb.util.claim_interface(dev, constants.STYLUS_INTERFACE)
            claimed = True
            if not self.args.no_uinput:
                forwarders.append(
                    UInputForwarder(
                        "XP-Pen Deco Mini7 V2 (uinput)",
                        vendor=constants.VENDOR_ID,
                        product=constants.PRODUCT_ID,
                        event_mode=self.args.event_mode,
                        event_group=self.args.event_group,
                    )
                )
            if self.args.socket_path:
                forwarders.append(SocketForwarder(self.args.socket_path))
            if not forwarders:
                LOG.error("No output targets requested (--no-uinput and no --socket-path). Aborting.")
                return
            self._pump(dev, forwarders)
        except usb.core.USBError as exc:
            LOG.warning("USB error while streaming (%s); will rescan if enabled", exc)
        finally:
            for fwd in forwarders:
                close = getattr(fwd, "close", None)
                if callable(close):
                    close()
            if claimed:
                usbio.release_interface(dev, constants.STYLUS_INTERFACE)
            if detached and not self.args.skip_set_alt:
                try:
                    usbio.attach_kernel_driver(dev, constants.STYLUS_INTERFACE)
                except Exception:
                    LOG.warning("Could not reattach kernel driver", exc_info=True)
            if not self.args.skip_set_alt:
                usbio.force_set_alt(dev, constants.STYLUS_INTERFACE)

    def _pump(self, dev: usb.core.Device, forwarders: List[object]) -> None:
        while self.should_run:
            payload = usbio.read_stylus_report(dev, timeout_ms=self.args.timeout)
            if not payload:
                continue
            sample = decode_stylus(payload)
            if not sample:
                continue
            for fwd in forwarders:
                forward = getattr(fwd, "forward", None)
                if callable(forward):
                    forward(sample)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.daemonize:
        daemonize()
    daemon = TabletDaemon(args)
    daemon.run()


if __name__ == "__main__":  # pragma: no cover
    main()
