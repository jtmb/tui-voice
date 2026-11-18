"""Voice-model loading notifications and the idle-unload setting."""
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_helper_bugs import helper


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.log = patch.object(helper, 'log_event')
        self.log.start()
        self.addCleanup(self.log.stop)

    def loader(self):
        return helper.ModelLoader(self.events.append)

    def test_a_running_worker_is_used_without_any_notice(self):
        with patch.object(helper, 'asr_running', return_value=True), \
             patch.object(helper, 'start_worker') as start:
            self.assertTrue(self.loader().ensure())
        start.assert_not_called()
        self.assertEqual(self.events, [])

    def test_an_unloaded_model_reports_loading_then_ready(self):
        state = {'up': False}

        def start(kind):
            state['up'] = True
            return {'ok': True}

        with patch.object(helper, 'asr_running', side_effect=lambda: state['up']), \
             patch.object(helper, 'start_worker', side_effect=start):
            self.assertTrue(self.loader().ensure())
        self.assertEqual([(e['event'], e['state']) for e in self.events],
                         [('model', 'loading'), ('model', 'ready')])
        self.assertIn('seconds', self.events[1])

    def test_a_failed_load_is_reported_once_and_not_retried_at_once(self):
        loader = self.loader()
        with patch.object(helper, 'asr_running', return_value=False), \
             patch.object(helper, 'start_worker',
                          return_value={'ok': False, 'error': 'Missing asr worker script'}) as start:
            self.assertFalse(loader.ensure())
            self.assertFalse(loader.ensure())
            self.assertFalse(loader.ensure())
        self.assertEqual(start.call_count, 1)
        self.assertEqual([e['state'] for e in self.events], ['loading', 'failed'])
        self.assertIn('Missing asr worker script', self.events[1]['error'])

    def test_a_retry_is_allowed_after_the_pause(self):
        loader = self.loader()
        with patch.object(helper, 'asr_running', return_value=False), \
             patch.object(helper, 'start_worker', return_value={'ok': False}) as start:
            loader.ensure()
            loader.failed_at -= helper.ModelLoader.RETRY_AFTER + 1
            loader.ensure()
        self.assertEqual(start.call_count, 2)

    def test_an_exception_while_starting_does_not_escape(self):
        with patch.object(helper, 'asr_running', return_value=False), \
             patch.object(helper, 'start_worker', side_effect=OSError('no python')):
            self.assertFalse(self.loader().ensure())
        self.assertEqual(self.events[-1]['state'], 'failed')

    def test_concurrent_callers_load_once(self):
        state = {'up': False}

        def start(kind):
            time.sleep(0.2)
            state['up'] = True
            return {'ok': True}

        loader = self.loader()
        results = []
        with patch.object(helper, 'asr_running', side_effect=lambda: state['up']), \
             patch.object(helper, 'start_worker', side_effect=start) as starter:
            threads = [threading.Thread(target=lambda: results.append(loader.ensure()))
                       for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(results, [True] * 4)
        self.assertEqual(starter.call_count, 1)
        self.assertEqual([e['state'] for e in self.events], ['loading', 'ready'])


class SetIdleTests(unittest.TestCase):
    def requests(self, running, supports):
        sent = []

        def fake(path, request, timeout=180, quiet=False, required=False):
            sent.append((Path(path).name, request['operation']))
            if request['operation'] == 'status':
                return {'pid': 1} if running else None
            return {'idle_seconds': request['seconds']} if supports else None
        return sent, fake

    def test_running_workers_are_updated_in_place(self):
        sent, fake = self.requests(True, True)
        with patch.object(helper, 'worker_request', side_effect=fake), \
             patch.object(helper, 'stop_worker') as stop:
            result = helper.set_idle(300)
        self.assertTrue(result['ok'])
        self.assertEqual(result['applied'], {'asr': 'updated', 'tts': 'updated'})
        stop.assert_not_called()

    def test_workers_that_are_not_running_are_left_alone(self):
        sent, fake = self.requests(False, True)
        with patch.object(helper, 'worker_request', side_effect=fake):
            result = helper.set_idle(0)
        self.assertEqual(result['applied'], {'asr': 'not running', 'tts': 'not running'})
        self.assertFalse([item for item in sent if item[1] == 'set_idle'])

    def test_an_old_worker_is_stopped_so_it_restarts_with_the_new_limit(self):
        sent, fake = self.requests(True, False)
        with patch.object(helper, 'worker_request', side_effect=fake), \
             patch.object(helper, 'stop_worker', return_value={'ok': True}) as stop:
            result = helper.set_idle(600)
        self.assertEqual(stop.call_count, 2)
        self.assertEqual(result['applied']['asr'], 'restarts on next use')
        self.assertTrue(result['ok'])

    def test_a_worker_that_cannot_be_updated_is_an_error(self):
        sent, fake = self.requests(True, False)
        with patch.object(helper, 'worker_request', side_effect=fake), \
             patch.object(helper, 'stop_worker', return_value={'ok': False}):
            self.assertFalse(helper.set_idle(600)['ok'])

    def test_the_value_is_clamped(self):
        sent, fake = self.requests(False, True)
        with patch.object(helper, 'worker_request', side_effect=fake):
            self.assertEqual(helper.set_idle(-5)['idle_seconds'], 0)
            self.assertEqual(helper.set_idle(10 ** 9)['idle_seconds'], helper.MAX_IDLE_SECONDS)


class WorkerProtocolTests(unittest.TestCase):
    """The shipped ASR worker really changes its limit and still idles out."""

    def run_worker(self, idle, actions):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'stack'))
        import asr_worker
        directory = tempfile.mkdtemp(prefix='tv-asr-')
        path = Path(directory) / 's' / 'asr.sock'
        done = threading.Event()

        def serve():
            try:
                asr_worker.serve(path, object(), idle, 1)
            finally:
                done.set()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        for _ in range(100):
            if path.exists():
                break
            time.sleep(0.02)
        try:
            return actions(asr_worker, path, done)
        finally:
            try:
                asr_worker.admin('shutdown', path)
            except (OSError, ValueError, ConnectionError):
                pass
            done.wait(3)

    def test_invalid_values_are_rejected_and_the_worker_keeps_running(self):
        def actions(worker, path, done):
            for bad in (-1, 10 ** 9, 'x', True, 1.5):
                reply = self.send(worker, path, {'operation': 'set_idle', 'seconds': bad})
                self.assertFalse(reply['ok'], bad)
            self.assertEqual(worker.admin('status', path)['result']['idle_seconds'], 900)
            self.assertFalse(done.is_set())

        self.run_worker(900, actions)

    def test_set_idle_applies_and_zero_means_never(self):
        def actions(worker, path, done):
            reply = self.send(worker, path, {'operation': 'set_idle', 'seconds': 0})
            self.assertTrue(reply['ok'])
            self.assertEqual(worker.admin('status', path)['result']['idle_seconds'], 0)
            time.sleep(1.6)
            self.assertFalse(done.is_set())  # 0 never unloads

        self.run_worker(900, actions)

    def test_a_short_limit_unloads_the_worker(self):
        def actions(worker, path, done):
            self.assertTrue(self.send(worker, path, {'operation': 'set_idle', 'seconds': 1})['ok'])
            self.assertTrue(done.wait(6), 'worker should exit after the new idle limit')

        self.run_worker(900, actions)

    def test_polling_does_not_postpone_the_unload(self):
        def actions(worker, path, done):
            self.assertTrue(self.send(worker, path, {'operation': 'set_idle', 'seconds': 2})['ok'])
            began = time.monotonic()
            while not done.is_set() and time.monotonic() - began < 8:
                try:
                    worker.admin('status', path)
                except (OSError, ValueError, ConnectionError):
                    pass
                time.sleep(0.3)
            self.assertTrue(done.is_set(), 'a polled worker must still unload')
            self.assertLess(time.monotonic() - began, 5)

        self.run_worker(900, actions)

    @staticmethod
    def send(worker, path, request):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(path))
            worker.send_frame(connection, request)
            return worker.recv_frame(connection)


if __name__ == '__main__':
    unittest.main()
