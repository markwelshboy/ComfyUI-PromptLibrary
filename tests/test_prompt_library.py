"""Run with: python -m unittest discover -s tests -v"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("prompt_library", Path(__file__).resolve().parents[1] / "prompt_library.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PromptLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("COMFYUI_PROMPT_LIBRARY_DIR")
        os.environ["COMFYUI_PROMPT_LIBRARY_DIR"] = self.temp.name

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("COMFYUI_PROMPT_LIBRARY_DIR", None)
        else:
            os.environ["COMFYUI_PROMPT_LIBRARY_DIR"] = self.previous
        self.temp.cleanup()

    def test_roundtrip_and_conflict(self):
        library = module.empty_library("sample")
        library["groups"] = [{"id": "a", "name": "Faces", "keyword": "face", "prompts": [{"id": "1", "text": "profile view"}]}]
        revision = module.save_library("sample", library, None)
        loaded, current = module.read_library("sample")
        self.assertEqual(library, loaded)
        self.assertEqual(revision, current)
        with self.assertRaises(FileExistsError):
            module.save_library("sample", library, None)

    def test_path_traversal_rejected(self):
        for name in ["../escape", "/tmp/x", "foo/bar", "", "..", "."]:
            with self.assertRaises(ValueError):
                module.library_path(name)

    def test_duplicate_ids_rejected(self):
        lib = module.empty_library()
        lib["groups"] = [{"id": "g", "name": "G", "prompts": [{"id": "p", "text": "A"}, {"id": "p", "text": "B"}]}]
        with self.assertRaises(ValueError):
            module.validate_library(lib)

    def test_selection_and_prefix(self):
        snap = json.dumps({"version": 1, "selected": [{"id": "1", "text": "front view"}, {"id": "2", "text": "side view"}, {"id": "3", "text": "back view"}]})
        node = module.PromptLibraryNode
        self.assertTrue(node.OUTPUT_NODE)
        self.assertEqual(node.OUTPUT_IS_LIST, (True, False, False, True, True, True, True))
        output = node.run("default", snap, "sH1VX woman", "Selected", 2, 0, 0)
        prompts, text, count, seeds, ratios, widths, heights = output["result"]
        self.assertEqual(prompts, ["sH1VX woman, front view", "sH1VX woman, side view"])
        self.assertEqual(count, 2)
        self.assertEqual(seeds, [-1, -1])
        self.assertEqual(ratios, ["", ""])
        self.assertEqual(widths, [0, 0])
        self.assertEqual(heights, [0, 0])
        self.assertEqual(output["ui"]["prompt_library"][0]["count"], 2)
        self.assertEqual(output["ui"]["prompt_library"][0]["preview"], prompts)
        self.assertIn("\n", text)
        self.assertEqual(node.run("default", snap, "", "Sequential N", 1, 1, 0)["result"][0], ["side view"])
        self.assertEqual(node.run("default", snap, "", "Random N", 2, 0, 123)["result"][0],
                         node.run("default", snap, "", "Random N", 2, 0, 123)["result"][0])

    def test_image_metadata_stays_aligned_with_random_and_ranges(self):
        selected = [
            {"id": "one", "text": "portrait", "seed": 11, "aspect_ratio": "4:5", "width": 1024, "height": 1280},
            {"id": "two", "text": "beach", "seed": 22, "aspect_ratio": "16:9", "width": 1600, "height": 900},
            {"id": "three", "text": "studio", "seed": 33, "aspect_ratio": "1:1", "width": 1024, "height": 1024},
        ]
        snap = json.dumps({"version": 1, "selected": selected})
        run = module.PromptLibraryNode.run
        result = run("default", snap, "", "Sequential N", 1, 1, 0)["result"]
        self.assertEqual(result[0], ["beach"])
        self.assertEqual(result[3:], ([22], ["16:9"], [1600], [900]))
        shuffled = run("default", snap, "", "Random N", 3, 0, 7)["result"]
        keyed = {item["text"]: item for item in selected}
        for prompt, seed, ratio, width, height in zip(shuffled[0], *shuffled[3:]):
            item = keyed[prompt]
            self.assertEqual((seed, ratio, width, height),
                             (item["seed"], item["aspect_ratio"], item["width"], item["height"]))

    def test_empty_selection(self):
        output = module.PromptLibraryNode.run("default", '{"version":1,"selected":[]}', "", "Selected", 0, 0, 0)
        self.assertEqual(output["result"], ([], "", 0, [], [], [], [],))
        self.assertEqual(output["ui"]["prompt_library"][0]["count"], 0)


if __name__ == "__main__":
    unittest.main()
