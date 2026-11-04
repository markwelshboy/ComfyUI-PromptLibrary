"""ComfyUI Prompt Library custom node and frontend."""
from .prompt_library import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, register_routes

WEB_DIRECTORY = "./web"
register_routes()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
