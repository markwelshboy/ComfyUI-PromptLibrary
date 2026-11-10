"""Image metadata replay tests; Pillow is a dependency of ComfyUI."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("image_import", ROOT / "image_import.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

try:
    from PIL import Image, PngImagePlugin
except ImportError:
    Image = None


class ImageReplayTests(unittest.TestCase):
    def test_comfy_execution_graph(self):
        graph = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "window portrait"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry"}},
            "3": {"class_type": "KSampler", "inputs": {"seed": 12345, "positive": ["1", 0], "negative": ["2", 0]}},
            "4": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0]}},
            "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0]}},
        }
        self.assertEqual(module.extract_comfy(json.dumps(graph)), {"text": "window portrait", "seed": 12345})
        self.assertIsNone(module.extract_comfy("{}"))

    def test_seed_from_randomnoise(self):
        graph = {
            "1": {"class_type": "TextEncode", "inputs": {"prompt": "test portrait"}},
            "2": {"class_type": "RandomNoise", "inputs": {"noise_seed": 9876}},
            "3": {"class_type": "SamplerCustomAdvanced", "inputs": {"guider": ["4", 0], "noise": ["2", 0]}},
            "4": {"class_type": "BasicGuider", "inputs": {"positive": ["1", 0], "negative": ["9", 0]}},
            "9": {"class_type": "TextEncode", "inputs": {"text": "negative"}},
        }
        self.assertEqual(module.extract_comfy(json.dumps(graph)), {"text": "test portrait", "seed": 9876})

    def test_a1111_parameters(self):
        p = "a red flower\nNegative prompt: blurry\nSteps: 22, Sampler: Euler a, Seed: 451, Size: 1024x1280"
        self.assertEqual(module.extract_parameters(p), {"text": "a red flower", "seed": 451})

    def test_ratios(self):
        self.assertEqual(module.ratio_for_size(1280, 1600), "4:5")
        self.assertEqual(module.ratio_for_size(768, 1152), "2:3")
        self.assertEqual(module.ratio_for_size(1024, 1024), "1:1")

    @unittest.skipUnless(Image is not None, "Pillow needed")
    def test_scan_and_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = PngImagePlugin.PngInfo()
            graph = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "soft portrait"}},
                     "2": {"class_type": "KSampler", "inputs": {"seed": 47, "positive": ["1", 0]}}}
            meta.add_text("prompt", json.dumps(graph))
            Image.new("RGB", (640, 800)).save(root / "accepted.png", pnginfo=meta)
            Image.new("RGB", (640, 800)).save(root / "skipped.png")
            report = module.scan_images(str(root))
            self.assertEqual(report["scanned"], 2)
            self.assertEqual(report["skipped_count"], 1)
            self.assertEqual(report["entries"][0]["seed"], "47")
            self.assertEqual(report["entries"][0]["aspect_ratio"], "4:5")
            self.assertEqual(report["entries"][0]["text"], "soft portrait")
            self.assertEqual(report["entries"][0]["width"], 640)

    def test_seed_bigger_than_javascript_safe_integer(self):
        with tempfile.TemporaryDirectory() as tmp:
            if Image is None:
                self.skipTest("Pillow needed")
            from PIL import PngImagePlugin
            root = Path(tmp)
            large_seed = 18446744073709551615
            meta = PngImagePlugin.PngInfo()
            graph = {"1": {"class_type": "TextEncode", "inputs": {"text": "portrait"}},
                     "2": {"class_type": "KSampler", "inputs": {"seed": large_seed, "positive": ["1", 0]}}}
            meta.add_text("prompt", json.dumps(graph))
            Image.new("RGB", (800, 1000)).save(root / "big_seed.png", pnginfo=meta)
            self.assertEqual(module.scan_images(str(root))["entries"][0]["seed"], str(large_seed))

    def test_invalid_folder(self):
        with self.assertRaises(ValueError):
            module.scan_images("/a/path/that/does/not/exist/promptlibrary")


if __name__ == "__main__":
    unittest.main()
