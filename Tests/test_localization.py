import ast
import json
import pathlib
import re
import unittest
from unittest.mock import Mock, patch
import bridge
import localization
import protocol


class LocalizationTests(unittest.TestCase):
    def test_every_helper_message_has_an_english_translation(self):
        resources = pathlib.Path(__file__).resolve().parents[1] / 'Resources'
        missing = []
        for path in resources.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search('[А-Яа-яЁё]', node.value):
                    if node.value not in localization.CATALOG: missing.append((path.name, node.value))
        self.assertEqual(missing, [])
        self.assertTrue(all(value and not re.search('[А-Яа-яЁё]', value) for value in localization.CATALOG.values()))

    def test_only_fixed_error_text_is_translated_and_user_names_are_preserved(self):
        name = 'Мой сервер'
        self.assertEqual(localization.message('Сервер используется: ' + name, 'en'), 'Server is used by: ' + name)
        reply = {'error': 'Системный помощник недоступен', 'configuration': {'title': 'Серверы'}}
        self.assertEqual(localization.reply(reply, 'ru'), reply)
        result = localization.reply(reply, 'en')
        self.assertEqual(result['error'], 'The system helper is unavailable')
        self.assertEqual(result['configuration'], reply['configuration'])

    def test_helper_errors_follow_the_request_language(self):
        connection = Mock();connection.recv.return_value = protocol.encode({'action': 'status', 'language': 'en'})
        with patch.object(bridge, 'peer_uid', return_value=501), patch.object(bridge, 'dispatch', side_effect=ValueError('PRIVATE-UUID')):
            bridge.handle(connection, 501)
        response = json.loads(connection.sendall.call_args[0][0])
        self.assertEqual(response['error'], 'The operation did not finish. Previous settings are retained.')
        self.assertNotIn('PRIVATE-UUID', str(response))

    def test_language_does_not_expand_helper_permissions(self):
        protocol.validate({'action': 'status', 'language': 'ru'})
        protocol.validate({'action': 'status', 'language': 'en'})
        with self.assertRaises(ValueError): protocol.validate({'action': 'status', 'language': 'fr'})
