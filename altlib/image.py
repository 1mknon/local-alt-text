"""Image loading, resizing, and base64 encoding."""
import base64
import tempfile


def resize_for_model(image_path, max_side):
    from PIL import Image
    img = Image.open(image_path).convert("RGB")
    width, height = img.size
    if max(width, height) <= max_side:
        return image_path, False, (width, height), (width, height)
    scale = max_side / max(width, height)
    new_size = (int(width * scale), int(height * scale))
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False, prefix="alt_")
    img.resize(new_size, Image.LANCZOS).save(tmp.name, "PNG", optimize=True)
    tmp.close()
    return tmp.name, True, (width, height), new_size


def encode_image(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()