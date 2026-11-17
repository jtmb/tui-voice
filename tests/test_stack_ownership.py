"""Ownership, layout, locking and platform rules of the speech-stack installer."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import setup_stack
from test_setup_stack import make_venv


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        env = patch.dict(os.environ, {'TUI_VOICE_LOG_DIR': str(self.base / 'log'),
                                      'XDG_DATA_HOME': str(self.base / 'data')})
        env.start()
        self.addCleanup(env.stop)


class LayoutTests(Base):
    """Folders may be named and placed anywhere."""

    def setUp(self):
        super().setUp()
        self.service = self.base / 'svc'
        self.voice = self.base / 'elsewhere' / 'models'

    def test_adapters_land_in_the_configured_folders(self):
        setup_stack.write_adapters(self.service, self.voice)
        self.assertTrue((self.service / 'web_voice.py').is_file())
        self.assertTrue((self.voice / 'asr-worker.py').is_file())
        self.assertTrue((self.voice / 'voices.py').is_file())
        self.assertFalse((self.base / 'service').exists())
        self.assertFalse((self.base / 'elsewhere' / 'voice').exists())

    def test_status_reads_the_same_folders(self):
        setup_stack.write_adapters(self.service, self.voice)
        with patch.object(setup_stack.voice_config, 'service', return_value=self.service), \
             patch.object(setup_stack.voice_config, 'voice', return_value=self.voice):
            report = setup_stack.status()
        self.assertTrue(all(row['present'] for row in report['adapters']))

    def test_explicit_interpreter_replaces_the_venv(self):
        python = self.base / 'custom-python'
        python.write_text('')
        with patch.object(setup_stack.voice_config, 'service', return_value=self.service), \
             patch.object(setup_stack.voice_config, 'voice', return_value=self.voice), \
             patch.object(setup_stack.voice_config, 'get',
                          side_effect=lambda key, environ=None:
                          str(python) if key == 'asr_python' else None):
            report = setup_stack.status()
        asr = [row for row in report['venvs'] if row['external']]
        self.assertEqual(len(asr), 1)
        self.assertTrue(asr[0]['ready'])
        self.assertEqual(asr[0]['path'], str(python))

    def test_external_interpreter_is_never_installed_into(self):
        with patch.object(setup_stack.voice_config, 'get',
                          side_effect=lambda key, environ=None: '/x/python'), \
             patch.object(setup_stack, 'create_venv') as create:
            installed = setup_stack.install_venvs(self.service, self.voice, owned=True)
        create.assert_not_called()
        self.assertTrue(all(row['action'] == 'external' for row in installed))


class OwnershipTests(Base):
    def setUp(self):
        super().setUp()
        self.service = self.base / 'svc'
        self.voice = self.base / 'voice'

    def venv(self, entry):
        return setup_stack.venv_dir(entry, self.service, self.voice)

    def test_empty_supported_venv_without_marker_is_not_ready_when_managed(self):
        entry = setup_stack.VENVS[0]
        make_venv(self.venv(entry))
        self.assertFalse(setup_stack.venv_ready(self.venv(entry), entry[3], True))
        self.assertTrue(setup_stack.venv_ready(self.venv(entry), entry[3], False))

    def test_marker_with_other_requirements_is_not_ready(self):
        entry = setup_stack.VENVS[0]
        make_venv(self.venv(entry), marker=('something-else==1',))
        self.assertFalse(setup_stack.venv_ready(self.venv(entry), entry[3], True))
        self.assertFalse(setup_stack.venv_ready(self.venv(entry), entry[3], False))

    def test_unsupported_python_is_not_ready(self):
        entry = setup_stack.VENVS[0]
        make_venv(self.venv(entry), marker=entry[3], version='3.14.4')
        self.assertFalse(setup_stack.venv_ready(self.venv(entry), entry[3], True))

    def test_user_venv_is_not_modified(self):
        for entry in setup_stack.VENVS:
            make_venv(self.venv(entry))
        with patch.object(setup_stack, '_venv_ok', return_value=True), \
             patch.object(setup_stack.shutil, 'rmtree') as remove, \
             patch.object(setup_stack.subprocess, 'run') as run:
            installed = setup_stack.install_venvs(self.service, self.voice, owned=False)
        remove.assert_not_called()
        run.assert_not_called()
        self.assertTrue(all(row['action'] == 'user-owned' for row in installed))

    def test_unusable_user_venv_is_reported_not_deleted(self):
        entry = setup_stack.VENVS[0]
        make_venv(self.venv(entry), version='3.14.4')
        with patch.object(setup_stack.shutil, 'rmtree') as remove:
            with self.assertRaises(setup_stack.SetupError) as raised:
                setup_stack.install_venvs(self.service, self.voice, owned=False)
        remove.assert_not_called()
        self.assertIn('--force-user-stack', str(raised.exception))

    def test_create_venv_refuses_to_replace_without_permission(self):
        target = self.base / 'existing'
        target.mkdir()
        (target / 'keep.txt').write_text('mine')
        with self.assertRaises(setup_stack.SetupError):
            setup_stack.create_venv(target)
        self.assertEqual((target / 'keep.txt').read_text(), 'mine')

    def test_pip_failure_leaves_no_marker_and_is_not_ready(self):
        entry = setup_stack.VENVS[2]
        directory = self.venv(entry)
        make_venv(directory, marker=entry[3])

        class Failed:
            returncode = 1
            stdout = ''
            stderr = 'no network'

        with patch.object(setup_stack, 'VENVS', (entry,)), \
             patch.object(setup_stack, 'create_venv'), \
             patch.object(setup_stack.subprocess, 'run', return_value=Failed()):
            with self.assertRaises(setup_stack.SetupError):
                setup_stack.install_venvs(self.service, self.voice, owned=True, force=True)
        self.assertFalse((directory / setup_stack.MARKER).exists())
        self.assertFalse(setup_stack.venv_ready(directory, entry[3], True))

    def test_successful_install_writes_a_matching_marker(self):
        entry = setup_stack.VENVS[2]
        directory = self.venv(entry)

        class Done:
            returncode = 0
            stdout = ''
            stderr = ''

        def create(path, replace=False):
            make_venv(path)

        with patch.object(setup_stack, 'VENVS', (entry,)), \
             patch.object(setup_stack, 'create_venv', side_effect=create), \
             patch.object(setup_stack.subprocess, 'run', return_value=Done()):
            installed = setup_stack.install_venvs(self.service, self.voice, owned=True)
        self.assertEqual(installed[0]['action'], 'installed')
        self.assertTrue(setup_stack.marker_matches(directory, entry[3]))

    def test_managed_means_inside_data_home_or_explicit_root(self):
        data = setup_stack.voice_config.data_home()
        self.assertTrue(setup_stack.managed(data / 'service', data / 'voice'))
        self.assertFalse(setup_stack.managed(self.base / 'a', data / 'voice'))
        self.assertTrue(setup_stack.managed(self.base / 'a', self.base / 'b', root='/x'))

    def test_existing_user_folders_keep_their_permissions(self):
        user = self.base / 'userstack'
        user.mkdir()
        os.chmod(user, 0o755)
        setup_stack._prepare_directories(user / 'service', user / 'voice', False, None)
        self.assertEqual(user.stat().st_mode & 0o777, 0o755)

    def test_new_managed_root_is_private(self):
        root = self.base / 'fresh'
        setup_stack._prepare_directories(root / 'service', root / 'voice', True, root)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_permission_error_becomes_a_setup_error(self):
        with patch.object(Path, 'mkdir', side_effect=PermissionError(13, 'denied', '/x')):
            with self.assertRaises(setup_stack.SetupError):
                setup_stack._prepare_directories(self.base / 's', self.base / 'v', False, None)


class InstallFlowTests(Base):
    def test_second_installer_sees_busy_and_touches_nothing(self):
        root = self.base / 'stack'
        with setup_stack.install_lock() as first:
            self.assertTrue(first)
            report = setup_stack.install(root=root)
        self.assertEqual(report['state'], 'busy')
        self.assertFalse(root.exists())

    def test_install_logs_start_and_finish(self):
        root = self.base / 'stack'
        with patch.object(setup_stack, 'model_entries', return_value=[]), \
             patch.object(setup_stack, 'ADAPTERS', ()), \
             patch.object(setup_stack, 'VENVS', ()):
            report = setup_stack.install(root=root)
        self.assertTrue(report['ok'])
        lines = (self.base / 'log/voice.log').read_text().splitlines()
        messages = [json.loads(line)['message'] for line in lines]
        self.assertIn('speech install started', messages)
        self.assertIn('speech install finished', messages)

    def test_failed_selftest_reports_an_error_in_text_mode(self):
        with patch.object(setup_stack, 'install', return_value={
                'ok': False, 'error': 'selftest failed: kokoro_onnx',
                'root': {}, 'selftest': [{'ok': False}], 'status': {'missing': []}}):
            err = io.StringIO()
            with patch('sys.stderr', err):
                code = setup_stack.main(['--install'])
        self.assertEqual(code, 1)
        self.assertIn('selftest failed', err.getvalue())

    def test_selftest_failure_sets_an_error(self):
        root = self.base / 'stack'
        rows = [{'venv': 'voice/.venv', 'import': 'kokoro_onnx', 'ok': False}]
        with patch.object(setup_stack, 'model_entries', return_value=[]), \
             patch.object(setup_stack, 'ADAPTERS', ()), patch.object(setup_stack, 'VENVS', ()), \
             patch.object(setup_stack, 'selftest', return_value=rows):
            report = setup_stack.install(root=root, run_selftest=True)
        self.assertFalse(report['ok'])
        self.assertIn('kokoro_onnx', report['error'])

    def test_setup_error_is_logged(self):
        with patch.object(setup_stack, 'install',
                          side_effect=setup_stack.SetupError('boom detail')):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(setup_stack.main(['--install', '--json']), 1)
        self.assertIn('boom detail', (self.base / 'log/voice.log').read_text())


class BootstrapUvTests(Base):
    def make_archive(self, destination):
        import io as _io
        import tarfile
        payload = b'#!/bin/sh\necho uv\n'
        with tarfile.open(destination, 'w:gz') as bundle:
            info = tarfile.TarInfo('uv-x86_64-unknown-linux-gnu/uv')
            info.size = len(payload)
            info.mode = 0o755
            bundle.addfile(info, _io.BytesIO(payload))

    def test_verified_archive_is_unpacked_to_an_executable(self):
        def fake_download(item, destination, timeout=60):
            self.make_archive(destination)
            self.assertEqual(len(item['sha256']), 64)
            self.assertTrue(item['url'].startswith('https://github.com/astral-sh/uv/'))

        with patch.object(setup_stack.platform, 'machine', return_value='x86_64'), \
             patch.object(setup_stack, 'download', side_effect=fake_download):
            path = setup_stack.bootstrap_uv()
        self.assertTrue(os.access(path, os.X_OK))
        self.assertIn(setup_stack.UV_VERSION, path)
        self.assertFalse(list(Path(path).parent.glob('*.tar.gz')))

    def test_existing_uv_is_not_downloaded_again(self):
        with patch.object(setup_stack.platform, 'machine', return_value='x86_64'), \
             patch.object(setup_stack, 'download') as fetch:
            target = setup_stack.voice_config.data_home() / 'tools' / \
                f'uv-{setup_stack.UV_VERSION}' / 'uv'
            target.parent.mkdir(parents=True)
            target.write_text('x')
            os.chmod(target, 0o755)
            self.assertEqual(setup_stack.bootstrap_uv(), str(target))
        fetch.assert_not_called()

    def test_unknown_architecture_has_no_pinned_build(self):
        with patch.object(setup_stack.platform, 'machine', return_value='riscv64'):
            with self.assertRaises(setup_stack.SetupError):
                setup_stack.bootstrap_uv()

    def test_create_venv_bootstraps_uv_when_no_python_fits(self):
        calls = []

        def fake_run(command, **_kwargs):
            calls.append(command[0])
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        target = self.base / 'v'
        with patch.object(setup_stack, 'compatible_python', return_value=None), \
             patch.object(setup_stack.shutil, 'which', return_value=None), \
             patch.object(setup_stack, 'bootstrap_uv', return_value='/data/uv') as boot, \
             patch.object(setup_stack, '_venv_ok', side_effect=[False, True]), \
             patch.object(setup_stack.subprocess, 'run', side_effect=fake_run):
            setup_stack.create_venv(target)
        boot.assert_called_once()
        self.assertEqual(calls, ['/data/uv'])


class PlatformTests(unittest.TestCase):
    def test_musl_is_unsupported(self):
        reason = setup_stack.platform_check(libc=('', ''), machine='x86_64',
                                            musl_loaders=['/lib/ld-musl-x86_64.so.1'])
        self.assertIn('musl', reason)

    def test_old_glibc_is_unsupported(self):
        self.assertIn('2.28', setup_stack.platform_check(
            libc=('glibc', '2.17'), machine='x86_64', musl_loaders=[]))

    def test_current_glibc_is_supported(self):
        self.assertIsNone(setup_stack.platform_check(
            libc=('glibc', '2.36'), machine='x86_64', musl_loaders=[]))

    def test_unsupported_architecture(self):
        self.assertIn('armv7l', setup_stack.platform_check(
            libc=('glibc', '2.36'), machine='armv7l', musl_loaders=[]))

    def test_install_refuses_before_touching_anything(self):
        with patch.object(setup_stack, 'platform_check', return_value='no wheels'), \
             tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'stack'
            with self.assertRaises(setup_stack.SetupError):
                setup_stack.install(root=target)
            self.assertFalse(target.exists())

    def test_status_reports_the_reason_and_sizes(self):
        with patch.object(setup_stack, 'platform_check', return_value='no wheels'), \
             tempfile.TemporaryDirectory() as root:
            report = setup_stack.status(root)
        self.assertFalse(report['supported'])
        self.assertEqual(report['reason'], 'no wheels')
        self.assertGreater(report['download_bytes'], 400_000_000)
        self.assertTrue(report['fix'][-1] == '--install')


if __name__ == '__main__':
    unittest.main()
