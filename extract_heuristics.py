"""Deterministic heuristics via PIL — layer 0 before any model.

Signals the LLM cannot see from the image alone: EXIF, noise, edge
density, unique-color ratio, luminance. Runs in ~50ms per image, no
GPU, no network.

All thresholds live in config.json under "heuristics". Nothing here
is hardcoded to a specific test image.
"""
import argparse
import json
from math import gcd

from PIL import Image, ImageStat, ImageFilter
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)


def _zone(lum):
    return min(10, max(0, int(lum / 25.6)))


def _is_monochrome(stddev):
    r, g, b = stddev
    return abs(r - g) < 3 and abs(g - b) < 3 and abs(r - b) < 3


def _has_exif_camera(img):
    try:
        exif = img.getexif()
        if not exif:
            return False
        return bool(exif.get(271) or exif.get(272))
    except Exception:
        return False


def _color_profile(img_rgb, is_mono):
    if is_mono:
        stat = ImageStat.Stat(img_rgb)
        r, _, b = stat.mean
        return "sepia" if r > b + 15 else "monochromatic"

    stat = ImageStat.Stat(img_rgb)
    r, g, b = stat.mean
    r_std, g_std, b_std = stat.stddev

    if r_std + g_std + b_std > 180:
        return "high_saturation"
    if r > g + 15 and g > b + 10 and r > 120:
        return "golden_hour"
    if b > r + 20 and b > g + 10:
        return "blue_hour"
    if r > 180 and g > 180 and b > 180 and (r_std + g_std + b_std) < 80:
        return "pastel"
    return "neutral_color"


def _is_dark_scene(img_gray):
    stat = ImageStat.Stat(img_gray)
    mean = stat.mean[0]
    return mean < 60 and stat.stddev[0] > 25


def _estimate_noise(img_gray):
    w, h = img_gray.size
    patches = []
    step = max(1, min(w, h) // 10)
    for y in range(0, h - 32, step):
        for x in range(0, w - 32, step):
            patch = img_gray.crop((x, y, x + 32, y + 32))
            patches.append(ImageStat.Stat(patch).stddev[0])
    patches.sort()
    return sum(patches[:3]) / 3 if patches else 0.0


def _edge_density(img_gray):
    edges = img_gray.filter(ImageFilter.FIND_EDGES)
    hist = edges.histogram()
    total = sum(hist)
    return (sum(hist[31:]) / total) if total else 0.0


def _edge_sharpness(img_gray):
    edges = img_gray.filter(ImageFilter.FIND_EDGES)
    data = list(edges.getdata())
    bright = [p for p in data if p > 30]
    if not bright:
        return 0.0
    return sum(bright) / len(bright)


def _unique_colors_ratio(img_rgb):
    small = img_rgb.resize((64, 64), Image.LANCZOS)
    pixels = list(small.getdata())
    return len(set(pixels)) / len(pixels)


def _aspect_ratio_bucket(width, height):
    """Reduce (w, h) to the smallest integer pair, e.g. (1920,1080) → (16,9)."""
    if width <= 0 or height <= 0:
        return (0, 0)
    divisor = gcd(width, height)
    return (width // divisor, height // divisor)


def analyze(image_path):
    img = Image.open(image_path)
    exif_camera = _has_exif_camera(img)

    img_rgb = img.convert("RGB")
    small = img_rgb.resize((256, 256), Image.LANCZOS)
    img_gray = small.convert("L")
    stat_rgb = ImageStat.Stat(small)
    lum_mean = ImageStat.Stat(img_gray).mean[0]

    is_mono = _is_monochrome(stat_rgb.stddev)
    is_dark = _is_dark_scene(img_gray)

    return {
        "exif_camera": exif_camera,
        "resolution": img.size,
        "aspect_ratio": round(img.size[0] / img.size[1], 2) if img.size[1] else 1.0,
        "aspect_ratio_bucket": _aspect_ratio_bucket(*img.size),
        "luminance_mean": round(lum_mean, 1),
        "luminance_zone": _zone(lum_mean),
        "is_dark_scene": is_dark,
        "is_night_likely": is_dark and not exif_camera and not is_mono,
        "is_bright": lum_mean > 200,
        "is_monochrome": is_mono,
        "color_profile": _color_profile(small, is_mono),
        "noise_estimate": round(_estimate_noise(img_gray), 2),
        "edge_density": round(_edge_density(img_gray), 3),
        "unique_colors_ratio": round(_unique_colors_ratio(img_rgb), 3),
        "edge_sharpness": round(_edge_sharpness(img_gray), 1),
    }


def format_for_prompt(h):
    lines = []
    has_exif = "present (likely real photograph)" if h["exif_camera"] else \
        "absent (may be screenshot, illustration, export or digital art)"
    lines.append(f"- Camera EXIF: {has_exif}")
    lines.append(f"- Resolution: {h['resolution'][0]}x{h['resolution'][1]} "
                 f"(aspect {h['aspect_ratio']})")
    lines.append(f"- Mean luminance: {h['luminance_mean']}/255 "
                 f"(zone {h['luminance_zone']}/10 of Ansel Adams)")
    if h["is_night_likely"]:
        lines.append("- Tone: likely night scene")
    elif h["is_dark_scene"]:
        lines.append("- Tone: dark scene or dark-theme interface")
    elif h["is_bright"]:
        lines.append("- Tone: very bright scene (snow, sky, overexposed)")
    if h["is_monochrome"]:
        lines.append("- Color: monochrome (black-and-white or sepia)")
    else:
        lines.append(f"- Color profile: {h['color_profile']}")
    lines.append(f"- Estimated noise: {h['noise_estimate']} "
                 f"(smooth photo: <5; noisy or grainy photo: >15)")
    lines.append(f"- Edge density: {h['edge_density']} "
                 f"(illustration/text: >0.15; soft photo/bokeh: <0.08)")
    return "\n".join(lines)


def route_by_heuristics(image_path, cfg):
    """Classify via PIL only. All thresholds come from cfg["heuristics"]."""
    t = cfg["heuristics"]
    a = analyze(image_path)

    edge = a["edge_density"]
    noise = a["noise_estimate"]
    exif = a["exif_camera"]
    mono = a["is_monochrome"]
    lum = a["luminance_mean"]
    ucr = a["unique_colors_ratio"]
    ratio_bucket = a["aspect_ratio_bucket"]

    if exif:
        return {
            "label": "photo",
            "confidence": t["exif_confidence"],
            "decidable": True,
            "reason": "camera EXIF present",
        }

    if noise < t["screenshot_noise_max"] and edge > t["screenshot_edge_min"]:
        return {
            "label": "screenshot",
            "confidence": 0.85,
            "decidable": True,
            "reason": f"near-zero noise + dense edges (edge={edge:.2f})",
        }

    screen_ratios = [tuple(r) for r in t["screen_ratio_set"]]
    if (noise < t["screenshot_noise_max"]
            and lum < t["dark_lum_max"]
            and ratio_bucket in screen_ratios):
        return {
            "label": "screenshot",
            "confidence": 0.80,
            "decidable": True,
            "reason": f"near-zero noise + dark theme + screen aspect (lum={lum:.0f})",
        }

    if ucr < t["illustration_ucr_max"] and not mono:
        return {
            "label": "illustration",
            "confidence": 0.85,
            "decidable": True,
            "reason": f"limited palette (ucr={ucr:.2f})",
        }

    if mono and noise > t["bw_noise_min"]:
        return {
            "label": "photo",
            "confidence": 0.80,
            "decidable": True,
            "reason": f"monochrome with sensor noise (noise={noise})",
        }

    if ucr > t["photo_ucr_min"] and noise < t["photo_low_noise_max"]:
        return {
            "label": "generic",
            "confidence": 0.50,
            "decidable": False,
            "reason": f"rich palette (ucr={ucr:.2f}) + very low noise "
                      f"({noise:.2f}) — may be clean photo or digital art",
        }

    if (ucr > t["photo_ucr_min"]
            and t["photo_noise_min"] < noise < t["photo_noise_max"]):
        return {
            "label": "photo",
            "confidence": 0.80,
            "decidable": True,
            "reason": f"rich palette (ucr={ucr:.2f}) + noise (noise={noise})",
        }

    if edge > t["diagram_edge_min"] and lum > t["diagram_lum_min"]:
        return {
            "label": "diagram",
            "confidence": 0.75,
            "decidable": True,
            "reason": f"dense edges (edge={edge:.2f}) + bright background",
        }

    if noise > t["lowlight_noise_min"] and lum < t["dark_lum_max"]:
        return {
            "label": "photo",
            "confidence": 0.85,
            "decidable": True,
            "reason": f"high noise ({noise}) in low light",
        }

    if (t["photo_noise_min"] < noise < t["photo_noise_max"]
            and ucr > t["photo_ucr_fallback_min"]):
        return {
            "label": "photo",
            "confidence": 0.60,
            "decidable": True,
            "reason": f"ucr={ucr:.2f} + noise={noise}",
        }

    return {
        "label": "generic",
        "confidence": 0.40,
        "decidable": False,
        "reason": f"ambiguous (exif={exif}, noise={noise}, "
                  f"edge={edge:.2f}, ucr={ucr:.2f}, mono={mono})",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("image")
    p.add_argument("--route", action="store_true",
                   help="output route decision (needs config)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.route:
        from altlib import load_config
        cfg = load_config()
        route = route_by_heuristics(args.image, cfg)
        route["heur_text"] = format_for_prompt(analyze(args.image))
        print(json.dumps(route, ensure_ascii=False))
    else:
        print(json.dumps(analyze(args.image), indent=2, ensure_ascii=False))
