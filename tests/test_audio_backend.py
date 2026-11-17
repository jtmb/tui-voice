"""Platform audio backend checks: no real device or player is touched."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import audio_backend as backend
import voice_config


def completed(stdout='', returncode=0, stderr=''):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def isolate_config(test):
    """Never read the developer's real config file in a hermetic test."""
    patcher = patch.object(voice_config, 'config_path',
                           lambda environ=None: Path('/nonexistent/tui-voice/config.json'))
    patcher.start()
    test.addCleanup(patcher.stop)


PULSE_SOURCES = [
    {'index': 1, 'name': 'alsa_output.hdmi.monitor', 'description': 'Monitor of HDMI',
     'monitor_source': 'alsa_output.hdmi'},
    {'index': 2, 'name': 'alsa_output.speakers.monitor',
     'description': 'Speakers Stereo', 'monitor_source': '',
     'properties': {'device.class': 'monitor'}},
    {'index': 3, 'name': 'alsa_input.usb-mic', 'description': 'BRIDGE CAST V2 Mic',
     'monitor_source': ''},
    {'index': 4, 'name': 'alsa_input.usb-streammix',
     'description': 'BRIDGE CAST V2 StreamMix', 'monitor_source': ''},
    {'index': 5, 'name': 'alsa_input.usb-webcam',
     'description': 'C920 PRO HD Webcam Analog Stereo', 'monitor_source': ''},
]


class PlatformTests(unittest.TestCase):
    def test_environment_markers_detect_wsl2(self):
        for name in ('WSL_DISTRO_NAME', 'WSL_INTEROP'):
            with self.subTest(name=name):
                self.assertEqual(
                    backend.detect_platform({name: 'Ubuntu-24.04'}, 'Linux version 6.1'),
                    backend.WSL2)

    def test_proc_version_marker_detects_wsl2(self):
        self.assertEqual(
            backend.detect_platform({}, 'Linux version 5.15.90.1-microsoft-standard-WSL2'),
            backend.WSL2)

    def test_plain_and_missing_proc_version_are_native_linux(self):
        self.assertEqual(
            backend.detect_platform({}, 'Linux version 6.12.1-arch1'), backend.LINUX)
        with patch.object(backend, '_proc_version', return_value=''):
            self.assertEqual(backend.detect_platform({}, None), backend.LINUX)

    def test_empty_environment_still_honours_proc_version(self):
        self.assertEqual(
            backend.detect_platform({'WSL_INTEROP': ''},
                                    'Linux version 5.15-microsoft-standard-WSL2'),
            backend.WSL2)


class LinuxDeviceTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        platform = patch.object(backend, 'PLATFORM', backend.LINUX)
        platform.start()
        self.addCleanup(platform.stop)
        isolate_config(self)

    def pulse(self, payload=None, default=None):
        text = json.dumps(PULSE_SOURCES if payload is None else payload)

        def run(command, **_kwargs):
            if command[:2] == ['pactl', '-f']:
                return completed(text)
            if command[:3] == ['pactl', 'get-default-source']:
                return completed(default or '', 0 if default else 1)
            return completed('', 1)

        patcher = patch.object(backend.subprocess, 'run', side_effect=run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pulse_json_marks_monitor_sources(self):
        self.pulse()
        self.assertEqual(
            backend.devices(),
            ['Monitor of HDMI', 'Speakers Stereo', 'BRIDGE CAST V2 Mic',
             'BRIDGE CAST V2 StreamMix', 'C920 PRO HD Webcam Analog Stereo'])
        self.assertTrue(backend.resolve_device('Monitor of HDMI').monitor)
        # device.class=monitor counts even when monitor_source is empty.
        self.assertTrue(backend.resolve_device('Speakers Stereo').monitor)
        self.assertFalse(backend.resolve_device('BRIDGE CAST V2 Mic').monitor)

    def test_automatic_selection_prefers_hardware_microphone(self):
        self.pulse()
        selected, names = backend.choose_mic(environ={})
        self.assertEqual(selected, 'BRIDGE CAST V2 Mic')
        self.assertIn('BRIDGE CAST V2 StreamMix', names)

    def test_os_default_source_outranks_higher_base_hardware(self):
        # "BRIDGE CAST V2 Mic" scores 3 by name; the webcam default scores 2 and
        # must still win through the +4 default bonus.
        self.pulse(default='alsa_input.usb-webcam')
        mic = backend.resolve_device('BRIDGE CAST V2 Mic')
        webcam = backend.resolve_device('C920 PRO HD Webcam Analog Stereo')
        self.assertGreater(backend.source_score(mic), backend.source_score(webcam))
        selected, _ = backend.choose_mic(environ={})
        self.assertEqual(selected, 'C920 PRO HD Webcam Analog Stereo')

    def test_virtual_default_source_still_loses_to_hardware(self):
        self.pulse(default='alsa_input.usb-streammix')
        selected, _ = backend.choose_mic(environ={})
        self.assertEqual(selected, 'BRIDGE CAST V2 Mic')
        webcam = backend.resolve_device('C920 PRO HD Webcam Analog Stereo')
        for name in ('BRIDGE CAST V2 StreamMix', 'Monitor of HDMI'):
            with self.subTest(name=name):
                virtual = backend.resolve_device(name)
                self.assertTrue(backend._is_virtual(virtual))
                self.assertLess(
                    backend.source_score(virtual, 'alsa_input.usb-streammix'),
                    backend.source_score(webcam, 'alsa_input.usb-streammix'))

    def test_unavailable_default_query_keeps_previous_scoring(self):
        self.pulse()
        self.assertIsNone(backend._default_source())
        selected, _ = backend.choose_mic(environ={})
        self.assertEqual(selected, 'BRIDGE CAST V2 Mic')
        for name in ('BRIDGE CAST V2 Mic', 'C920 PRO HD Webcam Analog Stereo'):
            with self.subTest(name=name):
                source = backend.resolve_device(name)
                self.assertEqual(backend.source_score(source),
                                 backend.source_score(source, None))

    def test_environment_preference_and_raw_ids_are_accepted(self):
        self.pulse()
        selected, _ = backend.choose_mic(environ={'TUI_VOICE_MIC': 'BRIDGE CAST V2 StreamMix'})
        self.assertEqual(selected, 'BRIDGE CAST V2 StreamMix')
        selected, _ = backend.choose_mic(environ={'TUI_VOICE_MIC': 'alsa_input.usb-webcam'})
        self.assertEqual(selected, 'C920 PRO HD Webcam Analog Stereo')

    def test_unknown_requested_microphone_is_reported(self):
        self.pulse()
        with self.assertRaisesRegex(backend.AudioBackendError, 'not available'):
            backend.choose_mic('Vanished microphone', environ={})

    def test_duplicate_descriptions_are_suffixed_and_resolve(self):
        self.pulse([
            {'index': 1, 'name': 'alsa_input.one', 'description': 'USB Mic',
             'monitor_source': ''},
            {'index': 2, 'name': 'alsa_input.two', 'description': 'USB Mic',
             'monitor_source': ''},
        ])
        names = backend.devices()
        self.assertEqual(names, ['USB Mic (alsa_input.one)', 'USB Mic (alsa_input.two)'])
        self.assertEqual(backend.resolve_device(names[1]).id, 'alsa_input.two')
        self.assertEqual(backend.resolve_device('alsa_input.one').id, 'alsa_input.one')

    def test_short_and_alsa_fallbacks(self):
        def run(command, **_kwargs):
            if command[:2] == ['pactl', '-f']:
                return completed('', 1)
            if command[:2] == ['pactl', 'list']:
                return completed('0\talsa_input.usb-mic\tPipeWire\tfloat32le 2ch\n'
                                 '1\talsa_output.hdmi.monitor\tPipeWire\tfloat32le 2ch\n')
            return completed('', 1)

        with patch.object(backend.subprocess, 'run', side_effect=run):
            self.assertEqual(backend.devices(),
                             ['alsa_input.usb-mic', 'alsa_output.hdmi.monitor'])
            self.assertTrue(backend.resolve_device('alsa_output.hdmi.monitor').monitor)

        def alsa_only(command, **_kwargs):
            if command[:2] == ['arecord', '-l']:
                return completed(
                    '**** List of CAPTURE Hardware Devices ****\n'
                    'card 2: V2 [BRIDGE CAST V2], device 0: USB Audio [USB Audio]\n')
            return completed('', 1)

        with patch.object(backend.subprocess, 'run', side_effect=alsa_only):
            self.assertEqual(backend.devices(), ['BRIDGE CAST V2: USB Audio'])
            self.assertEqual(backend.resolve_device('BRIDGE CAST V2: USB Audio').id,
                             'plughw:2,0')

    def test_no_enumeration_falls_back_to_default_source(self):
        with patch.object(backend.subprocess, 'run', return_value=completed('', 1)):
            self.assertEqual(backend.devices(), ['System default input'])
            selected, _ = backend.choose_mic(environ={})
            self.assertEqual(selected, 'System default input')
            self.assertEqual(backend.resolve_device('default').id, 'default')

    def test_capture_arguments_use_pulse_or_alsa(self):
        self.pulse()
        self.assertEqual(backend.capture_args('BRIDGE CAST V2 Mic'),
                         ['-f', 'pulse', '-i', 'alsa_input.usb-mic'])
        self.assertEqual(backend.capture_args('alsa_input.usb-webcam'),
                         ['-f', 'pulse', '-i', 'alsa_input.usb-webcam'])
        with patch.object(backend.subprocess, 'run', return_value=completed('', 1)):
            self.assertEqual(backend.capture_args('plughw:2,0'),
                             ['-f', 'alsa', '-i', 'plughw:2,0'])
            self.assertEqual(backend.capture_args('Unknown input: capture'),
                             ['-f', 'pulse', '-i', 'Unknown input: capture'])


class CaptureArgumentTests(unittest.TestCase):
    def setUp(self):
        isolate_config(self)

    def test_wsl_dshow_arguments_are_unchanged(self):
        with patch.object(backend, 'PLATFORM', backend.WSL2):
            self.assertEqual(backend.capture_args('Focusrite Analog'),
                             ['-f', 'dshow', '-rtbufsize', '64M',
                              '-i', 'audio=Focusrite Analog'])
            self.assertEqual(backend.capture_args('Focusrite Analog', streaming=True),
                             ['-f', 'dshow', '-rtbufsize', '64M',
                              '-audio_buffer_size', '50', '-i', 'audio=Focusrite Analog'])

    def test_wsl_ffmpeg_override_and_windows_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'ffmpeg.exe'
            binary.write_text('')
            shell = Path(directory) / 'powershell.exe'
            shell.write_text('')
            with patch.object(backend, 'PLATFORM', backend.WSL2), \
                 patch.dict(os.environ, {'TUI_VOICE_FFMPEG': str(binary)}):
                self.assertEqual(backend.ffmpeg(), str(binary))
            with patch.object(backend, 'PLATFORM', backend.WSL2), \
                 patch.dict(os.environ, {'TUI_VOICE_POWERSHELL': str(shell)}), \
                 patch.object(backend.subprocess, 'run',
                              return_value=completed('C:\\Temp\\tui-voice.wav')):
                self.assertEqual(backend.output_path('/mnt/c/Temp/tui-voice.wav'),
                                 'C:\\Temp\\tui-voice.wav')
                command = backend.playback_command('/mnt/c/Temp/tui-voice.wav')
            self.assertEqual(command[0], str(shell))
            self.assertIn('PlaySync()', command[-1])
            self.assertIn('C:\\Temp\\tui-voice.wav', command[-1])

    def test_linux_ffmpeg_discovery_and_failure_message(self):
        with patch.object(backend, 'PLATFORM', backend.LINUX), \
             patch.object(backend.shutil, 'which', return_value=None), \
             patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(backend.AudioBackendError, 'ffmpeg package'):
                backend.ffmpeg()
        with patch.object(backend, 'PLATFORM', backend.LINUX), \
             patch.object(backend.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
             patch.dict(os.environ, {}, clear=True):
            self.assertEqual(backend.ffmpeg(), '/usr/bin/ffmpeg')

    def test_linux_output_path_is_local(self):
        with patch.object(backend, 'PLATFORM', backend.LINUX):
            self.assertEqual(backend.output_path('/tmp/opencode/a.wav'), '/tmp/opencode/a.wav')

    def test_capture_backend_can_be_forced(self):
        with patch.object(backend, 'PLATFORM', backend.LINUX), \
             patch.object(backend.subprocess, 'run', return_value=completed('', 1)), \
             patch.dict(os.environ, {'TUI_VOICE_CAPTURE': 'alsa'}):
            self.assertEqual(backend.capture_args('default'), ['-f', 'alsa', '-i', 'default'])
        with patch.object(backend, 'PLATFORM', backend.LINUX), \
             patch.object(backend.subprocess, 'run', return_value=completed('', 1)), \
             patch.object(backend, '_pulse_reachable', return_value=False), \
             patch.dict(os.environ, {'TUI_VOICE_CAPTURE': 'nonsense'}):
            self.assertEqual(backend.capture_args('default'), ['-f', 'alsa', '-i', 'default'])

    def test_windows_paths_are_translated_without_a_fixed_drive(self):
        self.assertEqual(backend._windows_to_wsl('C:\\Users\\A\\Temp'),
                         '/mnt/c/Users/A/Temp')
        self.assertEqual(backend._windows_to_wsl('D:\\media\\a.wav'),
                         '/mnt/d/media/a.wav')
        self.assertIsNone(backend._windows_to_wsl('relative/path'))

    def test_wsl_ffmpeg_uses_where_exe_without_hardcoded_profiles(self):
        with patch.object(backend, 'PLATFORM', backend.WSL2), \
             patch.object(backend.shutil, 'which', return_value=None), \
             patch.object(backend, '_where_exe',
                          return_value='/mnt/d/ffmpeg/bin/ffmpeg.exe') as where, \
             patch.dict(os.environ, {}, clear=True):
            self.assertEqual(backend.ffmpeg(), '/mnt/d/ffmpeg/bin/ffmpeg.exe')
            where.assert_called_once_with('ffmpeg.exe')

    def test_powershell_override_reaches_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'powershell.exe'
            binary.write_text('')
            with patch.object(backend, 'PLATFORM', backend.WSL2), \
                 patch.object(backend, 'output_path', return_value='C:\\Temp\\a.wav'), \
                 patch.dict(os.environ, {'TUI_VOICE_POWERSHELL': str(binary)}):
                command = backend.playback_command('/tmp/opencode/a.wav')
            self.assertEqual(command[0], str(binary))
            self.assertIn('PlaySync()', command[-1])
            self.assertIn('C:\\Temp\\a.wav', command[-1])


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        platform = patch.object(backend, 'PLATFORM', backend.LINUX)
        platform.start()
        self.addCleanup(platform.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.runtime = Path(self.directory.name)
        isolate_config(self)

    def player(self, names, pactl=True, pipewire=True):
        def which(name):
            return '/usr/bin/' + name if name in names else None

        def run(command, **_kwargs):
            if command[:2] == ['pactl', 'info']:
                return completed('Server String: pipewire-0' if pactl else '', 0 if pactl else 1)
            return completed('', 1)

        if pipewire:
            (self.runtime / 'pipewire-0').touch()
        return [
            patch.object(backend.shutil, 'which', side_effect=which),
            patch.object(backend.subprocess, 'run', side_effect=run),
        ]

    def command(self, environ, players, pactl=True, pipewire=True):
        patchers = self.player(players, pactl=pactl, pipewire=pipewire)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        return backend.playback_command('/tmp/opencode/reply.wav', environ=environ)

    def test_override_player_is_shlex_split_and_blocking_tool_first(self):
        command = self.command({'TUI_VOICE_PLAYER': 'mpv --no-video'}, set())
        self.assertEqual(command, ['mpv', '--no-video', '/tmp/opencode/reply.wav'])

    def test_player_placeholder_can_appear_anywhere(self):
        command = self.command(
            {'TUI_VOICE_PLAYER': 'cvlc --play-and-exit {file} --quiet'}, set())
        self.assertEqual(command,
                         ['cvlc', '--play-and-exit', '/tmp/opencode/reply.wav', '--quiet'])

    def test_pulse_socket_enables_paplay_without_pactl(self):
        (self.runtime / 'pulse').mkdir()
        (self.runtime / 'pulse/native').touch()
        command = self.command({'XDG_RUNTIME_DIR': str(self.runtime)}, {'paplay'},
                               pactl=False, pipewire=False)
        self.assertEqual(command, ['paplay', '/tmp/opencode/reply.wav'])

    def test_pipewire_player_is_preferred_when_socket_exists(self):
        command = self.command({'XDG_RUNTIME_DIR': str(self.runtime)},
                               {'pw-play', 'paplay', 'aplay'})
        self.assertEqual(command, ['pw-play', '/tmp/opencode/reply.wav'])

    def test_pulse_player_is_used_when_pipewire_socket_is_absent(self):
        command = self.command({'XDG_RUNTIME_DIR': str(self.runtime)},
                               {'pw-play', 'paplay', 'aplay'}, pipewire=False)
        self.assertEqual(command, ['paplay', '/tmp/opencode/reply.wav'])

    def test_alsa_then_ffplay_then_error(self):
        command = self.command({'XDG_RUNTIME_DIR': str(self.runtime)},
                               {'aplay', 'ffplay'}, pactl=False, pipewire=False)
        self.assertEqual(command, ['aplay', '/tmp/opencode/reply.wav'])
        command = self.command({'XDG_RUNTIME_DIR': str(self.runtime)},
                               {'ffplay'}, pactl=False, pipewire=False)
        self.assertEqual(command, ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'error',
                                   '/tmp/opencode/reply.wav'])
        with self.assertRaisesRegex(backend.AudioBackendError, 'TUI_VOICE_PLAYER'):
            self.command({'XDG_RUNTIME_DIR': str(self.runtime)}, set(),
                         pactl=False, pipewire=False)


class ScratchDirectoryTests(unittest.TestCase):
    def setUp(self):
        isolate_config(self)

    def test_runtime_directory_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = backend.scratch_dir({'XDG_RUNTIME_DIR': directory})
            self.assertEqual(path, Path(directory) / 'tui-voice')
            self.assertTrue(path.is_dir())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_missing_runtime_directory_uses_the_temp_directory(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(backend.tempfile, 'gettempdir', return_value=directory):
            path = backend.scratch_dir({})
            self.assertEqual(path, Path(directory) / f'tui-voice-{os.getuid()}')
            self.assertTrue(path.is_dir())


if __name__ == '__main__':
    unittest.main()
