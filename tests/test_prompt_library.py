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

    def test_queue_canonical_sidecar_preserves_group_and_source_provenance(self):
        selected = [
            {"id": "light-7", "text": "Soft window lighting",
             "group_id": "lighting", "group_name": "Lighting and Camera",
             "group_keyword": "light", "group_index": 6, "group_position": 7,
             "seed": "1234567890123456789", "aspect_ratio": "4:5",
             "width": 1024, "height": 1280, "source_file": "/input/example.png"},
            {"id": "base-1", "text": "Looking at camera",
             "group_id": "base", "group_name": "Base", "group_index": 0,
             "group_position": 1},
        ]
        snap = json.dumps({"version": 1, "library": "Portraits", "selected": selected})
        node = module.PromptLibraryQueueNode
        self.assertEqual(node.OUTPUT_NODE, True)
        self.assertNotIn("OUTPUT_IS_LIST", node.__dict__)
        result = node.run("default", snap, "sH1VX woman", 0)
        prompt, index, total, sidecar_json, ratio, source_seed, width, height = result["result"]
        self.assertEqual((index, total), (0, 2))
        self.assertEqual(prompt, "sH1VX woman, Soft window lighting")
        self.assertEqual((ratio, source_seed, width, height),
                         ("4:5", 1234567890123456789, 1024, 1280))
        doc = json.loads(sidecar_json)
        self.assertEqual(doc["prompts"]["generation"]["positive"], prompt)
        self.assertEqual(doc["origin"]["generation"], {"settings": {}})
        provenance = doc["origin"]["prompt_library"]
        self.assertEqual(provenance["library"], "Portraits")
        self.assertEqual(provenance["group_name"], "Lighting and Camera")
        self.assertEqual(provenance["group_id"], "lighting")
        self.assertEqual(provenance["prompt_id"], "light-7")
        self.assertEqual((provenance["group_index"], provenance["group_position"]), (6, 7))
        self.assertEqual(provenance["source_seed"], 1234567890123456789)
        self.assertEqual(provenance["queue_total"], 2)
        self.assertNotIn("seed", doc["origin"]["generation"])
        result2 = node.run("default", snap, "", 1)["result"]
        doc2 = json.loads(result2[3])
        self.assertEqual(doc2["origin"]["prompt_library"]["group_name"], "Base")
        self.assertEqual(result2[5:], (-1, 0, 0))
        self.assertNotIn("source_seed", doc2["origin"]["prompt_library"])
        self.assertNotEqual(node.IS_CHANGED("default", snap, "", 0),
                            node.IS_CHANGED("default", snap, "", 1))

    def test_queue_old_snapshot_and_out_of_range(self):
        snapshot = '{"version":1,"selected":[{"id":"old","text":"Original prompt"}]}'
        doc = json.loads(module.PromptLibraryQueueNode.run(
            "default", snapshot, "", 0)["result"][3])
        self.assertEqual(doc["origin"]["prompt_library"]["prompt_id"], "old")
        self.assertNotIn("group_name", doc["origin"]["prompt_library"])
        with self.assertRaisesRegex(ValueError, "no selected prompts"):
            module.PromptLibraryQueueNode.run("default", '{"version":1,"selected":[]}', "", 0)
        with self.assertRaisesRegex(ValueError, "out of range"):
            module.PromptLibraryQueueNode.run("default", snapshot, "", 1)


if __name__ == "__main__":
    unittest.main()
