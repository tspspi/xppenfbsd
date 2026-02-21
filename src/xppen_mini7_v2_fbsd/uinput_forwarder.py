from __future__ import annotations

import ctypes
import ctypes.util
import grp
import logging
import os
import struct
import time
from typing import Optional

from .constants import DEFAULT_EVENT_GROUP, DEFAULT_EVENT_MODE
from .stylus import StylusSample

LOG = logging.getLogger(__name__)

IOCPARM_SHIFT = 13
IOCPARM_MASK = (1 << IOCPARM_SHIFT) - 1
IOC_VOID = 0x20000000
IOC_OUT = 0x40000000
IOC_IN = 0x80000000

def _IOC(direction: int, group: int, number: int, length: int) -> int:
    return direction | ((length & IOCPARM_MASK) << 16) | (group << 8) | number

def _IO(group: int, number: int) -> int:
    return _IOC(IOC_VOID, group, number, 0)

def _IOW(group: int, number: int, length: int) -> int:
    return _IOC(IOC_IN, group, number, length)

def _IOWINT(group: int, number: int) -> int:
    return _IOC(IOC_VOID, group, number, ctypes.sizeof(ctypes.c_int))

UINPUT_IOCTL_BASE = ord("U")
UINPUT_MAX_NAME_SIZE = 80

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

BTN_TOUCH = 0x14A
BTN_STYLUS = 0x14B
BTN_STYLUS2 = 0x14C
BTN_TOOL_PEN = 0x140
BTN_TOOL_RUBBER = 0x141

ABS_X = 0x00
ABS_Y = 0x01
ABS_PRESSURE = 0x18
ABS_TILT_X = 0x1A
ABS_TILT_Y = 0x1B

SYN_REPORT = 0

INPUT_EVENT = struct.Struct("llHHi")

class InputID(ctypes.Structure):
    _fields_ = [
        ("bustype", ctypes.c_uint16),
        ("vendor", ctypes.c_uint16),
        ("product", ctypes.c_uint16),
        ("version", ctypes.c_uint16),
    ]

class InputAbsInfo(ctypes.Structure):
    _fields_ = [
        ("value", ctypes.c_int32),
        ("minimum", ctypes.c_int32),
        ("maximum", ctypes.c_int32),
        ("fuzz", ctypes.c_int32),
        ("flat", ctypes.c_int32),
        ("resolution", ctypes.c_int32),
    ]

class UInputSetup(ctypes.Structure):
    _fields_ = [
        ("id", InputID),
        ("name", ctypes.c_char * UINPUT_MAX_NAME_SIZE),
        ("ff_effects_max", ctypes.c_uint32),
    ]

class UInputAbsSetup(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint16),
        ("absinfo", InputAbsInfo),
    ]

UINPUT_STRUCT_SIZE = ctypes.sizeof(UInputSetup)
UINPUT_ABS_SIZE = ctypes.sizeof(UInputAbsSetup)
UI_DEV_CREATE = _IO(UINPUT_IOCTL_BASE, 1)
UI_DEV_DESTROY = _IO(UINPUT_IOCTL_BASE, 2)
UI_DEV_SETUP = _IOW(UINPUT_IOCTL_BASE, 3, UINPUT_STRUCT_SIZE)
UI_ABS_SETUP = _IOW(UINPUT_IOCTL_BASE, 4, UINPUT_ABS_SIZE)
UI_GET_SYSNAME = lambda length: _IOC(IOC_OUT, UINPUT_IOCTL_BASE, 44, length)
UI_SET_EVBIT = _IOWINT(UINPUT_IOCTL_BASE, 100)
UI_SET_KEYBIT = _IOWINT(UINPUT_IOCTL_BASE, 101)
UI_SET_ABSBIT = _IOWINT(UINPUT_IOCTL_BASE, 103)

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
libc.ioctl.restype = ctypes.c_int

class UInputForwarder:
    """Forward stylus samples to a uinput-backed evdev node."""

    def __init__(
        self,
        name: str,
        *,
        uinput_path: str = "/dev/uinput",
        vendor: int,
        product: int,
        event_mode: int = DEFAULT_EVENT_MODE,
        event_group: str = DEFAULT_EVENT_GROUP,
    ) -> None:
        self.fd = os.open(uinput_path, os.O_WRONLY)
        self.path = uinput_path
        self.vendor = vendor
        self.product = product
        self.event_mode = event_mode
        self.event_group = event_group
        self.event_path: Optional[str] = None
        self._configure(name)

    def _configure(self, name: str) -> None:
        for ev in (EV_KEY, EV_ABS):
            self._ioctl_int(UI_SET_EVBIT, ev)
        for key in (BTN_TOUCH, BTN_STYLUS, BTN_STYLUS2, BTN_TOOL_PEN, BTN_TOOL_RUBBER):
            self._ioctl_int(UI_SET_KEYBIT, key)
        for abs_code in (ABS_X, ABS_Y, ABS_PRESSURE, ABS_TILT_X, ABS_TILT_Y):
            self._ioctl_int(UI_SET_ABSBIT, abs_code)
        self._setup_abs(ABS_X, 0, 0x4585, resolution=5080)
        self._setup_abs(ABS_Y, 0, 0x2B65, resolution=5080)
        self._setup_abs(ABS_PRESSURE, 0, 0x3FFF, resolution=1)
        self._setup_abs(ABS_TILT_X, -127, 127, resolution=1)
        self._setup_abs(ABS_TILT_Y, -127, 127, resolution=1)

        setup = UInputSetup()
        setup.id = InputID(0x0003, self.vendor, self.product, 0)
        encoded = name.encode("utf-8")[: UINPUT_MAX_NAME_SIZE - 1]
        padded = encoded + b"\x00" * (UINPUT_MAX_NAME_SIZE - len(encoded))
        setup.name = padded
        setup.ff_effects_max = 0
        self._ioctl_struct(UI_DEV_SETUP, setup)
        self._ioctl_void(UI_DEV_CREATE)
        self.event_path = self._query_event_node()
        if self.event_path:
            self._apply_permissions()

    def _query_event_node(self) -> Optional[str]:
        for length in (32, 64, 128):
            buf = ctypes.create_string_buffer(length)
            req = UI_GET_SYSNAME(length)
            if libc.ioctl(self.fd, ctypes.c_ulong(req), buf) == 0:
                name = buf.value.decode()
                if name:
                    return os.path.join("/dev/input", name)
        return None

    def _apply_permissions(self) -> None:
        if not self.event_path or not os.path.exists(self.event_path):
            return
        try:
            os.chmod(self.event_path, self.event_mode)
        except OSError as exc:
            LOG.warning("Could not chmod %s: %s", self.event_path, exc)
        if self.event_group:
            try:
                gid = grp.getgrnam(self.event_group).gr_gid
                st = os.stat(self.event_path)
                os.chown(self.event_path, st.st_uid, gid)
            except KeyError:
                LOG.warning("Group %s not found", self.event_group)
            except OSError as exc:
                LOG.warning("Could not chown %s: %s", self.event_path, exc)

    def _ioctl_void(self, request: int) -> None:
        if libc.ioctl(self.fd, ctypes.c_ulong(request), ctypes.c_void_p()) != 0:
            err = ctypes.get_errno()
            raise OSError(err, f"ioctl {request:#x} failed: {os.strerror(err)}")

    def _ioctl_int(self, request: int, value: int) -> None:
        arg = ctypes.c_void_p(ctypes.c_int(value).value)
        if libc.ioctl(self.fd, ctypes.c_ulong(request), arg) != 0:
            err = ctypes.get_errno()
            raise OSError(err, f"ioctl {request:#x} failed: {os.strerror(err)}")

    def _ioctl_struct(self, request: int, struct_obj: ctypes.Structure) -> None:
        if libc.ioctl(self.fd, ctypes.c_ulong(request), ctypes.byref(struct_obj)) != 0:
            err = ctypes.get_errno()
            raise OSError(err, f"ioctl {request:#x} failed: {os.strerror(err)}")

    def _setup_abs(self, code: int, minimum: int, maximum: int, *, resolution: int = 0) -> None:
        abs_setup = UInputAbsSetup()
        abs_setup.code = code
        abs_setup.absinfo.minimum = minimum
        abs_setup.absinfo.maximum = maximum
        abs_setup.absinfo.value = minimum
        abs_setup.absinfo.fuzz = 0
        abs_setup.absinfo.flat = 0
        abs_setup.absinfo.resolution = resolution
        self._ioctl_struct(UI_ABS_SETUP, abs_setup)

    def forward(self, sample: StylusSample) -> None:
        events = [
            (EV_ABS, ABS_X, sample.x),
            (EV_ABS, ABS_Y, sample.y),
            (EV_ABS, ABS_PRESSURE, sample.pressure),
            (EV_ABS, ABS_TILT_X, sample.tilt_x),
            (EV_ABS, ABS_TILT_Y, sample.tilt_y),
            (EV_KEY, BTN_TOUCH, int(sample.tip)),
            (EV_KEY, BTN_STYLUS, int(sample.barrel)),
            (EV_KEY, BTN_STYLUS2, int(sample.eraser)),
            (EV_KEY, BTN_TOOL_PEN, int(sample.in_range and not sample.invert)),
            (EV_KEY, BTN_TOOL_RUBBER, int(sample.in_range and sample.invert)),
        ]
        for ev_type, code, value in events:
            self._write_event(ev_type, code, value)
        self._write_event(EV_SYN, SYN_REPORT, 0)

    def _write_event(self, ev_type: int, code: int, value: int) -> None:
        now = time.time()
        sec = int(now)
        usec = int((now - sec) * 1_000_000)
        packet = INPUT_EVENT.pack(sec, usec, ev_type, code, value)
        os.write(self.fd, packet)

    def close(self) -> None:
        if getattr(self, "fd", None) is not None:
            try:
                self._ioctl_void(UI_DEV_DESTROY)
            finally:
                os.close(self.fd)
                self.fd = None

    def __enter__(self) -> "UInputForwarder":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
