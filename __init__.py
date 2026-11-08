"""ComfyUI Prompt Library: manual collections and indexed CSV/Parquet datasets."""
from .prompt_library import (
    NODE_CLASS_MAPPINGS as MANUAL_NODES,
    NODE_DISPLAY_NAME_MAPPINGS as MANUAL_NAMES,
    register_routes as register_library_routes,
)
from .dataset_browser import (
    NODE_CLASS_MAPPINGS as DATASET_NODES,
    NODE_DISPLAY_NAME_MAPPINGS as DATASET_NAMES,
    register_routes as register_dataset_routes,
)

WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS = {**MANUAL_NODES, **DATASET_NODES}
NODE_DISPLAY_NAME_MAPPINGS = {**MANUAL_NAMES, **DATASET_NAMES}

register_library_routes()
register_dataset_routes()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
