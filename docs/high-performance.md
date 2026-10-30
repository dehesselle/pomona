# High Performance mode, and why Pomona does not use it

This document explains why Pomona speaks Standard mode only. It records what was learned
about Apple's High Performance mode through an ssh forward, and the relay idea that was
not built.

## What High Performance is

High Performance mode shows a virtual display the Mac creates at the size the viewer
asks for. Picture and sound do not travel in RFB: they come as SRTP over **UDP**, the
audio on port 5900 and the video on 5901. The viewer sends a media offer (`0x1c`) with
the SRTP keys over the TCP connection, and the Mac's `avconferenced` then streams to the
viewer's address *as the TCP connection shows it*, to the same port numbers.

## Why it fails through ssh

Pomona reaches the Mac through an ssh forward, so the Mac sees the connection coming
from its own 127.0.0.1. A capture on the Mac showed `127.0.0.1.5901 > 127.0.0.1.5901` in
1,252-byte packets: the Mac streams to itself. No UDP ever reaches the viewer, and
NoMac's gateway carries ssh in a WebSocket and nothing else.

The Mac did accept High Performance with a 3840×2160 HEVC canvas, even as a virtual
machine. Measured on 2026-10-30 against a NoMac Mac (macOS 26.6), with iShareScreen.

iShareScreen reports this situation as "HP stream never started ... host-side stall",
which hides that the cause is UDP that cannot arrive.

## A relay, not built

A relay could carry the stream through ssh after all:

- loopback aliases 127.0.0.2 and 127.0.0.3 on the Mac (sudo needs no password there);
- a Mac-side relay that connects to Screen Sharing *from* 127.0.0.3, so the Mac streams
  to 127.0.0.3;
- the UDP packets, length-framed, over a second ssh forward;
- a local relay handing them to the viewer on loopback.

Head-of-line blocking inside ssh and the WebSocket would likely erase the gain. It is
worth revisiting only if Adaptive turns out not to be good enough.

## Firewall note

iShareScreen binds its UDP sockets on all interfaces by default, which makes Windows
Firewall ask. `ISS_UDP_BIND_HOST=127.0.0.1` avoids that. Pomona opens no listening
sockets at all.
