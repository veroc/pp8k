"""Image-to-scanline conversion for device exposure.

Converts image files (JPEG, PNG, TIFF, etc.) to raw R/G/B channel bytes
at the exact frame dimensions required by the PP8K.  Handles scaling,
cropping, letterboxing, and EXIF orientation.

The PP8K receives image data as individual scanlines -- one horizontal
row of pixels for one color channel.  A color exposure requires three
complete passes (one per channel); a B&W exposure uses a single pass
on one channel.

Each scanline is a bytes object of length = frame_width, where each
byte is a pixel value (0-255).  The full image is represented as three
lists of scanlines: (red_lines, green_lines, blue_lines).
"""

from pathlib import Path

from PIL import Image, ImageOps

from .constants import RESOLUTION_HRES


def get_frame_dimensions(aspect_w, aspect_h, resolution):
    """Compute frame dimensions from a film's aspect ratio and resolution.

    The PP8K is a programmable-resolution device; the host picks any
    pixel dimensions up to the CRT's maximum and sends them via
    MODE SELECT.  Per the RasterPlus95 driver docs (§7.3), the correct
    vres for a borderless exposure is `hres * aspect_h / aspect_w`
    using the aspect pair stored in the FLM header (bytes 26-27) --
    e.g. 11:9 for 6x7, 54:42 for 4x5.

    Args:
        aspect_w: Aspect width component (FLM byte 26, or device sub 5).
        aspect_h: Aspect height component (FLM byte 27, or device sub 5).
        resolution: "4k" (hres=4096) or "8k" (hres=8192).

    Returns:
        (width, height) in pixels.

    Raises:
        ValueError: If the resolution label or aspect is not usable.
    """
    hres = RESOLUTION_HRES.get(resolution.lower())
    if hres is None:
        raise ValueError(
            f"Unknown resolution {resolution!r}; use '4k' or '8k'."
        )
    if aspect_w <= 0 or aspect_h <= 0:
        raise ValueError(
            f"Invalid aspect ratio {aspect_w}:{aspect_h}; "
            f"both components must be positive."
        )
    vres = (hres * aspect_h) // aspect_w
    return (hres, vres)


def image_to_scanlines(
    image_path,
    width,
    height,
    transform="fit",
    background="black",
    is_bw=False,
):
    """Convert an image file to device-ready scanlines.

    The image is loaded, EXIF-rotated, and scaled to fit or fill the
    target frame dimensions.  The result is split into three lists of
    scanlines (one per color channel).

    Transform modes:
        "fit"  -- Scale the image to fit entirely within the frame.
                  Adds letterbox/pillarbox bars in the background color.
                  No image content is lost.
        "fill" -- Scale the image to fill the frame completely.
                  Crops one axis if the aspect ratios don't match.
                  No bars, but some image content may be lost.

    Args:
        image_path: Path to source image (any Pillow-supported format).
        width: Frame width in pixels (e.g. 4096).
        height: Frame height in pixels (e.g. 2730).
        transform: "fit" or "fill".
        background: "black" or "white" (for letterbox bars in fit mode).
        is_bw: If True, convert to grayscale (identical data on all channels).

    Returns:
        (red_lines, green_lines, blue_lines) -- each a list of `height`
        bytes objects, each `width` bytes long.
    """
    with Image.open(image_path) as img:
        # Apply EXIF orientation (camera rotation metadata)
        img = ImageOps.exif_transpose(img) or img

        # Ensure RGB mode for channel splitting
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Create the target canvas with the background color
        bg_color = (0, 0, 0) if background == "black" else (255, 255, 255)
        canvas = Image.new("RGB", (width, height), bg_color)

        img_ratio = img.width / img.height
        frame_ratio = width / height

        if transform == "fill":
            # Fill: scale to cover the entire frame, crop the overflow
            if img_ratio > frame_ratio:
                # Image is wider -- match height, crop sides
                new_h = height
                new_w = round(height * img_ratio)
            else:
                # Image is taller -- match width, crop top/bottom
                new_w = width
                new_h = round(width / img_ratio)
        else:
            # Fit: scale to fit entirely within the frame, add bars
            if img_ratio > frame_ratio:
                # Image is wider -- match width, bars top/bottom
                new_w = width
                new_h = round(width / img_ratio)
            else:
                # Image is taller -- match height, bars left/right
                new_h = height
                new_w = round(height * img_ratio)

        # Resize with high-quality Lanczos resampling
        resized = img.resize((new_w, new_h), Image.LANCZOS)

        # Center on canvas
        x = (width - new_w) // 2
        y = (height - new_h) // 2
        canvas.paste(resized, (x, y))

    # Split into per-channel scanlines
    if is_bw:
        # B&W: convert to grayscale, duplicate across all channels
        gray = canvas.convert("L")
        raw = gray.tobytes()
        lines = [raw[y * width : (y + 1) * width] for y in range(height)]
        return lines, lines, lines
    else:
        # Color: split into R, G, B channels
        r_ch, g_ch, b_ch = canvas.split()
        r_raw, g_raw, b_raw = r_ch.tobytes(), g_ch.tobytes(), b_ch.tobytes()
        red_lines = [r_raw[y * width : (y + 1) * width] for y in range(height)]
        green_lines = [g_raw[y * width : (y + 1) * width] for y in range(height)]
        blue_lines = [b_raw[y * width : (y + 1) * width] for y in range(height)]
        return red_lines, green_lines, blue_lines
