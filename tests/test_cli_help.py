# -*- coding: utf-8 -*-
"""`python -m taskplan help` muss die real implementierten Befehle vollstaendig
nennen. Insbesondere `projects` (list/refresh/add/remove/flag/unflag/markers)
wurde im Dispatch von main() bedient, tauchte aber nicht in der Hilfe auf --
ein Befehl, den man nur durch Lesen des Quellcodes finden konnte.
"""
import io
import unittest
from contextlib import redirect_stdout

from taskplan.__main__ import main


class TestHelpListsAllCommands(unittest.TestCase):
    def _help_text(self) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["help"])
        self.assertEqual(code, 0)
        return output.getvalue()

    def test_all_top_level_commands_are_listed(self):
        """Jeder im Dispatch von main() bediente Befehl muss in der Hilfe stehen."""
        text = self._help_text()
        for command in (
            "next", "doctor", "projects", "prompt", "runtime",
            "startup-prompt", "backoff", "launch", "starters", "skip",
        ):
            with self.subTest(command=command):
                self.assertIn(command, text)

    def test_projects_subcommands_are_listed(self):
        """Die projects-Familie war der eigentliche Befund: implementiert in
        _projects_command(), aber in der Hilfe unerwaehnt."""
        text = self._help_text()
        for action in ("list", "refresh", "add", "remove", "flag", "unflag",
                       "markers"):
            with self.subTest(action=action):
                self.assertIn(action, text)

    def test_no_help_and_no_args_agree(self):
        """`help` explizit und der implizite Default (keine Argumente) liefern
        denselben Text, damit die Hilfe nicht an zwei Stellen auseinanderlaeuft."""
        explicit = io.StringIO()
        with redirect_stdout(explicit):
            main(["help"])
        implicit = io.StringIO()
        with redirect_stdout(implicit):
            main([])
        self.assertEqual(explicit.getvalue(), implicit.getvalue())


if __name__ == "__main__":
    unittest.main()
