"""Static downloads use system trust without weakening HTTPS verification."""

from pathlib import Path
import ssl
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from map_capture import read_static_map


class StaticMapTlsTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.ids.return_value = []
        self.md5 = 'a' * 32

    def test_download_uses_verified_system_context(self):
        with patch('map_capture.urlopen') as download, \
             patch('map_capture.decode_map_file', return_value={'map': True}) as decode:
            download.return_value.__enter__.return_value.read.return_value = b'map'
            self.assertEqual(read_static_map(self.client, self.md5), {'map': True})
        context = download.call_args.kwargs['context']
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(download.call_args.kwargs['timeout'], 15)
        self.assertTrue(download.call_args.args[0].startswith('https://'))
        self.assertEqual(decode.call_args.args[0].read_bytes(), b'map')

    def test_certificate_failure_stops_without_insecure_retry(self):
        error = URLError(ssl.SSLCertVerificationError('untrusted CA'))
        with patch('map_capture.urlopen', side_effect=error) as download, \
             patch('map_capture.decode_map_file') as decode:
            with self.assertRaisesRegex(ValueError, '不会跳过证书校验'):
                read_static_map(self.client, self.md5)
        download.assert_called_once()
        decode.assert_not_called()

    def test_other_download_errors_keep_their_cause(self):
        error = URLError(TimeoutError('timeout'))
        with patch('map_capture.urlopen', side_effect=error):
            with self.assertRaises(URLError) as raised:
                read_static_map(self.client, self.md5)
        self.assertIs(raised.exception, error)

    def test_existing_capture_does_not_download(self):
        self.client.ids.return_value = [1]
        self.client.call.return_value = {'response': {
            'code': 200, 'body': {'encoding': 'utf8', 'text': 'map'}}}
        with patch('map_capture.urlopen') as download, \
             patch('map_capture.truststore.SSLContext') as context, \
             patch('map_capture.decode_map_file', return_value={}):
            read_static_map(self.client, self.md5)
        download.assert_not_called()
        context.assert_not_called()


if __name__ == '__main__':
    unittest.main()
