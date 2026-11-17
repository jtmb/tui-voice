"""Configuration precedence for paths and audio overrides; no stack is required."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voice_config


class DefaultsTests(unittest.TestCase):
    def test_plugin_owns_its_default_directories(self):
        with patch.dict(os.environ, {'XDG_DATA_HOME': '/data',
                                     'TUI_VOICE_CONFIG': '/nonexistent/config.json'},
                        clear=True):
            self.assertEqual(voice_config.service(), Path('/data/tui-voice/service'))
            self.assertEqual(voice_config.voice(), Path('/data/tui-voice/voice'))

    def test_home_fallbacks_when_xdg_is_unset(self):
        with tempfile.TemporaryDirectory() as home, \
             patch.dict(os.environ, {'HOME': home}, clear=True):
            self.assertEqual(voice_config.service(),
                             Path(home) / '.local/share/tui-voice/service')
            self.assertEqual(voice_config.config_path(),
                             Path(home) / '.config/tui-voice/config.json')

    def test_no_repository_is_named_in_a_default(self):
        with patch.dict(os.environ, {'TUI_VOICE_CONFIG': '/nonexistent/config.json'},
                        clear=True):
            for value in (voice_config.service(), voice_config.voice(),
                          voice_config.config_path()):
                self.assertNotIn('fire-tv-next', str(value))
                self.assertNotIn('lulu', str(value))


class ConfigFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'config.json'
        self.environment = {'TUI_VOICE_CONFIG': str(self.path),
                            'XDG_DATA_HOME': self.directory.name}
        patcher = patch.dict(os.environ, self.environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, payload):
        self.path.write_text(json.dumps(payload), encoding='utf-8')

    def test_config_supplies_paths_and_audio_overrides(self):
        self.write({'service': '~/stack/service', 'voice': '/srv/voice',
                    'ffmpeg': '/usr/bin/ffmpeg', 'player': 'mpv --no-video',
                    'mic': 'USB Mic'})
        self.assertEqual(voice_config.service(), Path.home() / 'stack/service')
        self.assertEqual(voice_config.voice(), Path('/srv/voice'))
        self.assertEqual(voice_config.audio('ffmpeg'), '/usr/bin/ffmpeg')
        self.assertEqual(voice_config.audio('player'), 'mpv --no-video')
        self.assertEqual(voice_config.audio('mic'), 'USB Mic')

    def test_environment_wins_over_the_file(self):
        self.write({'service': '/from/file', 'voice': '/from/file/voice'})
        with patch.dict(os.environ, {'TUI_VOICE_SERVICE': '/from/env'}):
            self.assertEqual(voice_config.service(), Path('/from/env'))
            self.assertEqual(voice_config.voice(), Path('/from/file/voice'))

    def test_broken_json_is_reported_and_ignored(self):
        self.path.write_text('{not json', encoding='utf-8')
        self.assertEqual(voice_config.service(),
                         Path(self.directory.name) / 'tui-voice/service')
        self.assertIn('not valid JSON', voice_config.problem())
        self.assertTrue(voice_config.describe()['config_error'])

    def test_non_object_json_is_rejected(self):
        self.write(['service'])
        self.assertTrue(voice_config.problem())

    def test_variable_expansion(self):
        self.write({'service': '$STACK/service'})
        with patch.dict(os.environ, {'STACK': '/opt/stack'}):
            self.assertEqual(voice_config.service(), Path('/opt/stack/service'))


class InterpreterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        patcher = patch.dict(os.environ, {
            'TUI_VOICE_CONFIG': str(self.root / 'missing.json'),
            'XDG_DATA_HOME': str(self.root),
        }, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def venv(self, directory, name='python'):
        binary = directory / 'bin' / name
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text('')
        return binary

    def test_explicit_python_wins(self):
        with patch.dict(os.environ, {'TUI_VOICE_PYTHON': '/opt/custom/python'}):
            self.assertEqual(voice_config.python(), '/opt/custom/python')

    def test_service_virtualenv_is_used_when_it_exists(self):
        service = self.root / 'tui-voice/service'
        binary = self.venv(service / '.venv')
        self.assertEqual(voice_config.python(), str(binary))

    def test_worker_interpreters_prefer_their_own_venvs(self):
        voice = self.root / 'tui-voice/voice'
        asr = self.venv(voice / '.asr-venv')
        tts = self.venv(voice / '.venv')
        self.assertEqual(voice_config.asr_python(), str(asr))
        self.assertEqual(voice_config.tts_python(), str(tts))

    def test_missing_worker_venvs_fall_back_to_the_helper_interpreter(self):
        resolved = voice_config.python()
        self.assertEqual(voice_config.asr_python(), resolved)
        self.assertEqual(voice_config.tts_python(), resolved)

    def test_describe_is_safe_without_a_stack(self):
        described = voice_config.describe()
        self.assertFalse(described['service_exists'])
        self.assertFalse(described['voice_exists'])
        self.assertIsNone(described['config_error'])
        self.assertTrue(described['python'])


if __name__ == '__main__':
    unittest.main()
