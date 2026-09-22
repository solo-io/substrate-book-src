import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("workbench_server", ROOT / "workbench" / "server.py")
SERVER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(SERVER)


class WorkbenchStateTest(unittest.TestCase):
    def make_state(self, directory, actor_info=None):
        return SERVER.WorkbenchState(
            Path(directory) / "workspace",
            ROOT / "sample-project",
            actor_info or Path(directory) / "actor-info",
        )

    def test_steps_advance_one_at_a_time_and_create_durable_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.make_state(directory)

            initial = state.snapshot()
            self.assertEqual(initial["phase"], "ready")
            self.assertEqual(initial["currentStep"], 0)
            self.assertEqual(initial["nextStep"], "Inspect repository")

            state.advance(0)
            after_tests = state.advance(1)
            self.assertEqual(after_tests["currentStep"], 2)
            self.assertIn("FAILED", after_tests["tests"])

            state.advance(2)
            after_repair = state.advance(3)
            source = next(item for item in after_repair["files"] if item["path"] == "src/slugify.py")
            self.assertEqual(source["status"], "modified")
            self.assertIn("normalized = re.sub", source["content"])

            after_report = state.advance(4)
            report = next(item for item in after_report["files"] if item["path"] == "AGENT_REPORT.md")
            self.assertEqual(report["status"], "added")

            after_verify = state.advance(5)
            self.assertIn("OK", after_verify["tests"])

            completed = state.advance(6)
            self.assertEqual(completed["phase"], "done")
            self.assertEqual(completed["currentStep"], completed["totalSteps"])
            self.assertIn("normalized = re.sub", completed["diff"])
            self.assertIn("AGENT_REPORT.md", completed["diff"])

    def test_fast_forward_uses_the_same_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.make_state(directory)
            completed = state.fast_forward(0)

            self.assertEqual(completed["phase"], "done")
            self.assertEqual(completed["currentStep"], len(SERVER.STEP_TITLES))
            self.assertTrue(all(step["status"] == "complete" for step in completed["steps"]))
            self.assertIn("Repository task complete", [event["title"] for event in completed["transcript"]])

    def test_expected_step_prevents_duplicate_advancement(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.make_state(directory)
            state.advance(0)

            with self.assertRaises(SERVER.StepConflict):
                state.advance(0)

            state.fast_forward(1)
            with self.assertRaises(SERVER.StepConflict):
                state.advance(len(SERVER.STEP_TITLES))

    def test_identity_falls_back_outside_substrate(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.make_state(directory)
            self.assertEqual(state.identity()["name"], "local-workbench")

    def test_identity_is_read_from_system_info_files(self):
        with tempfile.TemporaryDirectory() as directory:
            actor_info = Path(directory) / "actor-info"
            actor_info.mkdir()
            (actor_info / "actor-name").write_text("repo-workbench-7", encoding="utf-8")
            (actor_info / "atespace").write_text("book-workbench", encoding="utf-8")
            (actor_info / "actor-uid").write_text("uid-123", encoding="utf-8")
            state = self.make_state(directory, actor_info)

            self.assertEqual(
                state.identity(),
                {"name": "repo-workbench-7", "atespace": "book-workbench", "uid": "uid-123"},
            )


if __name__ == "__main__":
    unittest.main()
