"""Indexed CSV/Parquet prompt datasets for the ComfyUI Prompt Library.

Browsing and filtering use HTTP endpoints and SQLite, NOT ComfyUI graph execution.
Selections embed prompt text in workflow JSON for portable, deterministic execution.
The stablellama CSV format is supported without pandas/pyarrow; Parquet is optional.
"""

from __future__ import annotations

import ast
import asyncio
import csv
import hashlib
import json
import os
import random
import re
import sqlite3
import tempfile
import threading
from pathlib import Path
from contextlib import contextmanager

DEFAULT_DATASET = "stablellama_erotic-image-prompts"
HF_REPOSITORY = "stablellama/erotic-image-prompts"
DATASET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
MAX_SELECTED = 2000  # Embedded selections must not make workflows enormous.
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
IMPORT_LOCK = threading.Lock()

# SQL column identifiers are never constructed from untrusted input.
FIELDS = {
    "females": {"label": "Females", "type": "number"},
    "males": {"label": "Males", "type": "number"},
    "artistic_quality": {"label": "Artistic Quality", "type": "number"},
    "erotic_score": {"label": "Erotic Score", "type": "number"},
    "pornographic_score": {"label": "Pornographic Score", "type": "number"},
    "aspect_ratio": {"label": "Aspect Ratio", "type": "category"},
    "nudity": {"label": "Nudity", "type": "category"},
    "clothing": {"label": "Clothing", "type": "tags"},
    "caption": {"label": "Caption", "type": "text"},
}
HEADERS = {
    "caption": "caption",
    "females": "females",
    "males": "males",
    "artistic quality": "artistic_quality",
    "erotic score": "erotic_score",
    "pornographic score": "pornographic_score",
    "aspect ratio": "aspect_ratio",
    "nudity": "nudity",
    "clothing": "clothing",
}
ROW_COLUMNS = "p.id, p.caption, p.females, p.males, p.artistic_quality, p.erotic_score, p.pornographic_score, p.aspect_ratio, p.nudity, p.clothing"


def root_dir():
    override = os.getenv("COMFYUI_PROMPT_LIBRARY_DIR")
    if override:
        return Path(override).expanduser() / "datasets"
    try:
        import folder_paths
        return Path(folder_paths.get_user_directory()) / "default" / "prompt_library" / "datasets"
    except ImportError:
        return Path(__file__).resolve().parent / "data" / "datasets"


def dataset_dir(dataset_id):
    if not isinstance(dataset_id, str) or not DATASET_NAME.fullmatch(dataset_id) or dataset_id in {".", ".."}:
        raise ValueError("Invalid dataset identifier")
    return root_dir() / dataset_id


def manifest_path(dataset_id):
    return dataset_dir(dataset_id) / "manifest.json"


def read_manifest(dataset_id):
    path = manifest_path(dataset_id)
    if not path.is_file():
        raise FileNotFoundError("Dataset has not been imported")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("dataset_id") != dataset_id:
        raise ValueError("Dataset manifest is invalid")
    return data


def list_datasets():
    root = root_dir()
    found = []
    if root.exists():
        for path in sorted(root.iterdir()):
            if path.is_dir() and DATASET_NAME.fullmatch(path.name) and (path / "manifest.json").is_file():
                try:
                    manifest = read_manifest(path.name)
                    if (path / "prompts.sqlite").is_file():
                        found.append(manifest)
                except (ValueError, OSError, json.JSONDecodeError):
                    continue
    return {"datasets": found, "default_dataset": DEFAULT_DATASET, "repository": HF_REPOSITORY}


@contextmanager
def connect(dataset_id):
    read_manifest(dataset_id)
    path = dataset_dir(dataset_id) / "prompts.sqlite"
    if not path.is_file():
        raise FileNotFoundError("Dataset index is missing")
    # Read-only URI prevents a malformed path from creating a new database.
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def _clothing_tags(value):
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        try:
            raw = ast.literal_eval(value)
        except (ValueError, SyntaxError, TypeError):
            raw = [value] if value.strip() else []
    else:
        raw = []
    if not isinstance(raw, (list, tuple)):
        raw = []
    return sorted({str(v).strip().lower() for v in raw if str(v).strip()})


def _float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _iter_records(path):
    ext = path.suffix.lower()
    if ext == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            yield from csv.DictReader(stream)
    elif ext == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ValueError("Parquet requires pyarrow. Install it in ComfyUI's Python environment, or import the CSV instead.") from exc
        reader = pq.ParquetFile(str(path))
        for batch in reader.iter_batches(batch_size=2048):
            yield from batch.to_pylist()
    else:
        raise ValueError("Only .csv and .parquet files are supported")


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def import_dataset(source_path, dataset_id, source, revision=None):
    """Atomic import: build the index in a temporary DB and replace on success."""
    source_path = Path(source_path)
    if source_path.suffix.lower() not in {".csv", ".parquet"}:
        raise ValueError("Expected a CSV or Parquet file")
    if not source_path.is_file():
        raise FileNotFoundError("Source file not found")
    folder = dataset_dir(dataset_id)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = None
    with IMPORT_LOCK:
        try:
            with tempfile.NamedTemporaryFile(dir=folder, suffix=".sqlite.tmp", delete=False) as file:
                temporary = Path(file.name)
            db = sqlite3.connect(temporary)
            try:
                db.executescript("""
                    PRAGMA journal_mode=DELETE;
                    PRAGMA synchronous=OFF;
                    CREATE TABLE prompts (
                        id INTEGER PRIMARY KEY, caption TEXT NOT NULL,
                        females INTEGER, males INTEGER,
                        artistic_quality REAL, erotic_score REAL, pornographic_score REAL,
                        aspect_ratio TEXT, nudity TEXT, clothing TEXT NOT NULL
                    );
                    CREATE TABLE clothing_tags (prompt_id INTEGER NOT NULL, tag TEXT NOT NULL);
                """)
                cursor = db.cursor()
                count = 0
                for raw in _iter_records(source_path):
                    row = {HEADERS[str(k).strip().lower()]: v for k, v in raw.items() if str(k).strip().lower() in HEADERS}
                    caption = str(row.get("caption") or "").strip()
                    if not caption:
                        continue
                    count += 1
                    tags = _clothing_tags(row.get("clothing"))
                    cursor.execute(
                        "INSERT INTO prompts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (count, caption, _int(row.get("females")), _int(row.get("males")),
                         _float(row.get("artistic_quality")), _float(row.get("erotic_score")),
                         _float(row.get("pornographic_score")), str(row.get("aspect_ratio") or "").strip(),
                         str(row.get("nudity") or "").strip(), json.dumps(tags, ensure_ascii=False)),
                    )
                    if tags:
                        cursor.executemany("INSERT INTO clothing_tags VALUES (?, ?)", ((count, tag) for tag in tags))
                if count == 0:
                    raise ValueError("No nonempty captions found. Expected a column named Caption.")
                db.executescript("""
                    CREATE INDEX idx_females ON prompts(females);
                    CREATE INDEX idx_males ON prompts(males);
                    CREATE INDEX idx_quality ON prompts(artistic_quality);
                    CREATE INDEX idx_erotic ON prompts(erotic_score);
                    CREATE INDEX idx_pornographic ON prompts(pornographic_score);
                    CREATE INDEX idx_ratio ON prompts(aspect_ratio);
                    CREATE INDEX idx_nudity ON prompts(nudity);
                    CREATE INDEX idx_clothing_tag ON clothing_tags(tag, prompt_id);
                    CREATE INDEX idx_clothing_id ON clothing_tags(prompt_id);
                """)
                db.commit()
            finally:
                db.close()
            manifest = {
                "dataset_id": dataset_id,
                "name": HF_REPOSITORY if dataset_id == DEFAULT_DATASET else source_path.name,
                "source": source,
                "revision": revision or file_sha256(source_path),
                "source_format": source_path.suffix.lower().lstrip("."),
                "rows": count,
            }
            # Manifest is written only when the index is ready.
            os.replace(temporary, folder / "prompts.sqlite")
            temporary = None
            tmp_manifest = folder / "manifest.json.tmp"
            try:
                tmp_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                os.replace(tmp_manifest, folder / "manifest.json")
            finally:
                tmp_manifest.unlink(missing_ok=True)
            return manifest
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def import_huggingface(format_name="csv"):
    if format_name not in {"csv", "parquet"}:
        raise ValueError("Format must be csv or parquet")
    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError as exc:
        raise ValueError("huggingface_hub is required for Hugging Face imports") from exc
    info = HfApi().dataset_info(HF_REPOSITORY)
    revision = info.sha
    folder = dataset_dir(DEFAULT_DATASET)
    folder.mkdir(parents=True, exist_ok=True)
    filename = "captions." + format_name
    source_path = hf_hub_download(
        repo_id=HF_REPOSITORY, repo_type="dataset", filename=filename,
        revision=revision, local_dir=str(folder / "source"),
    )
    return import_dataset(source_path, DEFAULT_DATASET, "huggingface:" + HF_REPOSITORY, revision)


def _escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _contains(column, word, starts=False):
    term = _escape_like(word)
    return column + " LIKE ? ESCAPE '\\' COLLATE NOCASE", (term + "%" if starts else "%" + term + "%",)


def _where(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    clauses, params = [], []
    filters = payload.get("filters", [])
    if not isinstance(filters, list) or len(filters) > 20:
        raise ValueError("Invalid filters list (maximum 20)")
    for item in filters:
        if not isinstance(item, dict):
            raise ValueError("Invalid filter")
        if not item.get("enabled", False):
            continue
        name = item.get("field")
        spec = FIELDS.get(name)
        if spec is None:
            raise ValueError("Unknown filter field")
        op, value = item.get("operator", "equals"), item.get("value", "")
        kind = spec["type"]
        if not isinstance(value, str) or len(value) > 250:
            raise ValueError("Filter value is too long")
        value = value.strip()
        if not value:
            continue
        if kind == "number":
            if op not in {"equals", "not_equals", "gt", "gte", "lt", "lte", "between"}:
                raise ValueError("Invalid numeric operator")
            ops = {"equals": "=", "not_equals": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
            if op == "between":
                parts = value.split(",", 1)
                if len(parts) != 2:
                    raise ValueError("Between requires min,max")
                try:
                    a, b = map(float, parts)
                except ValueError as exc:
                    raise ValueError("Invalid numeric range") from exc
                clauses.append("p." + name + " BETWEEN ? AND ?")
                params.extend([a, b])
            else:
                try:
                    number = float(value)
                except ValueError as exc:
                    raise ValueError("Invalid numeric value") from exc
                clauses.append("p." + name + " " + ops[op] + " ?")
                params.append(number)
        elif kind == "tags":
            if op not in {"contains", "not_contains", "equals", "not_equals"}:
                raise ValueError("Invalid clothing operator")
            if op in {"contains", "not_contains"}:
                cond, bind = _contains("t.tag", value)
            else:
                cond, bind = "t.tag = ? COLLATE NOCASE", (value,)
            sql = "EXISTS (SELECT 1 FROM clothing_tags t WHERE t.prompt_id=p.id AND " + cond + ")"
            clauses.append("NOT " + sql if op in {"not_contains", "not_equals"} else sql)
            params.extend(bind)
        else:
            if op not in {"equals", "not_equals", "contains", "not_contains", "starts_with"}:
                raise ValueError("Invalid text operator")
            column = "p." + name
            if op in {"equals", "not_equals"}:
                clauses.append(column + (" != ?" if op == "not_equals" else " = ?") + " COLLATE NOCASE")
                params.append(value)
            else:
                cond, bind = _contains(column, value, starts=op == "starts_with")
                clauses.append("NOT (" + cond + ")" if op == "not_contains" else cond)
                params.extend(bind)
    for key in ("include", "exclude"):
        words = payload.get(key, [])
        if not isinstance(words, list) or len(words) > 20:
            raise ValueError(key + " must be an array of at most 20 tags")
        words = [str(word).strip() for word in words]
        if any(len(word) > 120 for word in words):
            raise ValueError("Caption search tag too long")
        words = [word for word in words if word]
        if not words:
            continue
        pieces = []
        for word in words:
            cond, bind = _contains("p.caption", word)
            pieces.append(cond)
            params.extend(bind)
        if key == "exclude":
            clauses.append("NOT (" + " OR ".join(pieces) + ")")
        else:
            mode = payload.get("include_mode", "all")
            if mode not in {"all", "any"}:
                raise ValueError("Invalid include match mode")
            clauses.append("(" + (" AND " if mode == "all" else " OR ").join(pieces) + ")")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _db_row(row):
    result = dict(row)
    result["clothing"] = json.loads(result["clothing"])
    return result


def schema(dataset_id):
    manifest = read_manifest(dataset_id)
    with connect(dataset_id) as db:
        fields = []
        for name, spec in FIELDS.items():
            field = {"key": name, **spec}
            if name in {"aspect_ratio", "nudity"}:
                field["options"] = [r[0] for r in db.execute(
                    "SELECT DISTINCT " + name + " FROM prompts WHERE " + name + " != '' ORDER BY " + name
                )]
            fields.append(field)
    return {"manifest": manifest, "fields": fields, "selection_limit": MAX_SELECTED}


def search(dataset_id, payload):
    where, binds = _where(payload)
    page = int(payload.get("page", 0))
    per_page = int(payload.get("per_page", 50))
    if not 0 <= page <= 100000 or not 1 <= per_page <= 100:
        raise ValueError("Invalid page size or offset")
    with connect(dataset_id) as db:
        total = db.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
        matched = db.execute("SELECT COUNT(*) FROM prompts p" + where, binds).fetchone()[0]
        rows = db.execute(
            "SELECT " + ROW_COLUMNS + " FROM prompts p" + where + " ORDER BY p.id LIMIT ? OFFSET ?",
            [*binds, per_page, page * per_page],
        ).fetchall()
    return {"total": total, "matched": matched, "page": page, "per_page": per_page,
            "rows": [_db_row(row) for row in rows]}


def _fetch_ids(db, ids):
    """Return rows in requested order, chunking to handle old SQLite parameter limits."""
    found = {}
    for offset in range(0, len(ids), 400):
        chunk = ids[offset:offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        for row in db.execute(
            "SELECT " + ROW_COLUMNS + " FROM prompts p WHERE p.id IN (" + placeholders + ")", chunk
        ):
            found[row["id"]] = _db_row(row)
    if len(found) != len(ids):
        raise ValueError("One or more selected records no longer exist in the dataset")
    return [found[x] for x in ids]


def select(dataset_id, payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected a selection object")
    mode = payload.get("mode", "manual")
    if mode not in {"manual", "all_matches", "random"}:
        raise ValueError("Unknown selection mode")
    manifest = read_manifest(dataset_id)
    with connect(dataset_id) as db:
        if mode == "manual":
            supplied = payload.get("ids", [])
            if not isinstance(supplied, list) or len(supplied) > MAX_SELECTED:
                raise ValueError("Too many manually selected prompts")
            ids = []
            for value in supplied:
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    raise ValueError("Invalid selected record ID")
                if value not in ids:
                    ids.append(value)
        else:
            where, binds = _where(payload)
            if mode == "all_matches":
                ids = [r[0] for r in db.execute(
                    "SELECT p.id FROM prompts p" + where + " ORDER BY p.id LIMIT ?", [*binds, MAX_SELECTED + 1]
                )]
                if len(ids) > MAX_SELECTED:
                    raise ValueError(
                        "More than " + str(MAX_SELECTED) + " prompts match. Tighten filters or use Random N."
                    )
            else:
                size = int(payload.get("sample_size", 100))
                seed = int(payload.get("seed", 42))
                if not 1 <= size <= MAX_SELECTED or not 0 <= seed <= 0xFFFFFFFF:
                    raise ValueError("Invalid sample size or seed")
                candidates = [r[0] for r in db.execute(
                    "SELECT p.id FROM prompts p" + where + " ORDER BY p.id", binds
                )]
                ids = random.Random(seed).sample(candidates, min(size, len(candidates)))
        rows = _fetch_ids(db, ids) if ids else []
    selected = [{
        "id": dataset_id + ":" + str(row["id"]), "text": row["caption"],
        "meta": {field: row[field] for field in ("aspect_ratio", "females", "males", "artistic_quality",
                                                 "erotic_score", "pornographic_score", "nudity", "clothing")},
    } for row in rows]
    snapshot = {
        "version": 1, "dataset": dataset_id, "revision": manifest["revision"],
        "mode": mode, "selected": selected,
    }
    if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Selection is too large for a workflow snapshot; choose fewer prompts")
    return {"snapshot": snapshot, "count": len(selected)}


def selected_snapshot(value):
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid dataset selection JSON") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Invalid dataset selection version")
    chosen = data.get("selected")
    if not isinstance(chosen, list) or len(chosen) > MAX_SELECTED:
        raise ValueError("Invalid selected entries")
    if any(not isinstance(row, dict) or not isinstance(row.get("text"), str) for row in chosen):
        raise ValueError("Selected entries must contain caption text")
    return data


def replace_placeholder(text, name, trigger):
    if not name.strip() or not trigger.strip():
        return text
    pattern = r"(?<!\w)" + re.escape(name.strip()) + r"(?!\w)"
    return re.sub(pattern, lambda _: trigger.strip(), text, flags=re.IGNORECASE)


class PromptDatasetBrowserNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "dataset_name": ("STRING", {"default": HF_REPOSITORY}),
            "selection_json": ("STRING", {"default": '{"version":1,"selected":[]}'}),
            "replace_name": ("STRING", {"default": "Alice"}),
            "trigger_word": ("STRING", {"default": ""}),
            "max_prompts": ("INT", {"default": 0, "min": 0, "max": MAX_SELECTED}),
            "start_index": ("INT", {"default": 0, "min": 0, "max": MAX_SELECTED}),
        }}

    RETURN_TYPES = ("STRING", "STRING", "INT", "STRING")
    RETURN_NAMES = ("prompts", "prompts_text", "prompt_count", "metadata_json")
    OUTPUT_IS_LIST = (True, False, False, False)
    OUTPUT_NODE = True
    FUNCTION = "run"
    CATEGORY = "Prompt Library"
    DESCRIPTION = "Browse local CSV/Parquet-backed datasets interactively. Apply Selection to freeze captions in the workflow."

    @staticmethod
    def run(dataset_name, selection_json, replace_name, trigger_word, max_prompts, start_index):
        snapshot = selected_snapshot(selection_json)
        items = snapshot["selected"][start_index:]
        if max_prompts:
            items = items[:max_prompts]
        prompts = [replace_placeholder(item["text"], replace_name, trigger_word) for item in items]
        metadata = {
            "dataset": snapshot.get("dataset", dataset_name),
            "revision": snapshot.get("revision"),
            "selection_mode": snapshot.get("mode", "manual"),
            "rows": [{"id": item.get("id"), "meta": item.get("meta", {})} for item in items],
        }
        receipt = {"count": len(prompts), "preview": prompts[:3]}
        return {"ui": {"prompt_dataset": [receipt]},
                "result": (prompts, "\n".join(prompts), len(prompts), json.dumps(metadata, ensure_ascii=False))}


NODE_CLASS_MAPPINGS = {"PromptDatasetBrowserNode": PromptDatasetBrowserNode}
NODE_DISPLAY_NAME_MAPPINGS = {"PromptDatasetBrowserNode": "📚 Dataset Prompt Browser"}


def register_routes():
    from aiohttp import web
    from server import PromptServer

    routes = PromptServer.instance.routes

    def failed(exc):
        if isinstance(exc, FileNotFoundError):
            return web.json_response({"error": str(exc)}, status=404)
        if isinstance(exc, (ValueError, TypeError, json.JSONDecodeError, sqlite3.Error)):
            return web.json_response({"error": str(exc)}, status=400)
        # Download errors are shown for usability; do not echo credentials/tokens.
        return web.json_response({"error": type(exc).__name__ + ": dataset operation failed. See ComfyUI logs."}, status=500)

    @routes.get("/prompt_library/api/datasets")
    async def dataset_list(request):
        try:
            return web.json_response(list_datasets())
        except Exception as exc:
            return failed(exc)

    @routes.post("/prompt_library/api/datasets/import")
    async def dataset_import(request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Expected JSON object")
            result = await asyncio.to_thread(import_huggingface, payload.get("format", "csv"))
            return web.json_response({"dataset": result})
        except Exception as exc:
            return failed(exc)

    @routes.post("/prompt_library/api/datasets/upload")
    async def dataset_upload(request):
        tmp_path = None
        try:
            reader = await request.multipart()
            part = await reader.next()
            if part is None or part.name != "file":
                raise ValueError("Upload a file using the 'file' form field")
            suffix = Path(part.filename or "").suffix.lower()
            if suffix not in {".csv", ".parquet"}:
                raise ValueError("Upload a .csv or .parquet file")
            folder = root_dir() / "uploads"
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=folder, suffix=suffix, delete=False) as tmp:
                tmp_path = Path(tmp.name)
                total = 0
                while True:
                    chunk = await part.read_chunk(size=1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise ValueError("Upload exceeds 256 MB limit")
                    tmp.write(chunk)
            checksum = await asyncio.to_thread(file_sha256, tmp_path)
            dataset_id = "upload_" + checksum[:16]
            final_folder = dataset_dir(dataset_id) / "source"
            final_folder.mkdir(parents=True, exist_ok=True)
            target = final_folder / ("captions" + suffix)
            os.replace(tmp_path, target)
            tmp_path = None
            result = await asyncio.to_thread(import_dataset, target, dataset_id, "upload", checksum)
            return web.json_response({"dataset": result})
        except Exception as exc:
            return failed(exc)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    @routes.get("/prompt_library/api/datasets/{dataset_id}/schema")
    async def dataset_schema(request):
        try:
            return web.json_response(await asyncio.to_thread(schema, request.match_info["dataset_id"]))
        except Exception as exc:
            return failed(exc)

    @routes.post("/prompt_library/api/datasets/{dataset_id}/search")
    async def dataset_search(request):
        try:
            payload = await request.json()
            return web.json_response(await asyncio.to_thread(search, request.match_info["dataset_id"], payload))
        except Exception as exc:
            return failed(exc)

    @routes.post("/prompt_library/api/datasets/{dataset_id}/select")
    async def dataset_select(request):
        try:
            payload = await request.json()
            return web.json_response(await asyncio.to_thread(select, request.match_info["dataset_id"], payload))
        except Exception as exc:
            return failed(exc)


__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "register_routes"]
