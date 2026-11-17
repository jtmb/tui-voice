"""Stack installer checks: manifests, status, adapters and model copying."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import setup_stack


SMALL = b'model-bytes'
SMALL_SHA = hashlib.sha256(SMALL).hexdigest()


def entry(name='m.bin', directory='models', payload=SMALL):
    return {'file': name, 'dir': directory, 'bytes': len(payload),
            'url': 'https://example.invalid/' + name,
            'sha256': hashlib.sha256(payload).hexdigest()}


class ManifestTests(unittest.TestCase):
    def test_shipped_manifests_are_pinned(self):
        for item in setup_stack.model_entries():
            with self.subTest(item['file']):
                self.assertTrue(item['url'].startswith('https://'))
                self.assertEqual(len(item['sha256']), 64)
                int(item['sha256'], 16)
                self.assertGreater(int(item['bytes']), 0)
                self.assertTrue((setup_stack.STACK / 'voices.py').is_file())
                self.assertTrue((setup_stack.STACK / 'asr_worker.py').is_file())
                self.assertTrue((setup_stack.STACK / 'web_voice.py').is_file())

    def test_manifest_paths_stay_inside_the_voice_directory(self):
        for item in setup_stack.model_entries():
            self.assertFalse(item['dir'].startswith('/'))
            self.assertNotIn('..', Path(item['dir']).parts)


class PathTests(unittest.TestCase):
    def test_root_override_keeps_service_and_voice_together(self):
        service, voice = setup_stack.paths('/tmp/stack-root')
        self.assertEqual(service, Path('/tmp/stack-root/service'))
        self.assertEqual(voice, Path('/tmp/stack-root/voice'))


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.service, self.voice = setup_stack.paths(self.root)

    def test_missing_stack_lists_every_item(self):
        report = setup_stack.status(self.root)
        self.assertFalse(report['ready'])
        self.assertTrue(report['missing'])
        self.assertTrue(all(row['present'] is False for row in report['adapters']))

    def test_ready_stack_with_small_manifest(self):
        payload = entry()
        model = self.voice / 'models' / 'm.bin'
        model.parent.mkdir(parents=True)
        model.write_bytes(SMALL)
        with patch.object(setup_stack, 'model_entries', return_value=[payload]), \
             patch.object(setup_stack, 'ADAPTERS', ()), \
             patch.object(setup_stack, 'VENVS', ()):
            report = setup_stack.status(self.root)
        self.assertTrue(report['ready'])
        self.assertEqual(report['missing'], [])

    def test_verify_hashes_models(self):
        payload = entry()
        model = self.voice / 'models' / 'm.bin'
        model.parent.mkdir(parents=True)
        model.write_bytes(b'wrong-size')  # size differs, hash never consulted
        with patch.object(setup_stack, 'model_entries', return_value=[payload]), \
             patch.object(setup_stack, 'ADAPTERS', ()), \
             patch.object(setup_stack, 'VENVS', ()):
            report = setup_stack.status(self.root, verify=True)
        self.assertFalse(report['ready'])
        model.write_bytes(SMALL)
        with patch.object(setup_stack, 'model_entries', return_value=[payload]), \
             patch.object(setup_stack, 'ADAPTERS', ()), \
             patch.object(setup_stack, 'VENVS', ()):
            report = setup_stack.status(self.root, verify=True)
        self.assertTrue(report['ready'])
        self.assertTrue(report['models'][0]['hash_ok'])


def make_venv(directory, marker=None, version='3.12.1'):
    (directory / 'bin').mkdir(parents=True)
    (directory / 'bin/python').write_text('')
    (directory / 'pyvenv.cfg').write_text(f'home = /usr/bin\nversion = {version}\n')
    if marker is not None:
        (directory / setup_stack.MARKER).write_text(
            json.dumps({'requirements': list(marker)}))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.service, self.voice = setup_stack.paths(self.root)

    def test_adapters_are_written_once_and_kept(self):
        written = setup_stack.write_adapters(self.service, self.voice)
        self.assertTrue(all(row['action'] == 'written' for row in written))
        voice_module = self.voice / 'voices.py'
        self.assertIn('af_heart', voice_module.read_text())
        worker = self.voice / 'asr-worker.py'
        self.assertTrue(worker.is_file())
        voice_module.write_text('# user edit\n')
        kept = setup_stack.write_adapters(self.service, self.voice)
        self.assertTrue(all(row['action'] == 'kept' for row in kept))
        self.assertEqual(voice_module.read_text(), '# user edit\n')
        setup_stack.write_adapters(self.service, self.voice, force=True)
        self.assertIn('af_heart', voice_module.read_text())

    def test_venvs_with_a_matching_marker_are_skipped(self):
        for entry in setup_stack.VENVS:
            make_venv(setup_stack.venv_dir(entry, self.service, self.voice),
                      marker=entry[3])
        with patch.object(setup_stack, '_venv_ok', return_value=True):
            installed = setup_stack.install_venvs(self.service, self.voice, owned=True)
        self.assertTrue(all(row['action'] == 'kept' for row in installed))


class ModelInstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.voice = self.base / 'voice'
        self.source = self.base / 'models-from'
        self.source.mkdir()

    def test_copy_from_local_models_and_verify(self):
        payload = entry()
        (self.source / 'm.bin').write_bytes(SMALL)
        with patch.object(setup_stack, 'model_entries', return_value=[payload]):
            installed = setup_stack.install_models(self.voice, models_from=self.source)
        self.assertEqual(installed[0]['action'], 'copied from ' + str(self.source / 'm.bin'))
        self.assertEqual((self.voice / 'models/m.bin').read_bytes(), SMALL)

    def test_wrong_local_model_is_rejected_and_never_left_behind(self):
        payload = entry()
        (self.source / 'm.bin').write_bytes(b'other-bytes')
        with patch.object(setup_stack, 'model_entries', return_value=[payload]), \
             patch.object(setup_stack, 'download',
                          side_effect=setup_stack.SetupError('offline')):
            with self.assertRaises(setup_stack.SetupError):
                setup_stack.install_models(self.voice, models_from=self.source)
        self.assertFalse((self.voice / 'models/m.bin').exists())
        self.assertFalse((self.voice / 'models/m.bin.part').exists())

    def test_bad_local_copy_falls_back_to_the_download(self):
        payload = entry()
        (self.source / 'm.bin').write_bytes(b'other-bytes')

        def fake_download(item, destination, timeout=60):
            destination.write_bytes(SMALL)

        with patch.object(setup_stack, 'model_entries', return_value=[payload]), \
             patch.object(setup_stack, 'download', side_effect=fake_download):
            installed = setup_stack.install_models(self.voice, models_from=self.source)
        self.assertIn('downloaded', installed[0]['action'])
        self.assertIn('failed SHA-256', installed[0]['action'])

    def test_a_later_good_candidate_is_used(self):
        payload = entry(directory='sub')
        (self.source / 'm.bin').write_bytes(b'other-bytes')
        (self.source / 'sub').mkdir()
        (self.source / 'sub/m.bin').write_bytes(SMALL)
        with patch.object(setup_stack, 'model_entries', return_value=[payload]):
            installed = setup_stack.install_models(self.voice, models_from=self.source)
        self.assertTrue(installed[0]['action'].startswith('copied from'))
        self.assertEqual((self.voice / 'sub/m.bin').read_bytes(), SMALL)

    def test_truncated_download_is_a_setup_error(self):
        payload = entry()
        with patch.object(setup_stack.urllib.request, 'urlopen',
                          side_effect=setup_stack.http.client.IncompleteRead(b'x')):
            with self.assertRaises(setup_stack.SetupError):
                setup_stack.download(payload, self.voice / 'models/m.bin')

    def test_matching_model_is_kept_without_copying(self):
        payload = entry()
        (self.voice / 'models').mkdir(parents=True)
        (self.voice / 'models/m.bin').write_bytes(SMALL)
        with patch.object(setup_stack, 'model_entries', return_value=[payload]):
            installed = setup_stack.install_models(self.voice)
        self.assertEqual(installed[0]['action'], 'kept')

    def test_download_verifies_hash_and_replaces_atomically(self):
        payload = entry()
        destination = self.voice / 'models/m.bin'

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with redirect_stderr(io.StringIO()), \
             patch.object(setup_stack.urllib.request, 'urlopen', return_value=Response(SMALL)):
            setup_stack.download(payload, destination)
        self.assertEqual(destination.read_bytes(), SMALL)
        self.assertFalse((destination.parent / 'm.bin.part').exists())

    def test_download_hash_mismatch_leaves_no_file(self):
        payload = entry()
        destination = self.voice / 'models/m.bin'

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with redirect_stderr(io.StringIO()), \
             patch.object(setup_stack.urllib.request, 'urlopen',
                          return_value=Response(b'corrupt')):
            with self.assertRaises(setup_stack.SetupError):
                setup_stack.download(payload, destination)
        self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
