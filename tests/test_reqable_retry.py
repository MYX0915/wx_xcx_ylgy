"""Bounded read retries must never repeat input or bypass freshness checks."""

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from map_capture import Reqable
from play_game import execute


class ReqableRetryTests(unittest.TestCase):
    def setUp(self):
        self.client = Reqable.__new__(Reqable)
        self.client.read_retries = 0
        self.client.close = Mock()
        self.client._connect = Mock()
        self.client.rpc = Mock()
        self.sleep = patch('map_capture.time.sleep').start()
        patch('map_capture.sys.stderr').start()
        self.addCleanup(patch.stopall)

    def read(self):
        return self.client.call('capture_live_get_by_id', {'id': 1})

    def test_timeout_reconnects_and_repeats_only_the_read(self):
        self.client.rpc.side_effect = [TimeoutError(), {'structuredContent': {'id': 1}}]
        self.assertEqual(self.read(), {'id': 1})
        self.assertEqual(self.client.rpc.call_count, 2)
        self.client.close.assert_called_once()
        self.client._connect.assert_called_once()
        self.sleep.assert_called_once_with(1)
        self.assertEqual(self.client.read_retries, 1)

    def test_failures_are_bounded(self):
        self.client.rpc.side_effect = TimeoutError()
        with self.assertRaisesRegex(RuntimeError, '已尝试 3 次'):
            self.read()
        self.assertEqual(self.client.rpc.call_count, 3)
        self.assertEqual(self.client._connect.call_count, 2)
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2)])

    def test_reconnection_failure_uses_attempt_budget(self):
        self.client.rpc.side_effect = TimeoutError()
        self.client._connect.side_effect = ConnectionError()
        with self.assertRaisesRegex(RuntimeError, '已尝试 3 次'):
            self.read()
        self.assertEqual(self.client.rpc.call_count, 1)
        self.assertEqual(self.client._connect.call_count, 2)

    def test_empty_or_malformed_mcp_response_retries(self):
        for text in ('', 'not JSON'):
            with self.subTest(text=text):
                self.client.rpc.side_effect = [
                    {'content': [{'type': 'text', 'text': text}]},
                    {'structuredContent': {'id': 1}},
                ]
                self.assertEqual(self.read(), {'id': 1})

    def test_local_http_disconnect_retries(self):
        self.client.rpc.side_effect = [
            {'isError': True, 'content': [{'type': 'text', 'text':
                'HttpException: Connection closed before full header was received'}]},
            {'structuredContent': {'id': 1}},
        ]
        self.assertEqual(self.read(), {'id': 1})
        self.client._connect.assert_called_once()

    def test_non_read_tools_are_never_retried(self):
        self.client.rpc.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            self.client.call('non_read_tool', {})
        self.client.rpc.assert_called_once()
        self.client._connect.assert_not_called()

    def test_semantic_failure_and_interrupt_are_not_retried(self):
        for failure in (RuntimeError('record missing'), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                self.client.rpc.side_effect = failure
                with self.assertRaises(type(failure)):
                    self.read()
        self.client._connect.assert_not_called()

    def test_tool_error_without_transport_failure_is_not_retried(self):
        self.client.rpc.return_value = {'isError': True, 'content': [
            {'type': 'text', 'text': 'record missing'}]}
        with self.assertRaisesRegex(RuntimeError, 'Reqable tool failed'):
            self.read()
        self.client._connect.assert_not_called()

    def test_retry_during_validation_requires_fresh_full_check(self):
        def check(source):
            if self.client.read_retries == 0:
                self.client.read_retries += 1
            else:
                raise ValueError('A different game has started')
        self.client._check_current_game = Mock(side_effect=check)
        with self.assertRaisesRegex(ValueError, 'different game'):
            self.client.ensure_current_game({'recordUid': 'old'})
        self.assertEqual(self.client._check_current_game.call_count, 2)

    def test_continuous_reconnections_cannot_validate_forever(self):
        def check(source):
            self.client.read_retries += 1
        self.client._check_current_game = Mock(side_effect=check)
        with self.assertRaisesRegex(RuntimeError, '无法稳定核对'):
            self.client.ensure_current_game({})
        self.assertEqual(self.client._check_current_game.call_count, 3)

    def test_connect_discards_old_partial_response(self):
        self.client.buffer = b'{"id":999'
        self.client.sequence = 999
        with patch('map_capture.subprocess.Popen') as popen:
            Reqable._connect(self.client)
        self.assertEqual(self.client.buffer, b'')
        self.assertEqual(self.client.sequence, 0)
        popen.return_value.stdin.write.assert_called_once()
        self.assertEqual(self.client.rpc.call_args.args[0], 'initialize')


class ClickAfterReadTests(unittest.TestCase):
    def test_recovered_read_sends_only_next_saved_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            window = {'id': 1, 'pid': 10, 'title': '羊了个羊',
                      'bounds': {'X': 0, 'Y': 0, 'Width': 350, 'Height': 665}}
            client = Reqable.__new__(Reqable)
            client.read_retries = 0
            client.close = Mock()
            client._connect = Mock()
            client._call_once = Mock(side_effect=[TimeoutError(), {}])
            client.ensure_current_game = lambda source: client.call('capture_live_filter', {})
            cards = [SimpleNamespace(x=x, y=0, type=1, name='test') for x in (10, 20)]
            progress = {'nextStep': 1, 'inFlight': False}
            model = {'xScale': 1, 'yScale': 1, 'xOffset': 0, 'yOffset': 0}
            def native(command, *args):
                return {'windows': [window]} if command == 'inspect' else {'x': 0, 'y': 0}
            with patch('play_game.native', side_effect=native) as helper, \
                 patch('play_game.check_stop'), patch('play_game.time.sleep'), \
                 patch('builtins.print'):
                execute(client, window, Path(tmp), {'_source': {}}, cards,
                        {'order': [0, 1], 'steps': [{}, {'trayAfter': []}]},
                        progress, model, SimpleNamespace(max_clicks=1, delay=0.3))
            clicks = [c for c in helper.call_args_list if c.args[0] == 'click']
            self.assertEqual(clicks, [call('click', 1, 20, 0)])
            self.assertEqual(progress['nextStep'], 2)
            self.assertFalse(progress['inFlight'])
            self.assertEqual(client._call_once.call_count, 2)

    def test_read_failure_or_input_changes_prevent_next_click(self):
        for failure in ('read', 'mouse', 'window'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                window = {'id': 1, 'pid': 10, 'title': '羊了个羊',
                          'bounds': {'X': 0, 'Y': 0, 'Width': 350, 'Height': 665}}
                changed = dict(window, pid=11)
                inspected = iter([window, changed if failure == 'window' else window])
                def native(command, *args):
                    if command == 'inspect':
                        return {'windows': [next(inspected)]}
                    return {'x': 0, 'y': 0}
                client = Mock()
                if failure == 'read':
                    client.ensure_current_game.side_effect = RuntimeError('read exhausted')
                progress = {'nextStep': 1, 'inFlight': False}
                (folder / 'progress.json').write_text(json.dumps(progress))
                stop = [None, InterruptedError('Mouse moved')] if failure == 'mouse' else None
                with patch('play_game.native', side_effect=native) as helper, \
                     patch('play_game.time.sleep'), \
                     patch('play_game.check_stop', side_effect=stop), \
                     patch('builtins.print'):
                    with self.assertRaises((RuntimeError, ValueError, InterruptedError)):
                        execute(client, window, folder, {'_source': {}}, [None, None],
                                {'order': [0, 1]}, progress, {}, SimpleNamespace(max_clicks=1))
                self.assertFalse(any(c.args[0] == 'click' for c in helper.call_args_list))
                self.assertEqual(progress, {'nextStep': 1, 'inFlight': False})
                self.assertEqual(json.loads((folder / 'progress.json').read_text()), progress)


if __name__ == '__main__':
    unittest.main()
