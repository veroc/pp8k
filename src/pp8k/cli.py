"""Command-line interface for the pp8k driver.

Device commands (require a SCSI device path, e.g. /dev/sg2, and root):

    pp8k info <device>                      -- device identification
    pp8k status <device>                    -- current mode and state
    pp8k slots <device>                     -- list films in device slots 0-19
    pp8k reset <device>                     -- reset device to default state
    pp8k install <device> <FLM> --slot N    -- persist FLM to a device slot
    pp8k expose <device> <image> --film <FLM> [options]
    pp8k expose <device> <image> --slot N [--filter C] [options]

Offline FLM inspection (no device required):

    pp8k flm show <FLM> [--set N] [--csv]   -- inspect header and LUT data
    pp8k flm validate <FLM>                 -- structural sanity check
"""

import argparse
import struct
import sys
import time

import pp8k
from pp8k.flm import FILE_HEADER_SIZE, FLM_FILE_SIZE, LUT_DATA_SIZE, LUT_SETS_COUNT, SET_HEADER_SIZE
from pp8k.models import ExposureProgress


def _progress_printer(p):
    """Print exposure progress as an updating status line."""
    if p.phase == "setup":
        print("Setting up exposure...", flush=True)
    elif p.phase == "calibrating":
        print(f"\rCalibrating CRT... ({p.elapsed_seconds:.0f}s)", end="", flush=True)
    elif p.phase == "sending":
        pct = (p.lines_sent / p.lines_total * 100) if p.lines_total > 0 else 0
        bar_len = 30
        filled = int(bar_len * pct / 100)
        bar = "#" * filled + "-" * (bar_len - filled)
        channel = f" [{p.channel}]" if p.channel else ""
        eta = f" ETA {p.eta_seconds:.0f}s" if p.eta_seconds > 0 else ""
        print(
            f"\r[{bar}] {pct:5.1f}%{channel} "
            f"({p.lines_sent}/{p.lines_total} lines){eta}   ",
            end="", flush=True,
        )
    elif p.phase == "finishing":
        print("\nFinalizing exposure...", flush=True)
    elif p.phase == "complete":
        print(f"Exposure complete ({p.elapsed_seconds:.1f}s total)", flush=True)
    elif p.phase == "error":
        print(f"\nExposure FAILED: {p.error}", file=sys.stderr, flush=True)
    elif p.phase == "aborted":
        print("\nExposure aborted.", flush=True)


def cmd_info(args):
    """Print device identification."""
    device = pp8k.open(args.device)
    try:
        info = device.info
        print(f"Product:      {info.product}")
        print(f"Firmware:     {info.firmware}")
        print(f"Revision:     {info.revision!r}")
        print(f"Buffer:       {info.buffer_kb} KB")
        print(f"Max H.res:    {info.hres_max}")
        print(f"Max V.res:    {info.vres_max}")
        print(f"Ident:        {info.identification}")
    finally:
        device.close()
    return 0


def cmd_status(args):
    """Print current device mode and state."""
    device = pp8k.open(args.device)
    try:
        mode = device.mode
        print(f"Film slot:    {mode.film_number}")
        print(f"Resolution:   {mode.hres} x {mode.vres}")
        print(f"Luminance:    R={mode.lum_rgb[0]} G={mode.lum_rgb[1]} B={mode.lum_rgb[2]}")
        print(f"Color bal:    R={mode.cbal_rgb[0]} G={mode.cbal_rgb[1]} B={mode.cbal_rgb[2]}")
        print(f"Exp. time:    R={mode.etime_rgb[0]} G={mode.etime_rgb[1]} B={mode.etime_rgb[2]}")
        print(f"Camera back:  {mode.camera_back}")
        print(f"Frame count:  {mode.frame_counter}")
        print(f"Buffer:       {mode.buffer_kb} KB")
    finally:
        device.close()
    return 0


def cmd_slots(args):
    """List films installed in all 20 device slots with aspect ratio."""
    device = pp8k.open(args.device)
    try:
        print(f"{'Slot':<5} {'Film name':<24} {'Aspect':<8}")
        print(f"{'-' * 4:<5} {'-' * 23:<24} {'-' * 7:<8}")
        for entry in device.film_slots_info():
            name = entry["name"] or "(empty)"
            aspect = entry["aspect"]
            aspect_str = f"{aspect[0]}:{aspect[1]}" if aspect else "-"
            print(f"{entry['slot']:<5} {name:<24} {aspect_str:<8}")
    finally:
        device.close()
    return 0


def cmd_reset(args):
    """Reset the device to machine-default state."""
    device = pp8k.open(args.device)
    try:
        device.reset()
        print("Device reset to default state.")
    finally:
        device.close()
    return 0


def cmd_install(args):
    """Persist an FLM film table to a device slot."""
    if not 0 <= args.slot <= 19:
        print(f"Error: --slot must be 0-19, got {args.slot}", file=sys.stderr)
        return 1

    # Parse the FLM up-front so a bad file fails before we touch the device.
    flm = pp8k.load_flm(args.file)

    device = pp8k.open(args.device)
    try:
        existing = device.film_name(args.slot)
        print(f"Slot {args.slot}: {existing if existing else '(empty)'}")
        print(f"New:     {flm.name!r} ({flm.internal_name})")

        if existing and not args.force:
            if not sys.stdin.isatty():
                print(
                    f"Error: slot {args.slot} contains {existing!r}. "
                    f"Use --force to overwrite non-interactively.",
                    file=sys.stderr,
                )
                return 1
            answer = input(f"Overwrite slot {args.slot}? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.")
                return 1

        if args.slot == 19:
            print("Warning: slot 19 is used as the scratch slot by expose() "
                  "and will be overwritten on the next exposure.")

        print(f"Writing {len(flm.encrypted_data)} bytes to slot {args.slot} "
              "(flash write, ~5-30s)...")
        device.install(args.slot, flm)
        print(f"Installed {flm.name!r} to slot {args.slot}.")
    finally:
        device.close()
    return 0


def cmd_flm_show(args):
    """Print FLM header and LUT summary (no device needed)."""
    flm = pp8k.load_flm(args.file)

    if args.set is None:
        # Header + 10-set summary
        print(f"Name:          {flm.name!r}")
        print(f"Internal:      {flm.internal_name!r}")
        print(f"Camera:        {flm.camera_type_name} ({flm.camera_type})")
        print(f"Aspect:        {flm.aspect_w}:{flm.aspect_h}")
        print(f"Type:          {'B&W' if flm.is_bw else 'Color'}"
              + (f" [{flm.bw_filter_name} filter]" if flm.is_bw else ""))
        print(f"Flags:         0x{flm.flags:02X}")
        print(f"File size:     {len(flm.encrypted_data)} bytes")
        print()
        print(f"{'Set':<4} {'Resolution':<10} {'scale R':<8} {'scale G':<8} {'scale B':<8} {'Header':<24}")
        print(f"{'-' * 3:<4} {'-' * 9:<10} {'-' * 7:<8} {'-' * 7:<8} {'-' * 7:<8} {'-' * 23:<24}")
        for i, lut in enumerate(flm.lut_sets):
            if lut.header is not None:
                res = struct.unpack_from("<H", lut.header, 0)[0]
                hdr_hex = " ".join(f"{b:02X}" for b in lut.header)
            else:
                res = "(base)"
                hdr_hex = "-- no per-set header --"
            print(f"{i:<4} {str(res):<10} {lut.scale_r:<8} {lut.scale_g:<8} {lut.scale_b:<8} {hdr_hex:<24}")
        return 0

    # Single-set curve dump
    if args.set < 0 or args.set >= LUT_SETS_COUNT:
        print(f"Error: set must be 0-{LUT_SETS_COUNT - 1}", file=sys.stderr)
        return 1
    lut = flm.lut_sets[args.set]
    if args.csv:
        print("index,red,green,blue")
        for i in range(256):
            print(f"{i},{lut.red.values[i]},{lut.green.values[i]},{lut.blue.values[i]}")
    else:
        print(f"Set {args.set}: scale R={lut.scale_r} G={lut.scale_g} B={lut.scale_b}")
        print(f"{'idx':<4} {'R':<7} {'G':<7} {'B':<7}")
        for i in range(0, 256, 16):
            print(f"{i:<4} {lut.red.values[i]:<7} {lut.green.values[i]:<7} {lut.blue.values[i]:<7}")
    return 0


def cmd_flm_validate(args):
    """Validate an FLM file: size, decrypt, round-trip, structural sanity."""
    from pathlib import Path
    path = Path(args.file)
    problems = []
    warnings = []

    # Size check
    raw = path.read_bytes()
    if len(raw) != FLM_FILE_SIZE:
        problems.append(f"File size {len(raw)} != {FLM_FILE_SIZE}")
        print(f"FAIL: {path.name}")
        for p in problems:
            print(f"  - {p}")
        return 1

    # Parse
    try:
        flm = pp8k.load_flm(path)
    except Exception as e:
        problems.append(f"Parse failed: {e}")
        print(f"FAIL: {path.name}")
        for p in problems:
            print(f"  - {p}")
        return 1

    # Byte-perfect round-trip
    rebuilt = pp8k.serialize_flm(flm)
    if rebuilt != raw:
        diff = sum(1 for a, b in zip(rebuilt, raw) if a != b)
        problems.append(f"Round-trip differs in {diff} bytes")

    # Structural checks
    if not flm.name.strip():
        warnings.append("Empty film name")
    if not flm.internal_name.strip():
        warnings.append("Empty internal_name (8-char ID)")
    if flm.camera_type > 5:
        warnings.append(f"Unknown camera_type {flm.camera_type}")
    if flm.aspect_w == 0 or flm.aspect_h == 0:
        warnings.append(f"Zero aspect component: {flm.aspect_w}:{flm.aspect_h}")

    # Set 0 has no header; sets 1-9 must have 10-byte headers
    for i, lut in enumerate(flm.lut_sets):
        if i == 0 and lut.header is not None:
            problems.append(f"Set 0 should not have a per-set header")
        elif i > 0 and (lut.header is None or len(lut.header) != SET_HEADER_SIZE):
            problems.append(f"Set {i} header invalid")

    # Per-set resolutions should be non-zero for sets 1-9
    for i in range(1, LUT_SETS_COUNT):
        if flm.lut_sets[i].header is not None:
            res = struct.unpack_from("<H", flm.lut_sets[i].header, 0)[0]
            if res == 0:
                warnings.append(f"Set {i}: resolution field is 0")

    # 2-master authoring convention: 57/58 original Polaroid FLMs follow it.
    # Inconsistencies indicate a hand-edited file that may load different
    # curves at different HRES values, breaking calibration.  Reported as
    # warnings (round-trip is unaffected).
    for issue in pp8k.validate_masters(flm):
        warnings.append(f"master pattern: {issue}")

    status = "OK" if not problems else "FAIL"
    print(f"{status}: {path.name}")
    print(f"  Name:       {flm.name!r}")
    print(f"  Internal:   {flm.internal_name!r}")
    print(f"  Camera:     {flm.camera_type_name}")
    print(f"  Type:       {'B&W' if flm.is_bw else 'Color'}"
          + (f" [{flm.bw_filter_name}]" if flm.is_bw else ""))
    print(f"  Size:       {len(raw)} bytes")
    print(f"  Round-trip: {'byte-perfect' if rebuilt == raw else 'DIFFERS'}")
    for p in problems:
        print(f"  - PROBLEM: {p}")
    for w in warnings:
        print(f"  - warning: {w}")
    return 0 if not problems else 1


def cmd_expose(args):
    """Run an exposure, using either an FLM file or a pre-installed slot."""
    if (args.film is None) == (args.slot is None):
        print("Error: pass exactly one of --film or --slot.", file=sys.stderr)
        return 1

    flm = None
    aspect_w = aspect_h = None
    is_bw = False

    if args.film is not None:
        # FLM mode
        flm = pp8k.load_flm(args.film)
        aspect_w, aspect_h = flm.aspect_w, flm.aspect_h
        is_bw = flm.is_bw
        print(f"Film table:   {flm.name} ({flm.camera_type_name})")
        print(f"Type:         {'B&W' if flm.is_bw else 'Color'}"
              + (f" [{flm.bw_filter_name} filter]" if flm.is_bw else ""))
        if args.filter is not None:
            print("Warning: --filter is ignored in FLM mode "
                  "(B&W/channel come from the FLM header).")
    # Slot mode resolves aspect after connecting; print what we know now.
    print(f"Resolution:   {args.res}")
    print(f"Transform:    {args.transform}")
    print(f"Background:   {args.background}")
    print(f"Image:        {args.image}")

    if args.dry_run:
        if args.slot is not None:
            # Without a device we can't query aspect; ask for FLM in dry-run.
            print("Error: --dry-run requires --film (slot aspect needs the device).",
                  file=sys.stderr)
            return 1
        from pp8k.imaging import get_frame_dimensions, image_to_scanlines
        width, height = get_frame_dimensions(aspect_w, aspect_h, args.res)
        print(f"Frame size:   {width} x {height}")
        print("Converting image...", flush=True)
        t0 = time.monotonic()
        scanlines = image_to_scanlines(
            args.image, width, height, args.transform, args.background, is_bw,
        )
        dt = time.monotonic() - t0
        print(f"Converted in {dt:.1f}s ({len(scanlines[0])} lines, {width} px wide)")
        print("DRY RUN -- no device commands sent.")
        return 0

    print(f"Connecting to {args.device}...")
    device = pp8k.open(args.device)
    try:
        print(f"Connected: {device.info.product} (fw {device.info.firmware})")
        if args.slot is not None:
            name = device.film_name(args.slot)
            if name is None:
                print(f"Error: slot {args.slot} is empty.", file=sys.stderr)
                return 1
            aspect = device.film_aspect(args.slot)
            filter_note = f" [{args.filter} filter, 1 pass]" if args.filter else " [3-pass color]"
            print(f"Slot {args.slot}:       {name}  {aspect[0]}:{aspect[1]}{filter_note}")
        print()
        device.expose(
            image_path=args.image,
            flm=flm,
            slot=args.slot,
            bw_filter=args.filter,
            resolution=args.res,
            transform=args.transform,
            background=args.background,
            on_progress=_progress_printer,
        )
    finally:
        device.close()
    return 0


def main():
    """Entry point for the pp8k CLI."""
    parser = argparse.ArgumentParser(
        prog="pp8k",
        description="Polaroid ProPalette 8000 film recorder driver",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- pp8k info ---
    p_info = subparsers.add_parser(
        "info", help="Print device identification",
    )
    p_info.add_argument("device", help="SCSI device: /dev/sg* path (SG_IO) or SCSI ID 0-7 (s2pexec/PiSCSI HAT)")

    # --- pp8k status ---
    p_status = subparsers.add_parser(
        "status", help="Print current device mode and state",
    )
    p_status.add_argument("device", help="SCSI device: /dev/sg* path (SG_IO) or SCSI ID 0-7 (s2pexec/PiSCSI HAT)")

    # --- pp8k slots ---
    p_slots = subparsers.add_parser(
        "slots", help="List films installed in device slots 0-19",
    )
    p_slots.add_argument("device", help="SCSI device: /dev/sg* path (SG_IO) or SCSI ID 0-7 (s2pexec/PiSCSI HAT)")

    # --- pp8k reset ---
    p_reset = subparsers.add_parser(
        "reset", help="Reset device to machine-default state",
    )
    p_reset.add_argument("device", help="SCSI device: /dev/sg* path (SG_IO) or SCSI ID 0-7 (s2pexec/PiSCSI HAT)")

    # --- pp8k install ---
    p_install = subparsers.add_parser(
        "install", help="Persist a .FLM film table to a device slot",
    )
    p_install.add_argument("device", help="SCSI device: /dev/sg* path (SG_IO) or SCSI ID 0-7 (s2pexec/PiSCSI HAT)")
    p_install.add_argument("file", help="Path to .FLM file")
    p_install.add_argument(
        "--slot", type=int, required=True,
        help="Target slot number (0-19)",
    )
    p_install.add_argument(
        "--force", action="store_true",
        help="Overwrite without confirmation if the slot is occupied",
    )

    # --- pp8k flm (subcommands: show, validate) ---
    p_flm = subparsers.add_parser(
        "flm", help="Offline FLM file inspection",
    )
    flm_subs = p_flm.add_subparsers(dest="flm_command", required=True)

    p_flm_show = flm_subs.add_parser("show", help="Print header and LUT summary")
    p_flm_show.add_argument("file", help="Path to .FLM file")
    p_flm_show.add_argument(
        "--set", type=int, default=None,
        help="Dump curves for a single set (0-9) instead of the summary",
    )
    p_flm_show.add_argument(
        "--csv", action="store_true",
        help="With --set, emit CSV instead of a sampled table",
    )

    p_flm_val = flm_subs.add_parser("validate", help="Structural sanity check")
    p_flm_val.add_argument("file", help="Path to .FLM file")

    # --- pp8k expose ---
    p_expose = subparsers.add_parser(
        "expose", help="Expose an image onto film",
    )
    p_expose.add_argument("device", help="SCSI device: /dev/sg* path (SG_IO) or SCSI ID 0-7 (s2pexec/PiSCSI HAT)")
    p_expose.add_argument("image", help="Path to source image file")
    p_expose.add_argument(
        "--film", default=None,
        help="Path to .FLM film table file (mutually exclusive with --slot)",
    )
    p_expose.add_argument(
        "--slot", type=int, default=None,
        help="Device slot 0-19 with a pre-installed film table "
             "(mutually exclusive with --film)",
    )
    p_expose.add_argument(
        "--filter", default=None, choices=["red", "green", "blue"],
        help="Slot mode only: force a 1-pass B&W exposure on this channel. "
             "Omit for 3-pass color. Ignored when --film is used.",
    )
    p_expose.add_argument(
        "--res", default="4k", choices=["4k", "8k"],
        help="Resolution (default: 4k)",
    )
    p_expose.add_argument(
        "--transform", default="fit", choices=["fit", "fill"],
        help="Scaling mode: fit (letterbox) or fill (crop). Default: fit",
    )
    p_expose.add_argument(
        "--background", default="black", choices=["black", "white"],
        help="Letterbox bar color for fit mode (default: black)",
    )
    p_expose.add_argument(
        "--dry-run", action="store_true",
        help="Validate and convert image without sending to device",
    )

    args = parser.parse_args()

    try:
        if args.command == "info":
            sys.exit(cmd_info(args))
        elif args.command == "status":
            sys.exit(cmd_status(args))
        elif args.command == "slots":
            sys.exit(cmd_slots(args))
        elif args.command == "reset":
            sys.exit(cmd_reset(args))
        elif args.command == "install":
            sys.exit(cmd_install(args))
        elif args.command == "expose":
            sys.exit(cmd_expose(args))
        elif args.command == "flm":
            if args.flm_command == "show":
                sys.exit(cmd_flm_show(args))
            elif args.flm_command == "validate":
                sys.exit(cmd_flm_validate(args))
    except pp8k.DeviceNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except pp8k.DeviceNotReadyError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except pp8k.SCSIError as e:
        print(f"SCSI error: {e}", file=sys.stderr)
        sys.exit(2)
    except FileNotFoundError as e:
        print(f"File not found: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
