"""Best-effort replay extraction from selected image files.

Only read image metadata. ComfyUI's saved execution graph can be traced for
basic prompts and seeds; arbitrary custom node graphs may not be reversible.
No guess is made when the positive prompt cannot be recovered.
"""
import json
import os
import re
from collections import deque
from math import gcd
from pathlib import Path

MAX_FILES = 20000
MAX_METADATA_TEXT = 5 * 1024 * 1024
IMAGE_SUFFIXES = frozenset({".png", ".webp", ".jpg", ".jpeg"})
SAMPLER_CLASSES = frozenset({"KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced"})
SAVE_CLASSES = frozenset({"SaveImage", "PreviewImage", "SaveAnimatedWEBP"})
TEXT_FIELDS = ("text", "text_g", "text_l", "prompt", "string")
POSITIVE_FIELDS = ("positive", "positive_cond", "positive_conditioning", "conditioning", "guider")
SEED_FIELDS = ("seed", "noise_seed", "value", "int", "number")
COMMON_RATIOS = [(1,1), (4,5), (5,4), (2,3), (3,2), (3,4), (4,3),
                 (9,16), (16,9), (9,21), (21,9), (5,7), (7,5)]

def ratio_for_size(width, height):
    if not width or not height or width < 1 or height < 1:
        return ""
    ratio = width / height
    for a, b in COMMON_RATIOS:
        if abs(ratio - a / b) / (a / b) <= 0.005:
            return f"{a}:{b}"
    div = gcd(width, height)
    return f"{width // div}:{height // div}"

def _connection(value):
    # ComfyUI execution API inputs point to [node_id, output_index].
    if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[0], (str, int)) and isinstance(value[1], int):
        return str(value[0])
    return None

def _node(graph, node_id):
    return graph.get(str(node_id)) if isinstance(graph, dict) else None

def _walk(graph, start, skipped=frozenset(), limit=500):
    ref = _connection(start)
    if ref is None:
        return
    queue = deque([ref])
    seen = set()
    while queue and len(seen) < limit:
        node_id = queue.popleft()
        if node_id in seen:
            continue
        seen.add(node_id)
        node = _node(graph, node_id)
        if not isinstance(node, dict):
            continue
        yield node_id, node
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        for key, value in inputs.items():
            if key.lower() in skipped:
                continue
            link = _connection(value)
            if link is not None:
                queue.append(link)

def _text_from_positive(graph, starting_value):
    lines = []
    seen = set()
    # Ignore negative conditioning paths from guiders and other connected nodes.
    for _, node in _walk(graph, starting_value, frozenset({"negative", "negative_cond", "negative_conditioning", "uncond"})):
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        for key in TEXT_FIELDS:
            text = inputs.get(key)
            if isinstance(text, str):
                stripped = text.strip()
                if stripped and stripped not in seen:
                    seen.add(stripped)
                    lines.append(stripped)
    return "\n".join(lines)

def _int_from_value(graph, value):
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    for _, node in _walk(graph, value):
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        for key in SEED_FIELDS:
            seed = inputs.get(key)
            if isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0:
                return seed
    return None

def _sampler_seed(graph, node):
    inputs = node.get("inputs", {})
    if not isinstance(inputs, dict):
        return None
    for key in ("seed", "noise_seed"):
        if key in inputs:
            result = _int_from_value(graph, inputs[key])
            if result is not None:
                return result
    if "noise" in inputs:
        return _int_from_value(graph, inputs["noise"])
    return None

def _sampler_text(graph, node):
    inputs = node.get("inputs", {})
    if not isinstance(inputs, dict):
        return ""
    for key in POSITIVE_FIELDS:
        if key in inputs:
            text = _text_from_positive(graph, inputs[key])
            if text:
                return text
    return ""

def extract_comfy(graph_blob):
    try:
        graph = json.loads(graph_blob) if isinstance(graph_blob, str) else graph_blob
    except (ValueError, TypeError):
        return None
    if not isinstance(graph, dict):
        return None
    samplers = [(key, node) for key, node in graph.items()
                if isinstance(node, dict) and node.get("class_type") in SAMPLER_CLASSES]
    if not samplers:
        return None

    # Prefer samplers used on a path feeding a save/preview image node.
    preferred = set()
    for node in graph.values():
        if not isinstance(node, dict) or node.get("class_type") not in SAVE_CLASSES:
            continue
        for value in (node.get("inputs") or {}).values():
            for node_id, upstream in _walk(graph, value):
                if upstream.get("class_type") in SAMPLER_CLASSES:
                    preferred.add(node_id)
    samplers.sort(key=lambda pair: (str(pair[0]) not in preferred, -int(pair[0]) if str(pair[0]).isdigit() else 0))
    for _, sampler in samplers:
        text = _sampler_text(graph, sampler)
        seed = _sampler_seed(graph, sampler)
        if text:
            return {"text": text, "seed": seed}
    return None

def extract_parameters(parameters):
    if not isinstance(parameters, str) or not parameters.strip():
        return None
    seed_match = re.search(r"(?:^|,\s*)Seed:\s*(\d+)\b", parameters, re.IGNORECASE | re.MULTILINE)
    if not seed_match:
        return None
    # A1111 uses the final "Steps: ... Seed: ... Size: ..." line.
    positive = parameters.split("\nNegative prompt:", 1)[0]
    positive = re.split(r"\nSteps:\s*\d+", positive, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    if not positive:
        return None
    return {"text": positive, "seed": int(seed_match.group(1))}

def extract_image(path):
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise RuntimeError("Pillow (normally installed with ComfyUI) is needed to read image metadata") from exc
    try:
        with Image.open(path) as img:
            width, height = img.size
            info = dict(img.info)
    except (OSError, ValueError, UnidentifiedImageError):
        return None, "Cannot decode image"
    for key in ("prompt", "parameters", "Comment", "comment", "Description", "description"):
        blob = info.get(key)
        if isinstance(blob, bytes):
            blob = blob.decode("utf-8", errors="replace")
        if not isinstance(blob, str) or not blob:
            continue
        if len(blob.encode("utf-8")) > MAX_METADATA_TEXT:
            return None, "Metadata exceeds size limit"
        parsed = extract_comfy(blob) if key == "prompt" else extract_parameters(blob)
        if parsed is None:
            continue
        # JSON numbers above 2**53 lose precision in the JavaScript frontend.
        # Store the seed as decimal text in the library, turn it back into an
        # exact Python INT when the node executes.
        return {"text": parsed["text"], "seed": str(parsed["seed"]) if parsed["seed"] is not None else None,
                "aspect_ratio": ratio_for_size(width, height),
                "width": width, "height": height, "source_file": str(path)}, None
    return None, "No supported prompt metadata (or prompt could not be traced)"

def scan_images(directory, recursive=False):
    if not isinstance(directory, str) or not directory.strip():
        raise ValueError("Enter a server-side directory path")
    root = Path(directory).expanduser().resolve()
    permitted = os.getenv("COMFYUI_PROMPT_LIBRARY_IMAGE_ROOT", "").strip()
    if permitted:
        allowed = Path(permitted).expanduser().resolve()
        if not root.is_relative_to(allowed):
            raise ValueError("Directory is outside COMFYUI_PROMPT_LIBRARY_IMAGE_ROOT")
    if not root.is_dir():
        raise ValueError("Directory does not exist on the ComfyUI server")
    paths = sorted(root.rglob("*") if recursive else root.iterdir())
    entries = []
    skipped = []
    scanned = 0
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if permitted and not path.resolve().is_relative_to(allowed):
            continue
        scanned += 1
        if scanned > MAX_FILES:
            raise ValueError(f"Too many image files (limit {MAX_FILES})")
        item, reason = extract_image(path)
        if item is not None:
            entries.append(item)
        else:
            skipped.append({"filename": path.name, "reason": reason})
    if not scanned:
        raise ValueError("No PNG, JPEG or WebP images found in the selected directory")
    return {"entries": entries, "scanned": scanned, "skipped_count": len(skipped), "skipped": skipped[:30]}
