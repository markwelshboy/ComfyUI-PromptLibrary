"""Dataset importer and query tests (stdlib only; no ComfyUI required)."""

import csv
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dataset_browser", ROOT / "dataset_browser.py")
dataset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dataset)


class DatasetBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.before = os.environ.get("COMFYUI_PROMPT_LIBRARY_DIR")
        os.environ["COMFYUI_PROMPT_LIBRARY_DIR"] = self.tmp.name
        self.input = Path(self.tmp.name) / "source.csv"
        with self.input.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=[
                "Caption", "Females", "Males", "Artistic Quality", "Erotic Score",
                "Pornographic Score", "Aspect Ratio", "Nudity", "Clothing",
            ])
            writer.writeheader()
            writer.writerows([
                {"Caption": "Alice at the beach\nunder the summer sky.", "Females": 1, "Males": 0,
                 "Artistic Quality": 0.9, "Erotic Score": 0.7, "Pornographic Score": 0.2,
                 "Aspect Ratio": "2:3", "Nudity": "none", "Clothing": "['bikini', 'hat']"},
                {"Caption": "Alice at sunset with a dress", "Females": 1, "Males": 0,
                 "Artistic Quality": 0.8, "Erotic Score": 0.3, "Pornographic Score": 0.1,
                 "Aspect Ratio": "3:2", "Nudity": "partial", "Clothing": "['dress']"},
                {"Caption": "Bella portrait in a studio", "Females": 1, "Males": 0,
                 "Artistic Quality": 0.75, "Erotic Score": 0.5, "Pornographic Score": 0.1,
                 "Aspect Ratio": "1:1", "Nudity": "none", "Clothing": "[]"},
                {"Caption": "Adam and Alice on a boardwalk", "Females": 1, "Males": 1,
                 "Artistic Quality": 0.4, "Erotic Score": 0.3, "Pornographic Score": 0.3,
                 "Aspect Ratio": "4:5", "Nudity": "none", "Clothing": "['hat']"},
            ])
        self.id = "unit_test"
        dataset.import_dataset(self.input, self.id, "test", "revisionA")

    def tearDown(self):
        if self.before is None:
            os.environ.pop("COMFYUI_PROMPT_LIBRARY_DIR", None)
        else:
            os.environ["COMFYUI_PROMPT_LIBRARY_DIR"] = self.before
        self.tmp.cleanup()

    def test_import_schema_and_cache(self):
        self.assertEqual(dataset.read_manifest(self.id)["rows"], 4)
        self.assertEqual(dataset.read_manifest(self.id)["revision"], "revisionA")
        self.assertEqual(dataset.schema(self.id)["selection_limit"], dataset.MAX_SELECTED)
        self.assertIn("2:3", next(f for f in dataset.schema(self.id)["fields"]
                                  if f["key"] == "aspect_ratio")["options"])
        self.assertEqual(dataset.search(self.id, {})["total"], 4)
        rows = dataset.search(self.id, {})["rows"]
        self.assertIn("\n", rows[0]["caption"])
        self.assertEqual(rows[0]["clothing"], ["bikini", "hat"])

    def test_dynamic_filters_and_tag_search(self):
        result = dataset.search(self.id, {"filters": [
            {"field": "females", "enabled": True, "operator": "equals", "value": "1"},
            {"field": "males", "enabled": True, "operator": "equals", "value": "0"},
            {"field": "artistic_quality", "enabled": True, "operator": "gte", "value": "0.8"},
        ]})
        self.assertEqual(result["matched"], 2)
        self.assertEqual(dataset.search(self.id, {
            "include": ["Alice", "beach"], "include_mode": "all",
            "exclude": ["studio"],
        })["matched"], 1)
        self.assertEqual(dataset.search(self.id, {
            "include": ["beach", "studio"], "include_mode": "any",
        })["matched"], 2)
        self.assertEqual(dataset.search(self.id, {"filters": [
            {"field": "clothing", "enabled": True, "operator": "equals", "value": "bikini"},
            {"field": "aspect_ratio", "enabled": True, "operator": "equals", "value": "2:3"},
        ]})["matched"], 1)
        self.assertEqual(dataset.search(self.id, {"filters": [
            {"field": "clothing", "enabled": True, "operator": "not_contains", "value": "hat"},
        ]})["matched"], 2)
        self.assertEqual(dataset.search(self.id, {"filters": [
            {"field": "erotic_score", "enabled": True, "operator": "between", "value": "0.3,0.5"},
        ]})["matched"], 3)

    def test_selection_manual_all_and_random_seed(self):
        manual = dataset.select(self.id, {"mode": "manual", "ids": [3, 1, 3]})["snapshot"]
        self.assertEqual([x["id"] for x in manual["selected"]], ["unit_test:3", "unit_test:1"])
        self.assertEqual(manual["revision"], "revisionA")
        all_rows = dataset.select(self.id, {"mode": "all_matches", "include": ["Alice"]})
        self.assertEqual(all_rows["count"], 3)
        random_args = {"mode": "random", "sample_size": 2, "seed": 99}
        a = dataset.select(self.id, random_args)
        b = dataset.select(self.id, random_args)
        self.assertEqual(a, b)
        self.assertEqual(a["count"], 2)

    def test_workflow_portability_and_replacement(self):
        frozen = dataset.select(self.id, {"mode": "manual", "ids": [1, 2]})["snapshot"]
        selection_json = json.dumps(frozen)
        # No database read is needed at ComfyUI execution time.
        dataset.dataset_dir(self.id).joinpath("prompts.sqlite").unlink()
        node = dataset.PromptDatasetBrowserNode
        self.assertTrue(node.OUTPUT_NODE)
        self.assertEqual(node.OUTPUT_IS_LIST, (True, False, False, False))
        output = node.run("Anything", selection_json, "Alice", r"sH1VX \woman", 1, 0)
        prompts, joined, count, meta = output["result"]
        self.assertEqual(count, 1)
        self.assertEqual(prompts[0], "sH1VX \\woman at the beach\nunder the summer sky.")
        self.assertIn("revisionA", meta)
        self.assertEqual(joined, prompts[0])
        self.assertEqual(output["ui"]["prompt_dataset"][0]["count"], 1)
        self.assertEqual(dataset.replace_placeholder("Malice & ALICE", "Alice", "trigger"),
                         "Malice & trigger")


    def test_sequential_queue_node(self):
        frozen = dataset.select(self.id, {"mode": "manual", "ids": [2, 1]})["snapshot"]
        data = json.dumps(frozen)
        node = dataset.PromptDatasetQueueNode
        self.assertTrue(node.OUTPUT_NODE)
        self.assertFalse(getattr(node, "OUTPUT_IS_LIST", False))
        self.assertEqual(node.RETURN_NAMES, ("prompt", "index", "total", "metadata_json"))
        self.assertNotEqual(
            node.IS_CHANGED("name", data, "Alice", "sH1VX woman", 0),
            node.IS_CHANGED("name", data, "Alice", "sH1VX woman", 1),
        )
        first = node.run("name", data, "Alice", "sH1VX woman", 0)
        self.assertEqual(first["result"][0], "sH1VX woman at sunset with a dress")
        self.assertEqual(first["result"][1:3], (0, 2))
        self.assertEqual(first["ui"]["prompt_dataset_queue"][0]["index"], 0)
        second = node.run("name", data, "Alice", "sH1VX woman", 1)
        self.assertIn("at the beach", second["result"][0])
        self.assertEqual(json.loads(second["result"][3])["id"], "unit_test:1")
        with self.assertRaisesRegex(ValueError, "out of range"):
            node.run("name", data, "Alice", "sH1VX woman", 2)
        with self.assertRaisesRegex(ValueError, "no selected prompts"):
            node.run("name", '{"version":1,"selected":[]}', "Alice", "sH1VX woman", 0)

    def test_bad_sql_filter_and_traversal(self):
        for name in ("../escape", "..", "a/b", ""):
            with self.assertRaises(ValueError):
                dataset.dataset_dir(name)
        with self.assertRaises(ValueError):
            dataset.search(self.id, {"filters": [
                {"field": "caption); DROP TABLE prompts;--", "enabled": True, "value": "abc"},
            ]})
        with self.assertRaises(ValueError):
            dataset.select(self.id, {"mode": "manual", "ids": [-1]})
        with self.assertRaises(ValueError):
            dataset.select(self.id, {"mode": "random", "sample_size": 2001})
        with self.assertRaises(ValueError):
            dataset.search(self.id, {"include": ["a"] * 21})
        self.assertEqual(dataset.search(self.id, {"include": ["%"]})["matched"], 0)

    def test_atomic_failure_retains_index(self):
        broken = Path(self.tmp.name) / "broken.csv"
        broken.write_text("BadHeader\nno caption\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            dataset.import_dataset(broken, self.id, "test", "revisionB")
        self.assertEqual(dataset.search(self.id, {})["total"], 4)
        self.assertEqual(dataset.read_manifest(self.id)["revision"], "revisionA")


if __name__ == "__main__":
    unittest.main()
