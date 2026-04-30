# pp8k

A Python driver for the [Polaroid ProPalette 8000](https://en.wikipedia.org/wiki/Polaroid_ProPalette) film recorder -- one of the last and finest CRT-based film recorders ever made.

**pp8k** lets you expose digital images onto real photographic film (35mm, 4x5, 6x7, 6x8) directly from the command line or from Python. It handles everything: image scaling, film table management, SCSI device communication, and the full exposure protocol.

```
$ sudo pp8k expose 4 photo.tiff --film PLUSXPAN.FLM
Film table:   Plus-X Pan 125 vP2 (35mm)
Type:         B&W [Green filter]
Resolution:   4k
Connected:    ProPalette 8K (fw 568)

[##############################] 100.0% [GREEN] (2730/2730 lines)
Exposure complete (70.8s total)
```


## Table of contents

- [What is this?](#what-is-this)
- [Verified on real hardware](#verified-on-real-hardware)
- [Install](#install)
- [Connecting to a device](#connecting-to-a-device)
- [Command line](#command-line)
- [Python API](#python-api)
- [Supported camera backs](#supported-camera-backs)
- [How it works](#how-it-works)
- [Film tables](#film-tables)
- [Driving the PP8K from a Raspberry Pi](#driving-the-pp8k-from-a-raspberry-pi)
- [Project structure](#project-structure)
- [Documentation](#documentation)
- [Requirements](#requirements)
- [Acknowledgements](#acknowledgements)
- [License](#license)


## What is this?

The ProPalette 8000 is a high-resolution film recorder from the late 1990s. It uses a precision CRT to draw images through color filters onto photographic film, producing slides and negatives at up to 8192 x 6710 pixels -- resolution that rivals or exceeds modern digital cameras.

These devices were originally driven by proprietary Windows software that no longer runs on modern systems. **pp8k** is a from-scratch Linux driver that speaks the ProPalette's SCSI protocol natively, giving these machines a second life.

The SCSI protocol and film table format were reverse-engineered through extensive analysis of the original Polaroid SDK, third-party tools, and real hardware testing. See [docs/scsi_protocol.md](docs/scsi_protocol.md) for the full protocol reference.


## Verified on real hardware

pp8k has been driven end-to-end against a real ProPalette 8000 (firmware 568) on two independent host configurations:

| Host | Bus | scsi2pi mode | Result |
|---|---|---|---|
| **Lenovo T60 + PCMCIA SCSI** | Linux SG_IO via `/dev/sg*` | n/a | 4K B&W exposure in 65 s |
| **Raspberry Pi 3B + PiSCSI v2.3 B HAT** | scsi2pi 6.2.1 (initiator mode) | `s2pexec` per CDB | 4K B&W exposure in 70.8 s |

The Pi run was an unattended 21-frame test roll on Plus-X 35mm at 4K (PLUSXPAN slot, --filter green, 1-pass B&W). pp8k's internal timer reported 70.8 s/frame; wall-clock 73.0 s/frame. The ~5.8 s delta against the PCMCIA reference is `s2pexec` subprocess overhead spread over 2730 scanlines — about 2.1 ms/scanline. Acceptable.

Both transports go through the same `pp8k.Device.expose()` API. Application code is identical between the two; only the `pp8k.open()` argument changes.

See [Driving the PP8K from a Raspberry Pi](#driving-the-pp8k-from-a-raspberry-pi) below for the full bring-up procedure.


## Install

```bash
pip install git+https://github.com/veroc/pp8k.git
```

Requires Linux and Python 3.10+. The only Python dependency is Pillow.

For the SCSI side you have two options, both supported by the same code path:

- **A regular Linux SCSI host adapter** -- any PCI/PCIe card, PCMCIA CardBus card, USB-SCSI bridge, or built-in HBA. The kernel exposes the device as `/dev/sgN` and pp8k drives it through SG_IO ioctls. No extra packages.
- **A Raspberry Pi with a PiSCSI HAT** -- bit-bangs the SCSI bus over GPIO. Requires the `scsi2pi` userland (Debian package). See the [Pi setup section](#driving-the-pp8k-from-a-raspberry-pi).


## Connecting to a device

`pp8k.open()` (and the CLI's `<device>` argument) accepts two forms and dispatches automatically:

| Form | Transport | When to use |
|---|---|---|
| Path string, e.g. `"/dev/sg2"` | `SGIOTransport` (kernel SG_IO ioctl) | Linux machine with a SCSI HBA |
| Integer or digit-string, e.g. `4` or `"4"` | `S2pexecTransport` (shells out to `s2pexec`) | Raspberry Pi with a PiSCSI HAT and `scsi2pi` installed |

PP8K's default SCSI ID is **4** (changeable via the front-panel keypad). On the Pi with the HAT, that's the typical argument.

```bash
# On a Linux workstation with a SCSI HBA:
sudo pp8k info /dev/sg2

# On a Raspberry Pi with PiSCSI HAT:
sudo pp8k info 4
```

```python
import pp8k

# Pick the form that matches your host:
device = pp8k.open("/dev/sg2")   # SCSI HBA path
device = pp8k.open(4)            # PiSCSI HAT, SCSI ID 4
device = pp8k.mock()             # no hardware -- mock backend for development
```

Examples in the rest of the README use the path form for brevity. Substitute the SCSI ID where needed.


## Command line

### Device commands (require root)

```bash
# Device identification
sudo pp8k info /dev/sg2
sudo pp8k info 4                                                # PiSCSI HAT

# Current mode and configuration
sudo pp8k status /dev/sg2

# List the films installed in all 20 device slots (name + aspect)
sudo pp8k slots /dev/sg2

# Reset the device to machine-default state (clears errors, returns to idle)
sudo pp8k reset /dev/sg2

# Persist a film table to a slot (survives power cycles, flash write ~5-30s)
sudo pp8k install /dev/sg2 PLUSXPAN.FLM --slot 3
sudo pp8k install /dev/sg2 PLUSXPAN.FLM --slot 3 --force        # skip confirm

# Expose an image (B&W/color detected from film table)
sudo pp8k expose /dev/sg2 photo.tiff --film PLUSXPAN.FLM
sudo pp8k expose 4         photo.tiff --film PLUSXPAN.FLM       # PiSCSI HAT

# 8K resolution, fill frame (crop to fit)
sudo pp8k expose /dev/sg2 photo.tiff --film EKTA100.FLM --res 8k --transform fill

# Use a pre-installed slot instead of uploading (aspect read from device)
sudo pp8k expose /dev/sg2 photo.tiff --slot 3                   # 3-pass color
sudo pp8k expose /dev/sg2 photo.tiff --slot 3 --filter green    # 1-pass B&W

# Validate everything without touching the device
sudo pp8k expose /dev/sg2 photo.tiff --film PLUSXPAN.FLM --dry-run
```

### Offline FLM inspection (no device required)

```bash
# Print film table header and a summary of all 10 LUT sets
pp8k flm show PLUSXPAN.FLM

# Dump one LUT set's curves, sampled every 16 entries
pp8k flm show PLUSXPAN.FLM --set 7

# Emit one LUT set as CSV
pp8k flm show PLUSXPAN.FLM --set 7 --csv > plusxpan-set7.csv

# Structural sanity check (size, decrypt, round-trip, header fields)
pp8k flm validate PLUSXPAN.FLM
```

### Expose options

| Option | Default | Description |
|--------|---------|-------------|
| `--film` | *(required, or use `--slot`)* | Path to .FLM film table file |
| `--slot` | *(required, or use `--film`)* | Use a film table already installed in a device slot |
| `--filter` | *(none)* | Force 1-pass B&W on `red`/`green`/`blue` (slot mode only) |
| `--res` | `4k` | Resolution: `4k` or `8k` |
| `--transform` | `fit` | `fit` (letterbox, no crop) or `fill` (crop to fill frame) |
| `--background` | `black` | Letterbox bar color: `black` or `white` |
| `--rotation` | `0` | Clockwise rotation: `0`, `90`, `180`, `270` |
| `--dry-run` | | Convert image and validate, don't send to device |


## Python API

```python
import pp8k

# Load a film table
flm = pp8k.load_flm("PLUSXPAN.FLM")

# Connect and expose
with pp8k.open("/dev/sg2") as device:                           # or pp8k.open(4)
    print(device.info)    # DeviceInfo(product='ProPalette 8K', firmware=568, ...)
    print(device.mode)    # ModeState(film_number=4, hres=4096, vres=2730, ..., lifetime_exposures=35168)

    device.expose("photo.tiff", flm=flm)

# With all options
with pp8k.open(4) as device:
    device.expose(
        "photo.tiff",
        flm=flm,
        resolution="8k",
        transform="fill",
        background="white",
        rotation=90,
        on_progress=lambda p: print(f"{p.phase} {p.lines_sent}/{p.lines_total}"),
    )

# Mock device for development (no hardware needed)
with pp8k.mock() as device:
    device.expose("photo.tiff", flm=flm)
```

### Film table inspection

```python
flm = pp8k.load_flm("EKTA100.FLM")
flm.name             # "Ektachrome 100"
flm.camera_type_name # "35mm"
flm.is_bw            # False
flm.lut_sets[7]      # LutSet for 4K resolution (set index 7)
```

### Writing film tables

`FilmTable`, `LutSet`, and `LutChannel` are `NamedTuple`s. Use `_replace()`
to build modified copies and write them back with `save_flm()`:

```python
import pp8k

flm = pp8k.load_flm("PLUSXPAN.FLM")

# Swap in a new name and scale the set 7 red channel
new_set = flm.lut_sets[7]._replace(scale_r=4)
modified = flm._replace(
    name="PLUSXPAN custom",
    lut_sets=flm.lut_sets[:7] + (new_set,) + flm.lut_sets[8:],
)

pp8k.save_flm("PLUSXPAN_custom.FLM", modified)

# Or get the encrypted bytes directly (e.g. for in-memory upload)
blob = pp8k.serialize_flm(modified)   # 15,639 encrypted bytes
```

Round-trip is byte-perfect: `serialize_flm(load_flm(path)) == open(path, "rb").read()`
for all 58 original Polaroid film tables tested.

### 2-master normalisation

Original Polaroid FLMs follow a strict 2-master authoring convention: Sets 0/2/4/6/7 are byte-identical to a "Master A" curve, Sets 1/3/5 are `ceil(Master A / 2)`, and Set 9 is `floor(Set 8 / 2)`. Editing one set in isolation breaks calibration because the firmware loads different curves at different HRES values. Use `normalize_masters()` to propagate edits cleanly:

```python
fixed = pp8k.normalize_masters(modified)   # rewrites derived sets from Set 7 + Set 9
issues = pp8k.validate_masters(flm)        # returns [] if the file conforms
```


## Supported camera backs

Frame dimensions are computed at runtime from each FLM's stored aspect
ratio: `vres = hres * aspect_h // aspect_w`.  The values below are what
`pp8k.get_frame_dimensions()` returns today.

| Camera back | Aspect | 4K (computed) | 8K (computed) |
|---|---|---|---|
| 35mm | 3:2 | 4096 x 2730 | 8192 x 5461 |
| 4x5 | 5:4 | 4096 x 3276 | 8192 x 6553 |
| 6x7 | 11:9 | 4096 x 3351 | 8192 x 6702 |
| 6x8 | 4:3 | 4096 x 3072 | 8192 x 6144 |

Maximum addressable area is 8192 x 6710. All values fit within hardware
limits.

### Preferred geometry on the 64-pixel grid

The PP8K's V53 video controller works on a 64-pixel grid: HRES (and
strictly speaking VRES) must be a multiple of 64, and the firmware pads
non-conforming requests up to the next ×64 boundary internally. HRES
values of 4096 and 8192 already conform; VRES does not, so the table
below lists the largest multiple of 64 at or below the computed VRES.
Using these values avoids the firmware-side padding and gives a
predictable raster:

| Camera back | 4K (×64) | 8K (×64) |
|---|---|---|
| 35mm | 4096 x 2688 | 8192 x 5440 |
| 4x5 | 4096 x 3264 | 8192 x 6528 |
| 6x7 | 4096 x 3328 | 8192 x 6656 |
| 6x8 | 4096 x 3072 | 8192 x 6144 |

`pp8k.get_frame_dimensions()` does not snap to 64 today -- it returns
the aspect-correct computed value. Snap manually if your downstream
pipeline cares about the grid alignment.


## How it works

An exposure goes through these steps:

1. **Load film table** -- decrypt and parse the .FLM file to determine camera type, B&W/color mode, and filter channel
2. **Convert image** -- scale/crop to frame dimensions, split into per-channel scanlines
3. **Upload film table** -- send encrypted FLM data to the device via SCSI
4. **Configure device** -- MODE SELECT sets resolution, film slot, and servo mode
5. **Start exposure** -- the CRT runs a 30-45 second auto-calibration cycle
6. **Send scanlines** -- 50-line bursts, paced by buffer status polling (~340 KB/s throughput)
7. **Terminate** -- finalize exposure, advance film

For color film: three complete passes (Red, Green, Blue) through the filter wheel.
For B&W film: a single pass on the channel specified by the film table (typically Green).

Typical exposure times: ~65 s for 4K B&W on a Linux HBA, ~70 s through a PiSCSI HAT, ~145 s for 4K color.


## Film tables

The PP8K requires `.FLM` film table files that define the tone mapping curves for each film stock. These encrypted lookup tables control how the CRT intensity translates to film density across all three color channels.

A collection of original Polaroid film tables for various film stocks is available from Phil Pemberton's [film recorder archive](https://www.philpem.me.uk/code/filmrec/start). Download the film table ZIP, pick the `.FLM` file that matches your film stock, and pass it to `pp8k expose` via the `--film` flag.


## Driving the PP8K from a Raspberry Pi

This is the recommended setup if you don't have a vintage SCSI host laying around. The PP8K's bus speaks SCSI-1 single-ended over a Centronics-50 connector, and the [PiSCSI](https://github.com/akuker/PISCSI/wiki) HAT family bit-bangs that bus over the Pi's GPIO header. With pp8k + the `scsi2pi` userland on top, a $30 Pi 3B is a complete drop-in replacement for the long-dead Windows host the PP8K was originally bundled with.

### Why scsi2pi (and not upstream PiSCSI)

The PiSCSI ecosystem has two software stacks: the original [akuker/PISCSI](https://github.com/akuker/PISCSI) project and Uwe Seimet's [scsi2pi](https://www.scsi2pi.net) fork. Both target the same FullSpec v2.x boards. **For driving a PP8K, scsi2pi is effectively the only viable option** -- and that's not a soft preference.

| Concern | Upstream PiSCSI | scsi2pi |
|---|---|---|
| Initiator-mode focus | No -- mainly target-mode (emulating drives for vintage hosts) | **Yes** -- forked specifically because upstream "had no interest in exploiting the initiator mode" (Uwe's words) |
| Arbitrary CDB injection | `scsidump` only handles disk dumps; vendor commands need C++ internal linking | **`s2pexec`** -- pass any CDB and data buffer, get the response back. The PP8K is 90% vendor commands |
| Pre-built packages | Source-only; C++20 build OOMs on 1 GB Pis (Pi 3B, Zero 2 W) and takes ~20 minutes | **Pre-built .deb** packages for Bullseye, Bookworm, and Trixie |
| Cross-host portability | tied to the PiSCSI HAT | `s2pexec --scsi-generic /dev/sg*` also drives a regular Linux SCSI HBA -- same CLI on a Pi and on a workstation with a PCMCIA card |

The hardware (any FullSpec PiSCSI v2.x board) is shared between the two stacks. Only the userland differs.

pp8k's `S2pexecTransport` shells out to `s2pexec` once per CDB. The per-call subprocess overhead is ~2.1 ms on a Pi 3B -- imperceptible for setup commands and a small fixed tax on the scanline burst loop (about 5.8 s per 4K frame).

### Hardware

- **Pi:** Raspberry Pi 3B verified; Pi Zero 2 W and Pi 4 are pin-compatible with the same HAT.
- **HAT:** PiSCSI v2.3 B FullSpec board (the DB-25 variant, e.g. from amigastore.com). The DB-9 / "external" variants also work.
- **Cable:** DB-25 (HAT side) to Centronics-50 (PP8K side). The PP8K has two Centronics-50 ports wired as a daisy-chain pass-through; either works.
- **Termination:** the PP8K has internal active termination, software-toggled from the front panel keypad. **You must enable it** -- the bus is not viable until you do. Default termination state from factory is OFF.
- **TERMPWR:** the HAT supplies it from the Pi's 5V rail (fused, with a Schottky diode). The PP8K supplies its own end via internal termination once enabled.

### Quick setup

The condensed path below is enough to get `pp8k info 4` returning a clean INQUIRY response from the PP8K. It assumes you've already provisioned the Pi itself (Pi OS Lite, SSH, hostname, time) -- only the SCSI-specific steps are listed here.

#### 1. Install scsi2pi from the pre-built package

```bash
# Pi OS Trixie 64-bit (substitute _bookworm_arm64 / _armhf as appropriate)
cd ~
wget https://www.scsi2pi.net/packages/releases/scsi2pi_6.2.1_trixie_arm64-1.deb
sudo apt install ./scsi2pi_6.2.1_trixie_arm64-1.deb
```

The leading `./` matters. Without it apt treats `scsi2pi` as a repo name.

#### 2. Add `/opt/scsi2pi/bin` to PATH (and to sudo's secure_path)

```bash
sudo tee /etc/profile.d/scsi2pi.sh >/dev/null <<'EOF'
export PATH="$PATH:/opt/scsi2pi/bin"
EOF
source /etc/profile.d/scsi2pi.sh

sudo tee /etc/sudoers.d/scsi2pi-path >/dev/null <<'EOF'
Defaults secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/scsi2pi/bin"
EOF
sudo chmod 440 /etc/sudoers.d/scsi2pi-path
sudo visudo -cf /etc/sudoers.d/scsi2pi-path
```

`pp8k` 0.4.1 and later also probe `/opt/scsi2pi/bin/s2pexec` directly, so the sudoers tweak is optional -- but every other scsi2pi tool benefits from it too.

#### 3. Install pp8k

```bash
sudo apt install -y python3-venv python3-pip
python3 -m venv ~/pp8k-env
~/pp8k-env/bin/pip install git+https://github.com/veroc/pp8k.git
sudo ln -s ~/pp8k-env/bin/pp8k /usr/local/bin/pp8k
```

#### 4. Wire up the PP8K and enable termination

1. Power everything off.
2. Seat the HAT on the Pi's GPIO header.
3. Connect the DB-25 to Centronics-50 cable between the HAT and either of the PP8K's two Centronics-50 ports.
4. Power the PP8K on; wait for the LCD to settle.
5. **On the PP8K's front keypad:** `Escape` → `Setup` → `SCSI Terminated?` → `Yes`. While you're there, confirm the SCSI ID (default `4`).
6. Power the Pi on. If it fails to come up on the network within a minute, kill power immediately -- that points at a TERMPWR short in the cable.

#### 5. Confirm the connection

```bash
sudo pp8k info 4
```

Expected output:

```
Product:      ProPalette 8K
Firmware:     568
Revision:     '568'
Buffer:       2456 KB
Max H.res:    8192
Max V.res:    6710
Ident:        DP2SCSI
```

If you see this, the driver has end-to-end initiator access to the device. Everything else is software.

If `info 4` hangs or returns "no device", in rough order of likelihood:

- **Termination not enabled on the PP8K** -- re-walk the keypad menu.
- **Wrong SCSI ID** -- check the PP8K's front panel and pass that integer to `pp8k`.
- **Cable seating** -- Centronics-50 connectors are stiff; reseat both ends firmly.
- **Cable wiring** -- DB-25-to-Centronics-50 cables are not all wired the same. Apple SCSI-2-standard wiring is what the PP8K expects; some budget cables omit GND returns or TERMPWR.

### Optional: neutralize scsi2pi's target-mode daemon

scsi2pi ships an `s2p` systemd service that runs the *target-mode* daemon (used to emulate disks for vintage Apple/Atari/Amiga hosts). It defaults to disabled, so it won't auto-start, but if you want belt-and-braces:

```bash
# This unit lives in /etc/systemd/system/ (vendor-style), so `mask` won't work.
# disabled is enough; nothing pulls s2p in as a dependency.
systemctl is-enabled s2p   # should print "disabled"
```

pp8k only ever invokes `s2pexec`, never `s2p`.


## Project structure

```
pp8k/
├── __init__.py     # public API: open(), mock(), load_flm(), Device
├── models.py       # NamedTuples: DeviceInfo, ModeState, ExposureProgress
├── errors.py       # exception hierarchy
├── constants.py    # frame dimensions, camera types, color channels
├── transport.py    # SG_IO ioctl + s2pexec subprocess transports
├── commands.py     # SCSI command builders
├── scsi.py         # real hardware backend
├── mock.py         # mock backend for development
├── exposure.py     # exposure workflow orchestration
├── imaging.py      # image-to-scanline conversion (Pillow)
├── flm.py          # .FLM file decryption, parsing, master normalisation
└── cli.py          # command-line interface
```


## Documentation

- [SCSI Protocol Reference](docs/scsi_protocol.md) -- complete command documentation
- [CHANGELOG](CHANGELOG.md) -- per-release detail
- Source code is thoroughly commented with protocol details and hardware notes


## Requirements

- **OS:** Linux (uses the SCSI Generic `/dev/sgN` kernel interface for HBAs, or `scsi2pi` for the PiSCSI HAT path)
- **Python:** 3.10+
- **Dependencies:** Pillow (image processing)
- **Hardware:** any SCSI host adapter (PCI, PCMCIA, USB-SCSI), or a Raspberry Pi 3B / Zero 2 W / 4 with a PiSCSI v2.x FullSpec HAT
- **Permissions:** root or appropriate udev rules for `/dev/sgN` access (HBA path) or GPIO (PiSCSI HAT path)


## Support this project

If you find this useful, consider buying me a coffee:

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/veroc)


## Acknowledgements

Special thanks to [Phil Pemberton](https://www.philpem.me.uk/code/filmrec/start) for his incredible work documenting and preserving information about Polaroid Digital Palette film recorders. His research into the film table encryption, device protocols, and his collection of original film tables made this project possible.

Thanks to Uwe Seimet for [scsi2pi](https://www.scsi2pi.net) -- the initiator-mode userland that makes the PiSCSI HAT path practical.


## License

LGPL-3.0-or-later
