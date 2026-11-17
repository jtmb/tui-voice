"""OS detection and dependency install planning; no package manager is run."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import audio_backend
import platform_deps


DEBIAN = {'ID': 'debian', 'ID_LIKE': '', 'NAME': 'Debian GNU/Linux',
          'PRETTY_NAME': 'Debian GNU/Linux 12 (bookworm)', 'VERSION_ID': '12'}
UBUNTU = {'ID': 'ubuntu', 'ID_LIKE': 'debian', 'PRETTY_NAME': 'Ubuntu 24.04 LTS',
          'VERSION_ID': '24.04'}
FEDORA = {'ID': 'fedora', 'ID_LIKE': '', 'PRETTY_NAME': 'Fedora Linux 41',
          'VERSION_ID': '41'}
ARCH = {'ID': 'arch', 'ID_LIKE': '', 'PRETTY_NAME': 'Arch Linux'}
CACHY = {'ID': 'cachyos', 'ID_LIKE': 'arch', 'PRETTY_NAME': 'CachyOS'}
SUSE = {'ID': 'opensuse-tumbleweed', 'ID_LIKE': 'suse', 'PRETTY_NAME': 'openSUSE Tumbleweed'}
ALPINE = {'ID': 'alpine', 'ID_LIKE': '', 'PRETTY_NAME': 'Alpine Linux v3.20'}
RASPBERRY = {'ID': 'raspbian', 'ID_LIKE': 'debian', 'PRETTY_NAME': 'Raspbian 12'}


class DetectTests(unittest.TestCase):
    def test_debian_family(self):
        for data in (DEBIAN, UBUNTU, RASPBERRY):
            with self.subTest(data['ID']):
                self.assertEqual(platform_deps.detect(os_release=data,
                                                      platform=audio_backend.LINUX).family,
                                 'debian')

    def test_other_families(self):
        for data, family in ((FEDORA, 'fedora'), (ARCH, 'arch'), (CACHY, 'arch'),
                             (SUSE, 'suse'), (ALPINE, 'alpine')):
            with self.subTest(data['ID']):
                self.assertEqual(platform_deps.detect(os_release=data,
                                                      platform=audio_backend.LINUX).family,
                                 family)

    def test_wsl_wins_over_the_distribution(self):
        info = platform_deps.detect(os_release=UBUNTU, platform=audio_backend.WSL2)
        self.assertEqual(info.family, 'wsl2')

    def test_unknown_distribution_is_reported(self):
        info = platform_deps.detect(os_release={'ID': 'funtoo', 'PRETTY_NAME': 'Funtoo'},
                                    platform=audio_backend.LINUX)
        self.assertEqual(info.family, 'unknown')
        self.assertIn('Funtoo', info.name)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.sudo = patch.object(platform_deps, '_privilege_prefix', return_value=[])
        self.sudo.start()
        self.addCleanup(self.sudo.stop)

    def plan(self, keys, data=DEBIAN, platform=audio_backend.LINUX):
        return platform_deps.plan(keys, os_release=data, platform=platform)

    def test_debian_command_is_an_argv_list(self):
        planned = self.plan(['ffmpeg'])
        self.assertTrue(planned['ok'])
        self.assertEqual(planned['commands'],
                         [['apt-get', 'install', '-y', '--no-install-recommends', 'ffmpeg']])

    def test_optional_helpers_use_the_distribution_package_names(self):
        self.assertEqual(self.plan(['pactl'], ARCH)['commands'][0][-1], 'libpulse')
        self.assertEqual(self.plan(['arecord'], FEDORA)['commands'][0][-1], 'alsa-utils')
        self.assertEqual(self.plan(['ffmpeg', 'pactl', 'arecord'], DEBIAN)['commands'][0][-3:],
                         ['alsa-utils', 'ffmpeg', 'pulseaudio-utils'])

    def test_sudo_is_added_without_a_root_shell(self):
        patch.stopall()
        with patch.object(platform_deps.os, 'geteuid', return_value=1000), \
             patch.object(platform_deps.shutil, 'which', return_value='/usr/bin/sudo'), \
             patch.object(platform_deps.subprocess, 'run',
                          return_value=type('R', (), {'returncode': 0})()):
            planned = platform_deps.plan(['ffmpeg'], os_release=DEBIAN,
                                         platform=audio_backend.LINUX)
        self.assertTrue(planned['commands'][0][0].endswith('sudo'))
        self.assertEqual(planned['commands'][0][1:3], ['-n', 'apt-get'])

    def test_missing_privileges_return_a_manual_command(self):
        patch.stopall()
        with patch.object(platform_deps.os, 'geteuid', return_value=1000), \
             patch.object(platform_deps.shutil, 'which', return_value=None):
            planned = platform_deps.plan(['ffmpeg'], os_release=DEBIAN,
                                         platform=audio_backend.LINUX)
        self.assertFalse(planned['ok'])
        self.assertTrue(planned['manual'])
        self.assertEqual(planned['commands'][0][:3], ['sudo', 'apt-get', 'install'])

    def test_unknown_distribution_has_no_command(self):
        planned = self.plan(['ffmpeg'], {'ID': 'funtoo', 'PRETTY_NAME': 'Funtoo'})
        self.assertFalse(planned['ok'])
        self.assertIn('No package manager mapping', planned['error'])

    def test_wsl_uses_winget_for_the_windows_build(self):
        with patch.object(audio_backend, 'windows_cmd', return_value='cmd.exe'), \
             patch.object(audio_backend, '_run',
                          return_value=type('R', (), {'returncode': 0})()):
            planned = platform_deps.plan(['ffmpeg'], platform=audio_backend.WSL2)
        command = planned['commands'][0]
        self.assertEqual(command[:3], ['cmd.exe', '/c', 'winget'])
        self.assertIn('Gyan.FFmpeg', command)

    def test_wsl_without_winget_is_manual(self):
        with patch.object(audio_backend, 'windows_cmd', return_value='cmd.exe'), \
             patch.object(audio_backend, '_run',
                          return_value=type('R', (), {'returncode': 1})()):
            planned = platform_deps.plan(['ffmpeg'], platform=audio_backend.WSL2)
        self.assertFalse(planned['ok'])
        self.assertTrue(planned['manual'])
        self.assertIn('TUI_VOICE_FFMPEG', planned['error'])


class VenvTests(unittest.TestCase):
    def test_check_on_native_linux_runs_and_reports_venv(self):
        ok = type('R', (), {'returncode': 0})()
        with patch.object(audio_backend, '_run', return_value=ok), \
             patch.object(platform_deps.audio_backend, 'ffmpeg', return_value='ffmpeg'), \
             patch.object(platform_deps.shutil, 'which', return_value='/usr/bin/x'):
            report = platform_deps.check(os_release=DEBIAN, platform=audio_backend.LINUX)
        self.assertIn('venv', [row['key'] for row in report['requirements']])
        self.assertEqual(report['missing'], [])

    def test_missing_ensurepip_means_venv_is_absent(self):
        with patch.object(audio_backend, '_run', return_value=None):
            self.assertFalse(platform_deps.present('venv'))

    def test_present_probes_the_given_interpreter(self):
        seen = []

        def run(command, timeout=10, env=None):
            seen.append(command[0])
            return type('R', (), {'returncode': 0})()

        with patch.object(audio_backend, '_run', side_effect=run):
            self.assertTrue(platform_deps.present('venv', python='/opt/py3.12'))
        self.assertEqual(seen, ['/opt/py3.12'])

    def test_debian_asks_for_the_venv_package_of_that_interpreter(self):
        with patch.object(platform_deps, '_privilege_prefix', return_value=[]), \
             patch.object(platform_deps, 'interpreter_version', return_value=(3, 12)):
            planned = platform_deps.plan(['venv'], os_release=DEBIAN,
                                         platform=audio_backend.LINUX, python='/x/python3.12')
        self.assertEqual(planned['commands'][0][-1], 'python3.12-venv')

    def test_unprobeable_interpreter_falls_back_to_the_generic_package(self):
        with patch.object(platform_deps, '_privilege_prefix', return_value=[]), \
             patch.object(platform_deps, 'interpreter_version', return_value=None):
            planned = platform_deps.plan(['venv'], os_release=DEBIAN,
                                         platform=audio_backend.LINUX, python='/x/python')
        self.assertEqual(planned['commands'][0][-1], 'python3-venv')

    def test_other_families_ignore_the_interpreter(self):
        with patch.object(platform_deps, '_privilege_prefix', return_value=[]):
            planned = platform_deps.plan(['venv'], os_release=FEDORA,
                                         platform=audio_backend.LINUX, python='/x/python3.12')
        self.assertEqual(planned['commands'][0][-1], 'python3')


class RefreshTests(unittest.TestCase):
    def test_wsl2_retry_refreshes_the_linux_package_index(self):
        calls = []

        def run_commands(commands, environ=None, timeout=900):
            calls.append(commands[0])
            return [{'command': commands[0], 'ok': len(calls) > 2, 'code': 0, 'detail': ''}]

        planned = {'ok': True, 'commands': [['apt-get', 'install', 'python3-venv']]}
        with patch.object(platform_deps, 'plan', return_value=planned), \
             patch.object(platform_deps, '_run_commands', side_effect=run_commands), \
             patch.object(platform_deps, '_privilege_prefix', return_value=[]), \
             patch.object(platform_deps, 'detect',
                          return_value=platform_deps.OSInfo('wsl2', 'Ubuntu', '24.04', 'debian')), \
             patch.object(platform_deps, 'present', return_value=False):
            platform_deps._install_locked(['venv'])
        self.assertIn(['apt-get', 'update'], calls)


class CheckTests(unittest.TestCase):
    def check(self, data=DEBIAN, present=(), platform=audio_backend.LINUX, environ=None):
        with patch.object(platform_deps, 'present',
                          side_effect=lambda key, environ=None: key in present):
            return platform_deps.check(os_release=data, platform=platform,
                                       environ={} if environ is None else environ)

    def test_missing_required_dependency_is_listed(self):
        report = self.check(present=('pactl', 'arecord'))
        self.assertEqual(report['required_missing'], ['ffmpeg'])
        self.assertEqual(report['os']['family'], 'debian')
        self.assertEqual(report['manager'], 'apt-get')

    def test_wsl_only_checks_ffmpeg(self):
        report = self.check(present=(), platform=audio_backend.WSL2)
        self.assertEqual([row['key'] for row in report['requirements']], ['ffmpeg'])

    def test_auto_install_can_be_disabled(self):
        report = self.check(environ={platform_deps.AUTO_INSTALL_ENV: '0'})
        self.assertFalse(report['auto_install'])


class EnsureTests(unittest.TestCase):
    def test_nothing_missing_needs_no_install(self):
        with patch.object(platform_deps, 'check', return_value={
                'ok': True, 'missing': [], 'required_missing': [],
                'requirements': [], 'auto_install': True, 'os': {'family': 'debian'}}), \
             patch.object(platform_deps, 'install') as install:
            report = platform_deps.ensure()
        self.assertTrue(report['ok'])
        install.assert_not_called()

    def test_disabled_auto_install_reports_the_command(self):
        with patch.object(platform_deps, 'check', return_value={
                'ok': True, 'missing': ['ffmpeg'], 'required_missing': ['ffmpeg'],
                'requirements': [{'key': 'ffmpeg', 'required': True, 'present': False}],
                'auto_install': False, 'os': {'family': 'debian'}}), \
             patch.object(platform_deps, 'install') as install:
            report = platform_deps.ensure()
        self.assertFalse(report['ok'])
        self.assertIn('doctor --install', report['error'])
        install.assert_not_called()

    def test_install_runs_and_rechecks(self):
        state = {'done': False}

        def install(keys, **_kwargs):
            state['done'] = True
            return {'ok': True, 'installed': keys, 'error': None}

        def check(**kwargs):
            present = state['done']
            return {'ok': True, 'missing': [] if present else ['ffmpeg'],
                    'required_missing': [] if present else ['ffmpeg'],
                    'requirements': [{'key': 'ffmpeg', 'required': True,
                                      'present': present}],
                    'auto_install': True, 'os': {'family': 'debian'}}

        with patch.object(platform_deps, 'check', side_effect=check), \
             patch.object(platform_deps, 'install', side_effect=install):
            report = platform_deps.ensure()
        self.assertTrue(report['ok'])
        self.assertTrue(report['install_attempted'])
        self.assertEqual(report['install']['installed'], ['ffmpeg'])


if __name__ == '__main__':
    unittest.main()
