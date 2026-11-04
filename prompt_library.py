"""Prompt Library: portable prompt snapshots and a disk-backed JSON library API.

The workflow holds selected prompt *text*, not just references, so it still runs
when the library file is missing on another ComfyUI installation.
"""

import hashlib
import json
import os
import random
import re
import tempfile
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
MAX_BYTES = 5 * 1024 * 1024
MAX_PROMPTS = 20000


def empty_library(name="default"):
    return {"version": 1, "name": name, "groups": [], "selections": []}


def validate_name(name):
    if not isinstance(name, str) or not NAME_RE.fullmatch(name) or name in (".", ".."):
        raise ValueError("Library name must be 1-80 characters: letters, numbers, _, - or .")
    return name


def validate_library(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Expected a version 1 library object")
    if not isinstance(data.get("name"), str) or len(data["name"]) > 160:
        raise ValueError("Library must have a name")
    groups, selections = data.get("groups"), data.get("selections", [])
    if not isinstance(groups, list) or not isinstance(selections, list):
        raise ValueError("groups and selections must be arrays")
    group_ids, prompt_ids = set(), set()
    count = 0
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("id"), str) or not group["id"]:
            raise ValueError("Every group needs an id")
        if group["id"] in group_ids:
            raise ValueError("Duplicate group id")
        group_ids.add(group["id"])
        if not isinstance(group.get("name"), str) or not group["name"].strip() or len(group["name"]) > 200:
            raise ValueError("Invalid group name")
        if not isinstance(group.get("keyword", ""), str) or len(group.get("keyword", "")) > 200:
            raise ValueError("Invalid group keyword")
        prompts = group.get("prompts")
        if not isinstance(prompts, list):
            raise ValueError("Each group needs a prompts array")
        for prompt in prompts:
            count += 1
            if count > MAX_PROMPTS:
                raise ValueError("Too many prompts")
            if not isinstance(prompt, dict) or not isinstance(prompt.get("id"), str) or not prompt["id"]:
                raise ValueError("Every prompt needs an id")
            if prompt["id"] in prompt_ids:
                raise ValueError("Duplicate prompt id")
            prompt_ids.add(prompt["id"])
            if not isinstance(prompt.get("text"), str) or not prompt["text"].strip():
                raise ValueError("Every prompt needs nonempty text")
    names = set()
    for selection in selections:
        if not isinstance(selection, dict) or not isinstance(selection.get("name"), str) or not selection["name"].strip():
            raise ValueError("Every saved selection needs a name")
        if selection["name"] in names:
            raise ValueError("Duplicate saved selection name")
        names.add(selection["name"])
        ids = selection.get("prompt_ids")
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
            raise ValueError("Selection prompt_ids must be strings")
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ValueError("Library exceeds 5 MB")
    return encoded


def storage_dir():
    override = os.environ.get("COMFYUI_PROMPT_LIBRARY_DIR")
    if override:
        return Path(override).expanduser()
    try:
        import folder_paths
        return Path(folder_paths.get_user_directory()) / "default" / "prompt_library"
    except ImportError:
        return Path(__file__).resolve().parent / "data"


def library_path(name):
    return storage_dir() / (validate_name(name) + ".json")


def revision(raw):
    return hashlib.sha256(raw).hexdigest()


def read_library(name):
    path = library_path(name)
    if not path.exists():
        return empty_library(name), None
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError("Library exceeds 5 MB")
    value = json.loads(raw)
    validate_library(value)
    return value, revision(raw)


def save_library(name, data, expected_revision):
    path = library_path(name)
    raw = validate_library(data)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    _, actual_revision = read_library(name)
    if actual_revision != expected_revision:
        raise FileExistsError("Library changed elsewhere. Reload before saving to avoid overwriting it.")
    # Write to a temporary file in the same directory and atomically replace.
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".prompt-library-", suffix=".tmp", delete=False) as file:
            tmp = Path(file.name)
            file.write(raw)
        os.replace(tmp, path)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()
    return revision(raw)


def selected_prompts(selection_json):
    try:
        snapshot = json.loads(selection_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid prompt selection snapshot") from exc
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        raise ValueError("Invalid prompt selection version")
    entries = snapshot.get("selected")
    if not isinstance(entries, list) or len(entries) > MAX_PROMPTS:
        raise ValueError("Invalid selected prompts array")
    if any(not isinstance(item, dict) or not isinstance(item.get("text"), str) for item in entries):
        raise ValueError("Selection entries must contain prompt text")
    return [item["text"] for item in entries if item["text"].strip()]


class PromptLibraryNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "library_name": ("STRING", {"default": "default"}),
            "selection_json": ("STRING", {"default": '{"version":1,"selected":[]}'}),
            "trigger": ("STRING", {"default": "", "multiline": False}),
            "mode": (["Selected", "Random N", "Sequential N"],),
            "max_prompts": ("INT", {"default": 0, "min": 0, "max": MAX_PROMPTS}),
            "start_index": ("INT", {"default": 0, "min": 0, "max": MAX_PROMPTS}),
            "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
        }}

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("prompts", "prompts_text", "prompt_count")
    OUTPUT_IS_LIST = (True, False, False)
    OUTPUT_NODE = True  # Allow standalone queueing for diagnostics.
    FUNCTION = "run"
    CATEGORY = "Prompt Library"
    DESCRIPTION = "Open the Prompt Manager to select prompts across groups. The selection is embedded in the workflow."

    @staticmethod
    def run(library_name, selection_json, trigger, mode, max_prompts, start_index, seed):
        prompts = selected_prompts(selection_json)
        if mode == "Random N":
            rng = random.Random(seed)
            prompts = rng.sample(prompts, min(max_prompts or len(prompts), len(prompts)))
        elif mode == "Sequential N":
            prompts = prompts[start_index:]
            if max_prompts:
                prompts = prompts[:max_prompts]
        else:
            if max_prompts:
                prompts = prompts[:max_prompts]
        prefix = trigger.strip()
        if prefix:
            prompts = [f"{prefix}, {prompt}" for prompt in prompts]
        # Execution receipt independent of third-party Show Text rendering.
        count = len(prompts)
        return {
            "ui": {"prompt_library": [{"count": count, "preview": prompts[:3]}]},
            "result": (prompts, "\n".join(prompts), count),
        }


NODE_CLASS_MAPPINGS = {"PromptLibraryNode": PromptLibraryNode}
NODE_DISPLAY_NAME_MAPPINGS = {"PromptLibraryNode": "📚 Prompt Library"}


def register_routes():
    from aiohttp import web
    from server import PromptServer

    routes = PromptServer.instance.routes

    @routes.get("/prompt_library/api/libraries")
    async def list_libraries(request):
        directory = storage_dir()
        names = sorted(p.stem for p in directory.glob("*.json") if NAME_RE.fullmatch(p.stem)) if directory.exists() else []
        return web.json_response({"libraries": names})

    @routes.get("/prompt_library/api/library")
    async def get_library(request):
        try:
            name = validate_name(request.query.get("name", "default"))
            data, current_revision = read_library(name)
            return web.json_response({"library": data, "revision": current_revision})
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.post("/prompt_library/api/library")
    async def put_library(request):
        if request.content_length is not None and request.content_length > MAX_BYTES + 8192:
            return web.json_response({"error": "Request exceeds size limit"}, status=413)
        try:
            payload = await request.json()
            name = validate_name(payload.get("name"))
            expected_revision = payload.get("revision")
            if expected_revision is not None and (not isinstance(expected_revision, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_revision)):
                raise ValueError("Invalid revision")
            new_revision = save_library(name, payload.get("library"), expected_revision)
            return web.json_response({"revision": new_revision})
        except FileExistsError as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except (ValueError, OSError, TypeError, json.JSONDecodeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)


__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "register_routes"]
