# pp8k

A Python driver for the [Polaroid ProPalette 8000](https://en.wikipedia.org/wiki/Polaroid_ProPalette) film recorder -- one of the last and finest CRT-based film recorders ever made.

**pp8k** lets you expose digital images onto real photographic film (35mm, 4x5, 6x7, 6x8) directly from the command line or from Python. It handles everything: image scaling, film table management, SCSI device communication, and the full exposure protocol.

```
$ sudo pp8k expose /dev/sg2 photo.tiff --film PLUSXPAN.FLM
Film table:   Plus-X Pan 125 vP2 (35mm)
Type:         B&W [Green filter]
Resolution:   4k
Connected:    ProPalette 8K (fw 568)

[##############################] 100.0% [GREEN] (2730/2730 lines)
Exposure complete (67.3s total)
```


## What is this?

The ProPalette 8000 is a high-resolution film recorder from the late 1990s. It uses a precision CRT to draw images through color filters onto photographic film, producing slides and negatives at up to 8192 x 7020 pixels -- resolution that rivals or exceeds modern digital cameras.

These devices were originally driven by proprietary Windows software that no longer runs on modern systems. **pp8k** is a from-scratch Linux driver that speaks the ProPalette's SCSI protocol natively, giving these machines a second life.

The SCSI protocol and film table format were reverse-engineered through extensive analysis of the original Polaroid SDK, third-party tools, and real hardware testing. See [docs/scsi_protocol.md](docs/scsi_protocol.md) for the full protocol reference.


## Install

```bash
pip install git+https://github.com/veroc/pp8k.git
```

Requires Linux, Python 3.10+, and a SCSI connection to the device (any SCSI HBA -- PCI, PCMCIA, USB, or PiSCSI).


## Command line

### Device commands (require /dev/sgN and root)

```bash
# Device identification
sudo pp8k info /dev/sg2

# Current mode and configuration
sudo pp8k status /dev/sg2

# List the films installed in all 20 device slots (name + aspect)
sudo pp8k slots /dev/sg2

# Reset the device to machine-default state (clears errors, returns to idle)
sudo pp8k reset /dev/sg2

# Persist a film table to a slot (survives power cycles, flash write ~5-30s)
sudo pp8k install /dev/sg2 PLUSXPAN.FLM --slot 3
sudo pp8k install /dev/sg2 PLUSXPAN.FLM --slot 3 --force   # skip confirm

# Expose an image (B&W/color detected from film table)
sudo pp8k expose /dev/sg2 photo.tiff --film PLUSXPAN.FLM

# 8K resolution, fill frame (crop to fit)
sudo pp8k expose /dev/sg2 photo.tiff --film EKTA100.FLM --res 8k --transform fill

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
| `--film` | *(required)* | Path to .FLM film table file |
| `--res` | `4k` | Resolution: `4k` or `8k` |
| `--transform` | `fit` | `fit` (letterbox, no crop) or `fill` (crop to fill frame) |
| `--background` | `black` | Letterbox bar color: `black` or `white` |
| `--dry-run` | | Convert image and validate, don't send to device |


## Python API

```python
import pp8k

# Load a film table
flm = pp8k.load_flm("PLUSXPAN.FLM")

# Connect and expose
with pp8k.open("/dev/sg2") as device:
    print(device.info)    # DeviceInfo(product='ProPalette 8K', firmware=568, ...)
    print(device.mode)    # ModeState(film_number=4, hres=4096, vres=2730, ...)

    device.expose("photo.tiff", flm=flm)

# With all options
with pp8k.open("/dev/sg2") as device:
    device.expose(
        "photo.tiff",
        flm=flm,
        resolution="8k",
        transform="fill",
        background="white",
        on_progress=lambda p: print(f"{p.phase} {p.lines_sent}/{p.lines_total}"),
    )

# Mock device for development (no hardware needed)
with pp8k.mock() as device:
    device.expose("photo.tiff", flm=flm)
```

### Film table inspection

```python
flm = pp8k.load_flm("EKTA100.FLM")
flm.name            # "Ektachrome 100"
flm.camera_type_name # "35mm"
flm.is_bw           # False
flm.lut_sets[7]     # LutSet for 4K resolution (set index 7)
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
for all 62 original Polaroid film tables tested.


## Supported camera backs

Frame dimensions are determined by the film table's camera type:

| Camera back | 4K | 8K |
|---|---|---|
| 35mm | 4096 x 2730 | 8192 x 5462 |
| 4x5 | 4096 x 3184 | 8192 x 6371 |
| 6x7 | 4096 x 3510 | 8192 x 7020 |
| 6x8 | 4096 x 3072 | 8192 x 6144 |


## How it works

An exposure goes through these steps:

1. **Load film table** -- decrypt and parse the .FLM file to determine camera type, B&W/color mode, and filter channel
2. **Convert image** -- scale/crop to frame dimensions, split into per-channel scanlines
3. **Upload film table** -- send encrypted FLM data to the device via SCSI
4. **Configure device** -- MODE SELECT sets resolution, film slot, and servo mode
5. **Start exposure** -- the CRT runs a 15-25 second auto-calibration cycle
6. **Send scanlines** -- 50-line bursts, paced by buffer status polling (~340 KB/s throughput)
7. **Terminate** -- finalize exposure, advance film

For color film: three complete passes (Red, Green, Blue) through the filter wheel.
For B&W film: a single pass on the channel specified by the film table (typically Green).

Typical exposure times: ~65 seconds for 4K B&W, ~145 seconds for 4K color.


## Film tables

The PP8K requires `.FLM` film table files that define the tone mapping curves for each film stock. These encrypted lookup tables control how the CRT intensity translates to film density across all three color channels.

A collection of original Polaroid film tables for various film stocks is available from Phil Pemberton's [film recorder archive](https://www.philpem.me.uk/code/filmrec/start). Download the film table ZIP, pick the `.FLM` file that matches your film stock, and pass it to `pp8k expose` via the `--film` flag.


## Project structure

```
pp8k/
├── __init__.py     # public API: open(), mock(), load_flm(), Device
├── models.py       # NamedTuples: DeviceInfo, ModeState, ExposureProgress
├── errors.py       # exception hierarchy
├── constants.py    # frame dimensions, camera types, color channels
├── transport.py    # Linux SG_IO ioctl wrapper
├── commands.py     # SCSI command builders
├── scsi.py         # real hardware backend
├── mock.py         # mock backend for development
├── exposure.py     # exposure workflow orchestration
├── imaging.py      # image-to-scanline conversion (Pillow)
├── flm.py          # .FLM file decryption and parsing
└── cli.py          # command-line interface
```


## Documentation

- [SCSI Protocol Reference](docs/scsi_protocol.md) -- complete command documentation
- Source code is thoroughly commented with protocol details and hardware notes


## Requirements

- **OS:** Linux (uses the SCSI Generic `/dev/sgN` kernel interface)
- **Python:** 3.10+
- **Dependencies:** Pillow (image processing)
- **Hardware:** any SCSI host adapter -- PCI cards, PCMCIA CardBus, USB-SCSI adapters, or PiSCSI HAT on Raspberry Pi
- **Permissions:** root or udev rules for `/dev/sgN` access


## Support this project

If you find this useful, consider buying me a coffee:

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/veroc)


## Acknowledgements

Special thanks to [Phil Pemberton](https://www.philpem.me.uk/code/filmrec/start) for his incredible work documenting and preserving information about Polaroid Digital Palette film recorders. His research into the film table encryption, device protocols, and his collection of original film tables made this project possible.


## License

LGPL-3.0-or-later
