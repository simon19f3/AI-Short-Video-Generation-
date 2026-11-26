import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline import media
from pipeline.__main__ import Film
from pipeline.core import Cache, PipelineError, ROOT, RunLog, load_story


class StoryboardTests(unittest.TestCase):
    def setUp(self):
        self.story = load_story(ROOT / "storyboard.json")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)

    def test_duplicate_shot_id_fails_validation(self):
        self.story["shots"][1]["id"] = self.story["shots"][0]["id"]
        path = self.work / "storyboard.json"
        path.write_text(json.dumps(self.story), encoding="utf-8")
        with self.assertRaisesRegex(PipelineError, "unique id"):
            load_story(path)

    def test_narration_edit_generates_one_new_model_call(self):
        log = RunLog(self.work / "runs.jsonl")
        film = Film(self.story, self.work / "output", Cache(self.work / "cache"), log, False)
        shots = self.story["shots"][:2]

        def generate(target, params):
            media.silence(target, 2.1 if params["text"].endswith("!") else 2)

        with patch("pipeline.providers.narrate", side_effect=generate) as provider:
            film.narration(shots)
            self.assertEqual(provider.call_count, 2)
            film.narration(shots)
            self.assertEqual(provider.call_count, 2)

            revised = copy.deepcopy(shots)
            revised[0]["subtitle"] = "Edited English subtitle."
            film.narration(revised)
            self.assertEqual(provider.call_count, 2)

            revised[0]["narration"] += "!"
            film.narration(revised)
            self.assertEqual(provider.call_count, 3)
            self.assertTrue(provider.call_args.args[1]["text"].endswith("!"))

        events = [json.loads(row) for row in log.path.read_text().splitlines()]
        self.assertEqual([event["item"] for event in events], ["baby", "preschool", "baby"])
        self.assertEqual([event["status"] for event in events], ["ok", "ok", "ok"])
        self.assertNotIn("text", events[-1]["parameters"])

    def test_failed_generation_does_not_leave_a_cache_hit(self):
        cache = Cache(self.work / "cache")

        def fail(target):
            target.write_bytes(b"incomplete")
            raise PipelineError("generation failed")

        with self.assertRaisesRegex(PipelineError, "generation failed"):
            cache.get("test", "shot", {"seed": 1}, ".bin", fail)
        output = cache.get("test", "shot", {"seed": 1}, ".bin", lambda path: path.write_bytes(b"complete"))
        self.assertEqual(output.read_bytes(), b"complete")
