# Measurements

This document collects what Pomona and its research measured: the Mac's picture in
Standard mode, decoding speed, and live sessions. Each section says when and over which
path it was measured, because the path changes the numbers.

## Standard mode's qualities

Measured on 2026-10-30 with the research probe (iShareScreen's sign-in and record layer,
zDesk's prelude and encoding lists). The probe ran on a Windows PC and reached a NoMac
Mac (macOS 26.6, `VirtualMac2,1`, 1440×900) through qem's ssh forward. The Mac showed
a constantly scrolling Terminal over a photo wallpaper, for 15 s per mode.

| Mode | Updates/s | Bandwidth | Area updated | First picture |
| --- | --- | --- | --- | --- |
| Adaptive (`1011`) | 45.6 | 2.5 Mbit/s | 146 screens | 0.95 s |
| Full (zlib, `6`) | 33.2 | 30.8 Mbit/s | 120 screens | 1.54 s |
| ZRLE (`16`, what TigerVNC gets) | 34.5 | 25.7 Mbit/s | 126 screens | 1.53 s |

"Area updated" counts every rectangle, including Adaptive's refinement passes over areas
already sent, so it overstates fresh content for Adaptive. Updates per second and
bandwidth are exact. The Adaptive run had one second with a single update, not
investigated.

Adaptive needs a tenth of the bandwidth of the other two at a higher update rate, which
is why Pomona is built around it.

## Decoding Adaptive

The Adaptive capture above (682 rectangles, 4.7 MB) decoded on a Hetzner cpx22 (2 vCPU AMD
EPYC Genoa):

| | Pure Python port | zDesk's `decoder.rs` through PyO3 |
| --- | --- | --- |
| Whole 15 s capture | 122.9 s (0.12× real time) | 0.85 s (17.7× real time) |
| Full update, median / max | 308 ms / 964 ms | 2.0 ms / 7.1 ms |
| Partial update, median / max | 15.5 ms / 722 ms | 0.1 ms / 6.1 ms |

Both decoders produced byte-identical frames that match the Mac's screen. Pure Python
spends about 70% of its time in the inverse DCT and the colour conversion, so the
decoder has to be native.

## Signing in

The type-30 key exchange takes two 4096-bit modular exponentiations with 8192-bit
exponents: 0.56 s in CPython on the same cpx22. It runs once per connection.

## Pomona, live

Measured on 2026-10-30 with `pomona --probe 15` (Pomona's own sign-in and record layer),
against a NoMac Mac with the same scrolling Terminal. Pomona ran on a Hetzner cpx22 in
Falkenstein; the path was Mac → NoMac's gateway → qem's ssh forward on a Windows PC →
an ssh reverse forward → the cpx22.

| Run | Updates/s | Bandwidth | First picture | Adaptive rectangles |
| --- | --- | --- | --- | --- |
| 1 | 18.6 | 1.07 Mbit/s | 3.54 s | 319, 62 screens |
| 2 | 22.9 | 1.88 Mbit/s | 3.14 s | 387, 96 screens |

Every rectangle decoded. The same path carried a raw 40 MB transfer from the Mac at
17.1 Mbit/s, so bandwidth was not the limit. The Mac paces its updates by how fast its
socket drains, and the two ssh hops add latency and flow-control windows. These runs
show that the protocol works, not how fast Pomona is: compare rates only over the same
path. The first picture includes the sign-in's 0.56 s and the round trips of the longer
path.
