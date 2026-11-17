"""First-run race: the helper must find the stack's interpreter or say clearly why not."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_helper_bugs import helper


class Reexec(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.python = Path(self.directory.name) / 'venv-python'
        self.python.write_text('#!/bin/sh\n')
        self.python.chmod(0o755)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(helper.REEXEC_ENV, None)

    def test_a_different_stack_interpreter_is_used(self):
        with patch.object(helper.voice_config, 'python', return_value=str(self.python)):
            self.assertEqual(helper.stack_interpreter(), str(self.python))

    def test_the_same_interpreter_is_left_alone(self):
        with patch.object(helper.voice_config, 'python', return_value=sys.executable):
            self.assertIsNone(helper.stack_interpreter())

    def test_a_missing_interpreter_is_ignored(self):
        with patch.object(helper.voice_config, 'python', return_value='/nonexistent/python'):
            self.assertIsNone(helper.stack_interpreter())

    def test_the_switch_happens_once(self):
        with patch.object(helper.voice_config, 'python', return_value=str(self.python)), \
             patch.object(helper.os, 'execv') as execv:
            helper.reexec_into_stack('say')
            execv.assert_called_once()
            self.assertEqual(execv.call_args[0][0], str(self.python))
            self.assertEqual(os.environ[helper.REEXEC_ENV], '1')
            self.assertIsNone(helper.stack_interpreter())

    def test_commands_without_the_stack_do_not_switch(self):
        with patch.object(helper.voice_config, 'python', return_value=str(self.python)), \
             patch.object(helper.os, 'execv') as execv:
            for command in ('doctor', 'devices', 'play', 'record'):
                helper.reexec_into_stack(command)
        execv.assert_not_called()

    def test_a_failed_switch_is_not_fatal(self):
        with patch.object(helper.voice_config, 'python', return_value=str(self.python)), \
             patch.object(helper.os, 'execv', side_effect=OSError('nope')):
            helper.reexec_into_stack('dictate')


class Clear(unittest.TestCase):
    def test_missing_packages_name_the_fix_instead_of_a_traceback(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'web_voice.py').write_text("import numpy_that_is_missing\n")
            with patch.object(helper, 'SERVICE', Path(root)):
                with self.assertRaises(helper.StackNotReady) as raised:
                    helper.voice()
        text = str(raised.exception)
        self.assertIn('not ready', text)
        self.assertIn('setup_stack.py --install', text)
        self.assertIn('Local speech is ready', text)
        self.assertNotIn('Traceback', text)

    def test_missing_stack_is_the_same_kind_of_error(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(helper, 'SERVICE', Path(root) / 'none'):
                with self.assertRaises(helper.StackNotReady):
                    helper.voice()


if __name__ == '__main__':
    unittest.main()
