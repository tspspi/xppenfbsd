# XP-Pen Deco Mini7 v2 FreeBSD Daemon (Unofficial)

A small userspace daemon that allows simple operation of the [XP-Pen Deco Mini7 V2](https://amzn.to/4kPG1C7)
(_note_: affiliate link) on FreeBSD (will be extended to different types of tablets most likely later on).

## Rationale / some history

This repository emerged out of the frustration that it has been hard to get an otherwise excellent
XP-Pen Deco Mini7 V2 up and running easily on FreeBSD. Everytime the device was attached it
turned up as `pointer` and `keyboard` device as expected (two `HID` device instances), but the
pointer device never started streaming stylus positions. I played around trying to install existing
drivers like `OpenTabletDriver`, tried out `xf86-input-wacom`, `libwacom` and many other solutions 
but they either failed to compile due to heavy dependencies and unsupported platforms or just
plainly did not work due to some untraceable errors. 

Minor research pointed out that those tablets require an `activation` sequence to enable
output of the HID nodes and will then spill out reports. In addition the HID messages for
_styli_ are not compatible with the standard USB mouse driver `usm`. This lead to the quick
overnight development of `xppen-mini7-v2-fbsd` based on information captured on Windows utilizing
[Wireshark](https://www.wireshark.org/) and [USBPcap](https://desowin.org/usbpcap/).

## Introduction

`xppen-mini7-v2-fbsd` is a Python daemon that:

- Locates XP-Pen Deco Mini7 V2 tablets on FreeBSD using `PyUSB` by their vendor and product ID.
- Replays the Windows-style initialization sequence so the stylus interface enters reporting mode.
- Creates a virtual `/dev/input/event*` node via FreeBSD's `uinput` driver and forwards stylus
  packets with pressure/tilt data.
- Optionally forwards packets to a Unix domain socket instead of `uinput`. This feature will be used
  for some applications this author had in mind, it will most likely be useless for anyone else.
- Can run once against an explicit `/dev/ugenX.Y` path (for example to be executed via a `devd` hook)
  or stay resident, periodically scanning for devices and auto-binding when the tablet appears.

## Requirements

- FreeBSD 13 or higher with `evdev`, `uinput`, and `hid` support available (either compiled into the kernel like
  for `GENERIC`, or loaded via `kldload evdev uinput hid` if your kernel omits them). If `/dev/uinput` exists
  the daemon will most likely work.
- Access to `/dev/uinput` and to the USB device (typically run as `root` or grant `devfs` permissions since
  we need access to `usbconfig` and other low level routines).
- Python 3.11 or higher with `pyusb` installed.

## Installation

```
pip install xppenfbsd
```

## Usage

Foreground daemon that scans for the tablet and logs all received messages and events verbosely:

```
xppen-fbsd-daemon --scan --verbose
```

Daemon performing the same action in background:

```
xppen-fbsd-daemon --scan --daemonize
```

Launching by specifying an explicit device path (for example when launching useful from `devd`):

```
xppen-fbsd-daemon --device ugen0.7 --detach --event-mode 660 --event-group wheel
```

Socket-only mode:

```
xppen-fbsd-daemon --socket /var/run/xppen.sock --no-uinput
```
