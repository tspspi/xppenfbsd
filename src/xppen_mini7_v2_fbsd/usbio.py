from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Iterable, Optional

import usb.core
import usb.util
from usb.core import USBError

from . import constants

LOG = logging.getLogger(__name__)

@dataclass
class UnlockOptions:
    force_detach: bool = True
    set_alt: bool = True
    verbose: bool = False

class TabletNotFoundError(RuntimeError):
    pass

def find_device(ugen: Optional[str] = None) -> usb.core.Device:
    """Locate the tablet optionally by ugen bus.address."""
    devices = usb.core.find(
        find_all=True, idVendor=constants.VENDOR_ID, idProduct=constants.PRODUCT_ID
    )
    for dev in devices:
        if ugen is None:
            return dev
        if _matches_ugen(dev, ugen):
            return dev
    raise TabletNotFoundError("XP-Pen Deco Mini7 V2 not found")

def _matches_ugen(dev: usb.core.Device, ugen: str) -> bool:
    if not hasattr(dev, "bus") or not hasattr(dev, "address"):
        return False
    try:
        prefix = "ugen"
        if not ugen.startswith(prefix):
            return False
        bus_str, addr_str = ugen[len(prefix) :].split(".")
        return int(bus_str) == dev.bus and int(addr_str) == dev.address
    except Exception:
        return False

def unlock_interfaces(dev: usb.core.Device, verbose: bool = False) -> None:
    _ensure_configuration(dev)
    for interface, length in constants.REPORT_LENGTHS.items():
        LOG.debug("SET_IDLE iface %s", interface)
        _set_idle(dev, interface)
        LOG.debug("GET_REPORT_DESCRIPTOR iface %s len=%s", interface, length)
        report = _get_report_descriptor(dev, interface, length)
        if verbose:
            LOG.debug("    %s", report.hex())

def _ensure_configuration(dev: usb.core.Device) -> None:
    try:
        dev.set_configuration()
    except USBError as exc:
        if exc.errno not in (None, 16):
            raise

def _set_idle(dev: usb.core.Device, interface: int) -> None:
    dev.ctrl_transfer(0x21, 0x0A, 0, interface, [])

def _get_report_descriptor(dev: usb.core.Device, interface: int, length: int) -> bytes:
    return bytes(dev.ctrl_transfer(0x81, 0x06, 0x2200, interface, length))

def detach_kernel_driver(dev: usb.core.Device, interface: int) -> bool:
    if not hasattr(dev, "is_kernel_driver_active"):
        return False
    try:
        if dev.is_kernel_driver_active(interface):
            dev.detach_kernel_driver(interface)
            return True
    except USBError as exc:
        raise RuntimeError(f"Failed to detach kernel driver: {exc}")
    return False

def attach_kernel_driver(dev: usb.core.Device, interface: int) -> None:
    try:
        dev.attach_kernel_driver(interface)
    except (USBError, NotImplementedError) as exc:
        LOG.warning("Could not reattach kernel driver: %s", exc)

def force_set_alt(dev: usb.core.Device, interface: int) -> None:
    usbconfig = "usbconfig"
    bus = getattr(dev, "bus", None)
    address = getattr(dev, "address", None)
    if bus is None or address is None:
        LOG.warning("Device bus/address unknown; cannot run usbconfig set_alt")
        return
    ugen = f"ugen{bus}.{address}"
    cmd = [usbconfig, "-d", ugen, "-i", str(interface), "set_alt", "0"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        LOG.info("Forced usbconfig set_alt 0 on %s interface %s", ugen, interface)
    except FileNotFoundError:
        LOG.warning("usbconfig not found; skipping set_alt recovery")
    except subprocess.CalledProcessError as exc:
        LOG.warning("usbconfig failed: %s", exc.stderr or exc.stdout)

def release_interface(dev: usb.core.Device, interface: int) -> None:
    try:
        usb.util.release_interface(dev, interface)
    finally:
        usb.util.dispose_resources(dev)

def read_stylus_report(dev: usb.core.Device, timeout_ms: int) -> Optional[bytes]:
    try:
        data = dev.read(
            constants.STYLUS_ENDPOINT,
            constants.STYLUS_READ_SIZE,
            timeout=timeout_ms,
        )
    except USBError as exc:
        if exc.errno in (60, 110):
            return None
        raise
    payload = bytes(data)
    return payload if payload else None
