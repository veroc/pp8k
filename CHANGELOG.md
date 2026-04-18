# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


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

[0.3.0]: https://github.com/veroc/pp8k/releases/tag/v0.3.0
[0.2.0]: https://github.com/veroc/pp8k/releases/tag/v0.2.0
[0.1.1]: https://github.com/veroc/pp8k/releases/tag/v0.1.1
[0.1.0]: https://github.com/veroc/pp8k/releases/tag/v0.1.0
