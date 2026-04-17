"""Command-line interface for the pp8k driver.

Provides three commands:

    pp8k info <device>      -- print device identification
    pp8k status <device>    -- print current mode and state
    pp8k expose <device> <image> --film <FLM> [options]

All commands require a SCSI device path (e.g. /dev/sg2) and root access.
"""

import argparse
import sys
import time

import pp8k
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


def cmd_expose(args):
    """Run an exposure."""
    # Load the film table
    flm = pp8k.load_flm(args.film)
    print(f"Film table:   {flm.name} ({flm.camera_type_name})")
    print(f"Type:         {'B&W' if flm.is_bw else 'Color'}"
          + (f" [{flm.bw_filter_name} filter]" if flm.is_bw else ""))
    print(f"Resolution:   {args.res}")
    print(f"Transform:    {args.transform}")
    print(f"Background:   {args.background}")
    print(f"Image:        {args.image}")

    if args.dry_run:
        # Validate everything without touching the device
        from pp8k.imaging import get_frame_dimensions, image_to_scanlines
        width, height = get_frame_dimensions(flm.camera_type, args.res)
        print(f"Frame size:   {width} x {height}")
        print("Converting image...", flush=True)
        t0 = time.monotonic()
        scanlines = image_to_scanlines(
            args.image, width, height, args.transform, args.background, flm.is_bw,
        )
        dt = time.monotonic() - t0
        print(f"Converted in {dt:.1f}s ({len(scanlines[0])} lines, {width} px wide)")
        print("DRY RUN -- no device commands sent.")
        return 0

    # Connect and expose
    print(f"Connecting to {args.device}...")
    device = pp8k.open(args.device)
    try:
        print(f"Connected: {device.info.product} (fw {device.info.firmware})")
        print()
        device.expose(
            image_path=args.image,
            flm=flm,
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
    p_info.add_argument("device", help="SCSI device path (e.g. /dev/sg2)")

    # --- pp8k status ---
    p_status = subparsers.add_parser(
        "status", help="Print current device mode and state",
    )
    p_status.add_argument("device", help="SCSI device path (e.g. /dev/sg2)")

    # --- pp8k expose ---
    p_expose = subparsers.add_parser(
        "expose", help="Expose an image onto film",
    )
    p_expose.add_argument("device", help="SCSI device path (e.g. /dev/sg2)")
    p_expose.add_argument("image", help="Path to source image file")
    p_expose.add_argument(
        "--film", required=True,
        help="Path to .FLM film table file (required)",
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
        elif args.command == "expose":
            sys.exit(cmd_expose(args))
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
