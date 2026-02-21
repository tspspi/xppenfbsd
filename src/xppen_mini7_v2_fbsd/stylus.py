from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .constants import STYLUS_READ_SIZE


@dataclass
class StylusSample:
    tip: bool
    barrel: bool
    eraser: bool
    in_range: bool
    invert: bool
    x: int
    y: int
    pressure: int
    tilt_x: int
    tilt_y: int

def hexdump(data: Iterable[int]) -> str:
    buf = bytes(data)
    return " ".join(f"{byte:02x}" for byte in buf)

def _decode_signed(byte: int) -> int:
    return byte - 0x100 if byte & 0x80 else byte

def decode_stylus(payload: bytes) -> Optional[StylusSample]:
    if len(payload) < 10 or payload[0] != 0x07:
        return None
    status = payload[1]
    tip = bool(status & 0x01)
    barrel = bool(status & 0x02)
    eraser = bool(status & 0x04)
    in_range = bool(status & 0x08)
    invert = bool(status & 0x20)
    x = payload[2] | (payload[3] << 8)
    y = payload[4] | (payload[5] << 8)
    pressure = payload[6] | (payload[7] << 8)
    tilt_x = _decode_signed(payload[8])
    tilt_y = _decode_signed(payload[9])
    return StylusSample(
        tip=tip,
        barrel=barrel,
        eraser=eraser,
        in_range=in_range,
        invert=invert,
        x=x,
        y=y,
        pressure=pressure,
        tilt_x=tilt_x,
        tilt_y=tilt_y,
    )
