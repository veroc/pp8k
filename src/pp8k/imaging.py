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

from .constants import CAMERA_TYPES, FRAME_DIMENSIONS


def get_frame_dimensions(camera_type, resolution):
    """Look up frame dimensions for a camera type and resolution.

    Args:
        camera_type: Camera type code from the FLM header (0-5).
        resolution: "4k" or "8k".

    Returns:
        (width, height) in pixels.

    Raises:
        ValueError: If the camera type or resolution is not supported.
    """
    key = (camera_type, resolution.lower())
    dims = FRAME_DIMENSIONS.get(key)
    if dims is None:
        type_name = CAMERA_TYPES.get(camera_type, f"Unknown({camera_type})")
        raise ValueError(
            f"No frame dimensions for camera type '{type_name}' "
            f"at resolution '{resolution}'. "
            f"Supported: 35mm, 4x5, 6x7, 6x8 at 4k/8k."
        )
    return dims


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
