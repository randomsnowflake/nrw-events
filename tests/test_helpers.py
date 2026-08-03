import unittest

from tests.helpers import default_window, make_event, make_runner_env


class SharedFixtureTests(unittest.TestCase):
    def test_event_builder_applies_narrow_overrides(self):
        event = make_event(title="Override", city="Köln")

        self.assertEqual(event["title"], "Override")
        self.assertEqual(event["city"], "Köln")
        self.assertEqual(event["source"], "Test Source")

    def test_default_window_is_fresh_and_deterministic(self):
        first = default_window()
        second = default_window()

        self.assertIsNot(first, second)
        self.assertEqual(first, second)

    def test_runner_environment_cleans_up_its_directory(self):
        with make_runner_env() as environment:
            root = environment.root
            context = environment.context()
            self.assertTrue(root.is_dir())
            self.assertEqual(context.settings.previous_meta_json, str(environment.previous_path))

        self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
