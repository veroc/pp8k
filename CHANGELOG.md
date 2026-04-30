# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [0.6.0] - 2026-04-30

### Fixed
- `MODE SENSE` parsing.  The active film slot is at byte 8 of the
  61-byte response, not byte 6 (which is a vendor status byte that's
  typically non-zero on a powered unit).  Earlier driver revisions
  reported the status byte as the slot number, which was harmless on
  freshly powered devices that hadn't received a MODE SELECT yet but
  wrong as soon as one was issued.
- `INQUIRY` now strips trailing null bytes from `identification`,
  `product`, and `revision`.  Real hardware (fw 568) returns
  `'568\x00'` for the revision string and `str.strip()` only removes
  whitespace, so the null bled through into `pp8k info` output.
- `serialize_flm` enforces null-termination on the 24-byte film name
  field by capping the payload at 23 bytes and forcing byte 23 to 0.
  Without this, a 24-char name caused the firmware to overrun into
  camera_type/flags/aspect (bytes 24-27), corrupting later metadata
  reads -- e.g. `DFRCMD ASPECT_RATIO` returning the wrong aspect.
  Round-trip of all 58 original Polaroid FLMs remains byte-perfect
  (every original name fits within 23 chars).
- CRT calibration polling timeouts in `run_exposure`.
  `CALIBRATION_WAIT_S` raised from 30 to 45 and `CALIBRATION_MIN_S`
  from 15 to 30, matching the >=30s real cal duration observed across
  21 consecutive exposures on fw 568.  The `exposure_state` flag never
  flipped within the old 30s window; the firmware accepts scanlines
  based on buffer state regardless, but the polling now matches the
  hardware's actual behaviour.

### Changed
- **Breaking:** `ModeState.frame_counter` renamed to
  `lifetime_exposures`.  The MODE SENSE bytes 58-59 read
  unit-lifetime exposure counts (35168 on the test unit -- consistent
  with years of pro use), not a session/frame counter that's reset
  somewhere.  `pp8k status` shows the field as `Lifetime exp:`.
- `HARDWARE_MAX_VRES` constant lowered from 7020 to 6710, matching
  the real device's `INQUIRY` response on fw 568.  `MockDevice.inquiry()`
  now returns `vres_max=6710`.  The 7020 figure was carried over from
  pre-hardware-test documentation and never matched any unit we've
  measured.


## [0.5.0] - 2026-04-30

### Added
- `image_to_scanlines()` and `Device.expose()` accept a new `rotation`
  argument: 0, 90, 180, or 270 degrees clockwise.  Applied after EXIF
  transpose, before fit/fill scaling.  Default 0 (no rotation), so
  existing callers are unaffected.
- `pp8k expose` CLI gains a `--rotation {0,90,180,270}` flag.

### Changed
- Backward-compatible signature extension; no breaking changes.


## [0.4.1] - 2026-04-30

### Fixed
- `S2pexecTransport.open()` now probes `/opt/scsi2pi/bin/s2pexec` (the
  scsi2pi .deb install location) and `/usr/local/bin/s2pexec` in
  addition to `PATH`.  Lets `sudo pp8k info <id>` work on a stock Pi
  setup without needing `/opt/scsi2pi/bin` in sudo's `secure_path`.


## [0.4.0] - 2026-04-30

### Added
- Slot-mode exposure: `Device.expose(image_path, slot=N, bw_filter=...)`
  and `pp8k expose <device> <image> --slot N [--filter red|green|blue]`
  expose against a pre-installed film table without uploading.
  Aspect is read from the device via DFRCMD sub 5; presence of
  `--filter` implies a single-pass B&W exposure.
- 2-master FLM normalisation.  `pp8k.normalize_masters(table)` rewrites
  Sets 0/2/4/6/7 from a single Master A and Sets 1/3/5 from
  `ceil(Master A / 2)`; Set 8 from Master B and Set 9 from
  `floor(Master B / 2)`.  `pp8k.validate_masters(table)` reports
  inter-set inconsistencies.  Matches the authoring convention used
  in 57/58 original Polaroid FLMs and prevents curve editors from
  producing files that load different curves at different HRES.
- Pluggable SCSI transport.  Two transports ship in the box and share
  a common `Transport` interface:
    - `SGIOTransport(path)` -- the existing Linux SG_IO ioctl path.
      No behaviour change for `/dev/sg*` users (Ubuntu, T60 + PCMCIA,
      anywhere with a Linux SCSI HBA).
    - `S2pexecTransport(scsi_id)` -- shells out to scsi2pi's
      `s2pexec` for Raspberry Pi systems with a PiSCSI HAT, where
      there is no `/dev/sg*` node.  Requires the scsi2pi package
      installed on the Pi.
  `pp8k.open()` now accepts either form:
  `pp8k.open("/dev/sg2")` -> SG_IO; `pp8k.open(4)` or
  `pp8k.open("4")` -> s2pexec at SCSI ID 4.

### Fixed
- Frame dimensions for 6×7 film backs.  Every 6×7 FLM stores aspect
  11:9 (per the RasterPlus95 driver docs §7.3 and confirmed against
  all 6×7 tables in the ProPalette SDK), giving 4096×3351 at 4K and
  8192×6702 at 8K.  The hardcoded table used 7:6 (4096×3510 /
  8192×7020), which stretched every 6×7 exposure vertically by ~5%.

### Changed
- `FRAME_DIMENSIONS` table removed.  `get_frame_dimensions()` now
  takes `(aspect_w, aspect_h, resolution)` and computes
  `vres = hres * aspect_h / aspect_w` on the fly.  This matches
  Polaroid's original programmable-resolution design (RasterPlus95
  supplement §6) and lets slot-mode expose work without a local
  camera-type table.  Minor 1-pixel rounding differences from the
  old table for 35mm @ 8K and 4×5 @ 4K.
- Public API: `Device.expose()` no longer requires `flm` positionally
  -- use `flm=` or `slot=` keyword.
- Public API: `pp8k.open(target=...)` -- the `device_path=` keyword
  is renamed to `target=`.  Positional callers (`pp8k.open("/dev/sg2")`)
  are unaffected.


## [0.3.1] - 2026-04-18

### Added
- `Device.install(slot, flm)` -- persist a parsed FLM to a device slot.
- New CLI command `pp8k install <device> <FLM> --slot N [--force]`.
  Prompts before overwriting an occupied slot (bypass with `--force`);
  warns when installing to slot 19 since `expose()` uses it as the
  scratch slot.


## [0.3.0] - 2026-04-18

### Added
- Three SCSI commands found in the original Polaroid SDK header
  (`tkdfpcmd.h`):
    - `GET_COLOR_TAB` (DFRCMD sub 2) -- read back the per-exposure
      gamma LUT for a channel.
    - `ASPECT_RATIO` (DFRCMD sub 5) -- read (width, height) aspect for
      a device slot without needing the FLM file.
    - `RESET_TO_DFLT` (DFRCMD sub 7) -- reset the device to machine
      defaults.
- `Device.film_aspect(slot)` and `Device.film_slots_info()` return
  aspect alongside slot names.
- `Device.reset()` high-level wrapper for RESET_TO_DFLT.
- New CLI command `pp8k reset`.
- `pp8k slots` now shows an aspect column.


## [0.2.0] - 2026-04-18

### Added
- FLM write support via `serialize_flm(table)` and `save_flm(path, table)`.
  Round-trips byte-perfectly on all 62 original Polaroid film tables tested.
- `_FilmTableCrypto.encrypt()` -- inverse of `decrypt()`.
- New offline CLI commands:
    - `pp8k flm show <FILE.FLM>` -- print header and 10-set LUT summary,
      or dump a single set's curves (optionally as CSV).
    - `pp8k flm validate <FILE.FLM>` -- structural sanity check with
      round-trip verification.
- New device CLI command:
    - `pp8k slots <device>` -- list films installed in all 20 device slots.
- `FilmTable` now preserves `flags` (raw header byte 25) and `raw_extended`
  (bytes 28-188 of the decrypted file header) to enable byte-perfect
  round-trip through `serialize_flm()`.
- `LutSet` now preserves the 10-byte per-set `header` (None for set 0).
- Public exports for `LutSet`, `LutChannel`, `save_flm`, `serialize_flm`.

### Changed
- `FilmTable` and `LutSet` gained new fields. The additions are at the end
  with defaults, so positional construction continues to work, but callers
  that build these objects from scratch should now populate the new fields
  if they intend to serialize back to a valid FLM file.


## [0.1.1] - 2026-04-17

### Added
- Human-readable device error codes: 106 ASC messages mapped from the
  firmware for clearer SCSI error diagnostics.

### Fixed
- Correct install URL in README.


## [0.1.0] - 2026-04-17

Initial release.

### Added
- Complete SCSI driver for the Polaroid ProPalette 8000 film recorder.
- `pp8k.open()` / `pp8k.mock()` -- real and mock device backends.
- `pp8k.load_flm()` -- parse and decrypt `.FLM` film table files.
- `Device.expose()` -- full exposure workflow: image conversion, film
  table upload, SCSI exposure protocol.
- CLI commands: `pp8k info`, `pp8k status`, `pp8k expose`.
- Image-to-scanline conversion with fit/fill transforms, letterbox
  background colors, EXIF orientation, and Lanczos resampling.
- Auto-detection of B&W vs color mode from the FLM header.
- Abort-capable exposure via `threading.Event`.
- Progress callbacks emitting `ExposureProgress` (phase, channel, lines
  sent/total, buffer state, ETA).

[0.6.0]: https://github.com/veroc/pp8k/releases/tag/v0.6.0
[0.5.0]: https://github.com/veroc/pp8k/releases/tag/v0.5.0
[0.4.1]: https://github.com/veroc/pp8k/releases/tag/v0.4.1
[0.4.0]: https://github.com/veroc/pp8k/releases/tag/v0.4.0
[0.3.1]: https://github.com/veroc/pp8k/releases/tag/v0.3.1
[0.3.0]: https://github.com/veroc/pp8k/releases/tag/v0.3.0
[0.2.0]: https://github.com/veroc/pp8k/releases/tag/v0.2.0
[0.1.1]: https://github.com/veroc/pp8k/releases/tag/v0.1.1
[0.1.0]: https://github.com/veroc/pp8k/releases/tag/v0.1.0
