import base64
import copy
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import time
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch
from fixtures import config, group
from configuration import ConfigurationError, VERSION, normalize, public, rule_set
from store import candidate
import bridge
import engine
import profiles
import protocol
import rulesets

BINARY = pathlib.Path(__file__).resolve().parents[1] / 'work/runtime-v9/sing-box'
DIGEST = 'a' * 64


def legacy(snapshots):
    value = config(); value['schemaVersion'] = 9
    value['groups'][0].pop('ruleSets')
    value['groups'][0]['snapshots'] = snapshots
    return value


def item(kind='geosite', name='category-example', digest=DIGEST, **extra):
    return {'kind': kind, 'name': name, 'digest': digest, **extra}


class AssetCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.base = pathlib.Path(folder.name)
        self.assets = self.base / 'assets'; self.assets.mkdir()
        for name, value in (('BASE', self.base), ('ASSETS', self.assets)):
            patcher = patch.object(engine, name, value); patcher.start(); self.addCleanup(patcher.stop)

    def asset(self, digest=DIGEST, age=0):
        path = self.assets / (digest + '.srs'); path.write_bytes(b'SRS synthetic')
        if age: os.utime(path, (time.time() - age, time.time() - age))
        return path


class MigrationTests(unittest.TestCase):
    def test_v9_snapshots_become_named_custom_sets_and_keep_their_route(self):
        result = normalize(legacy([DIGEST, 'b' * 64, DIGEST]))
        self.assertEqual(result['schemaVersion'], VERSION)
        self.assertNotIn('snapshots', result['groups'][0])
        self.assertEqual(result['groups'][0]['ruleSets'], [item('custom', DIGEST[:12]), item('custom', 'b' * 12, 'b' * 64)])
        rules = engine.build_runtime(result, '/synthetic')['route']['rules']
        self.assertEqual(next(r for r in rules if 'rule_set' in r)['rule_set'], [DIGEST, 'b' * 64])

    def test_migration_is_idempotent_and_does_not_mutate_the_source(self):
        source = legacy([DIGEST]); original = copy.deepcopy(source)
        once = normalize(source)
        self.assertEqual(source, original)
        self.assertEqual(normalize(once), once)

    def test_invalid_v9_references_and_unknown_versions_are_rejected(self):
        for value in (['../../etc/passwd'], 'not-a-list', [7]):
            with self.assertRaises(ConfigurationError): normalize(legacy(value))
        future = config(); future['schemaVersion'] = VERSION + 1
        with self.assertRaises(ConfigurationError): normalize(future)

    def test_snapshots_field_is_not_accepted_in_the_current_schema(self):
        value = config(); value['groups'][0]['snapshots'] = [DIGEST]
        with self.assertRaises(ConfigurationError): normalize(value)

    def test_v9_profile_imports_with_its_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder); blob = b'SRS synthetic'; digest = hashlib.sha256(blob).hexdigest()
            (root / (digest + '.srs')).write_bytes(blob)
            encoded = profiles.pack(legacy([digest]), root)
            imported, blobs, _ = profiles.unpack(encoded, 3)
            self.assertEqual(imported['groups'][0]['ruleSets'], [item('custom', digest[:12], digest)])
            self.assertEqual(blobs, {digest: blob})


class SchemaTests(unittest.TestCase):
    def test_rule_set_fields_are_validated_by_kind(self):
        self.assertEqual(rule_set(item('geoip', ' DE '))['name'], 'de')
        self.assertEqual(rule_set(item('geosite', 'geolocation-!cn'))['name'], 'geolocation-!cn')
        self.assertEqual(rule_set(item('custom', 'Мои правила', url='https://example.invalid/a.srs'))['url'], 'https://example.invalid/a.srs')
        for bad in (item('geoip', '../x'), item('geosite', 'a/b'), item('geoip', 'de', url='https://example.invalid/'), item('other'),
                    item(digest='../../etc/passwd'), item('custom', 'x', url='http://example.invalid/'),
                    item('custom', 'x', url='https://user:secret@example.invalid/'), item('custom', 'x', url='https://exa mple.invalid/'),
                    item('custom', ''), dict(item(), path='/etc/passwd')):
            with self.assertRaises(ConfigurationError, msg=bad): rule_set(bad)

    def test_matchers_alone_satisfy_a_route_and_are_hidden_from_status(self):
        value = config(); value['groups'][0].update(domains=[], keywords=['tracker'], regexes=['^a.*b$'], ruleSets=[item()])
        result = normalize(value)
        for field in ('keywords', 'regexes', 'ruleSets'):
            self.assertNotIn(field, public(result)['groups'][0])
            self.assertTrue(public(result, True)['groups'][0][field])

    def test_runtime_matches_all_kinds_but_keeps_geoip_out_of_dns_rules(self):
        value = config(); value['groups'][0].update(domains=[], keywords=['tracker'], regexes=['^a.*b$'], cidrs=['198.51.100.0/24'],
                                                    ruleSets=[item(), item('geoip', 'de', 'b' * 64), item('custom', 'Mine', 'c' * 64)])
        runtime = engine.build_runtime(normalize(value), '/synthetic')
        routes = runtime['route']['rules']
        self.assertEqual(next(r for r in routes if 'domain_keyword' in r)['domain_keyword'], ['tracker'])
        self.assertEqual(next(r for r in routes if 'domain_regex' in r)['domain_regex'], ['^a.*b$'])
        self.assertEqual(next(r for r in routes if 'rule_set' in r)['rule_set'], [DIGEST, 'b' * 64, 'c' * 64])
        self.assertEqual(next(r for r in runtime['dns']['rules'] if 'rule_set' in r)['rule_set'], [DIGEST, 'c' * 64])
        self.assertEqual({r['tag'] for r in runtime['route']['rule_set']}, {DIGEST, 'b' * 64, 'c' * 64})
        self.assertTrue(all(r['path'].startswith('/synthetic/') for r in runtime['route']['rule_set']))

    def test_fallback_still_has_no_matchers(self):
        value = config(); value['fallback']['ruleSets'] = [item()]
        with self.assertRaises(ConfigurationError): normalize(value)


class CandidateTests(AssetCase):
    def saved(self, base, **fields):
        raw = dict(group(), **fields)
        return candidate(base, {'action': 'save-group', 'revision': base['revision'], 'group': raw})

    def test_new_reference_requires_a_stored_asset(self):
        base = normalize(config())
        with self.assertRaises(ConfigurationError): self.saved(base, ruleSets=[item()])
        self.asset()
        self.assertEqual(self.saved(base, ruleSets=[item()])['groups'][0]['ruleSets'], [item()])

    def test_symlinks_are_not_stored_assets(self):
        (self.assets / (DIGEST + '.srs')).symlink_to('/etc/hosts')
        with self.assertRaises(ConfigurationError): self.saved(normalize(config()), ruleSets=[item()])

    def test_migrated_set_can_be_renamed_and_removed_without_its_file(self):
        base = normalize(legacy([DIGEST]))
        renamed = self.saved(base, ruleSets=[item('custom', 'Мои правила')])
        self.assertEqual(renamed['groups'][0]['ruleSets'][0]['name'], 'Мои правила')
        self.assertEqual(self.saved(base, ruleSets=[])['groups'][0]['ruleSets'], [])

    def test_update_changes_only_matching_updatable_sets(self):
        for digest in (DIGEST, 'b' * 64, 'c' * 64): self.asset(digest)
        url = 'https://example.invalid/a.srs'
        value = config(); value['groups'][0]['ruleSets'] = [item(), item('custom', 'Linked', 'b' * 64, url=url), item('custom', 'Local', 'c' * 64)]
        base = normalize(value)
        new = 'd' * 64; self.asset(new)
        result = candidate(base, {'action': 'update-rule-sets', 'revision': 0, 'updates': [item(digest=new)]})
        self.assertEqual([r['digest'] for r in result['groups'][0]['ruleSets']], [new, 'b' * 64, 'c' * 64])
        with self.assertRaises(ConfigurationError):
            candidate(base, {'action': 'update-rule-sets', 'revision': 0, 'updates': [item('custom', 'Local', new)]})
        with self.assertRaises(ConfigurationError):
            candidate(base, {'action': 'update-rule-sets', 'revision': 0, 'updates': [item(digest='e' * 64)]})
        with self.assertRaises(ConfigurationError):
            candidate(base, {'action': 'update-rule-sets', 'revision': 9, 'updates': []})


class SourceTests(unittest.TestCase):
    def test_geo_names_map_to_fixed_sources_with_quoting(self):
        self.assertEqual(rulesets.source_url(item('geoip', 'de')), 'https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-de.srs')
        self.assertEqual(rulesets.source_url(item('geosite', 'geolocation-!cn')), 'https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-geolocation-!cn.srs')

    def test_redirects_may_not_leave_https(self):
        handler = rulesets.HTTPSOnly()
        request = SimpleNamespace(full_url='https://example.invalid/', get_method=lambda: 'GET', headers={}, unredirected_hdrs={}, origin_req_host='example.invalid')
        with self.assertRaises(ConfigurationError): handler.redirect_request(request, None, 302, 'Found', {}, 'http://example.invalid/')

    def test_download_is_bounded_and_reports_fixed_errors(self):
        class Response:
            def __init__(self, data): self.chunks = [data, b'']
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _): return self.chunks.pop(0)
        with patch('urllib.request.OpenerDirector.open', return_value=Response(b'SRS')):
            self.assertEqual(rulesets.download('https://example.invalid/'), b'SRS')
        with patch('urllib.request.OpenerDirector.open', return_value=Response(b'x' * (rulesets.MAX_ASSET + 1))):
            with self.assertRaisesRegex(ConfigurationError, 'слишком большой'): rulesets.download('https://example.invalid/')
        with patch('urllib.request.OpenerDirector.open', side_effect=urllib.error.HTTPError('https://example.invalid/', 404, 'x', {}, None)):
            with self.assertRaisesRegex(ConfigurationError, 'не найден'): rulesets.download('https://example.invalid/')
        with patch('urllib.request.OpenerDirector.open', side_effect=OSError('PRIVATE-ADDRESS')):
            with self.assertRaises(ConfigurationError) as caught: rulesets.download('https://example.invalid/')
            self.assertNotIn('PRIVATE-ADDRESS', str(caught.exception))


class BridgeFlowTests(unittest.TestCase):
    def test_prepare_downloads_as_the_user_and_sends_only_bytes_to_the_helper(self):
        sent = []
        def client(request): sent.append(request); return {'ok': True, 'digest': DIGEST}
        with patch.object(rulesets, 'download', return_value=b'SRS') as download:
            reply = rulesets.prepare({'kind': 'geoip', 'name': 'DE'}, client)
        download.assert_called_once_with('https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-de.srs')
        self.assertEqual(sent, [{'action': 'store-rule-set', 'data': base64.b64encode(b'SRS').decode()}])
        self.assertEqual(reply, {'ok': True, 'ruleSet': {'kind': 'geoip', 'name': 'de', 'digest': DIGEST}})
        protocol.validate(sent[0])

    def test_prepare_accepts_local_data_only_for_custom_sets(self):
        client = lambda request: {'ok': True, 'digest': DIGEST}
        with patch.object(rulesets, 'download', side_effect=AssertionError('no network')):
            self.assertTrue(rulesets.prepare({'kind': 'custom', 'name': 'Mine', 'data': 'U1JT'}, client)['ok'])
            for source in ({'kind': 'geoip', 'name': 'de', 'data': 'U1JT'}, {'kind': 'custom', 'name': 'Mine'},
                           {'kind': 'custom', 'name': 'Mine', 'data': 'U1JT', 'url': 'https://example.invalid/'},
                           {'kind': 'custom', 'name': 'Mine', 'data': 7}, {'kind': 'custom', 'name': 'Mine', 'path': '/etc/hosts'}, 'x'):
                self.assertFalse(rulesets.prepare(source, client)['ok'], source)

    def test_prepare_returns_fixed_errors_instead_of_raising(self):
        with patch.object(rulesets, 'download', side_effect=ConfigurationError('Набор правил не найден')):
            self.assertEqual(rulesets.prepare({'kind': 'geosite', 'name': 'x'}, None), {'ok': False, 'error': 'Набор правил не найден'})
        self.assertEqual(rulesets.prepare({'kind': 'custom', 'name': 'x', 'data': 'U1JT'}, lambda _: {'ok': False, 'error': 'stop'}), {'ok': False, 'error': 'stop'})

    def refresh(self, sets, download, stored=None):
        calls = []
        configuration = {'revision': 7, 'groups': [{'ruleSets': sets}, {'ruleSets': sets}]}
        def client(request):
            calls.append(request)
            if request['action'] == 'configuration': return {'ok': True, 'configuration': configuration}
            if request['action'] == 'store-rule-set': return {'ok': True, 'digest': stored or 'b' * 64}
            return {'ok': True, 'configuration': {'revision': 8, 'groups': []}}
        with patch.object(rulesets, 'download', side_effect=download):
            return rulesets.refresh(client), calls

    def test_refresh_skips_local_sets_and_downloads_each_source_once(self):
        sets = [item(), item('custom', 'Local', 'c' * 64)]
        fetched = []
        reply, calls = self.refresh(sets, lambda url: fetched.append(url) or b'SRS')
        self.assertTrue(reply['ok'])
        self.assertEqual(len(fetched), 1)
        update = next(c for c in calls if c['action'] == 'update-rule-sets')
        self.assertEqual(update['revision'], 7)
        self.assertEqual(update['updates'], [item(digest='b' * 64)])
        protocol.validate(update)

    def test_refresh_keeps_failed_sets_and_names_them(self):
        def download(url):
            if 'broken' in url: raise ConfigurationError('Набор правил не найден')
            return b'SRS'
        reply, calls = self.refresh([item(), item(name='broken', digest='c' * 64)], download)
        self.assertFalse(reply['ok'])
        self.assertEqual(reply['error'], 'Не удалось обновить наборы: broken')
        self.assertEqual(reply['configuration']['revision'], 8)
        self.assertEqual([u['name'] for u in next(c for c in calls if c['action'] == 'update-rule-sets')['updates']], ['category-example'])

    def test_refresh_without_updatable_sets_touches_nothing(self):
        reply, calls = self.refresh([item('custom', 'Local')], lambda url: self.fail(url))
        self.assertTrue(reply['ok'])
        self.assertEqual([c['action'] for c in calls], ['configuration'])

    def test_helper_refuses_network_actions(self):
        for request in ({'action': 'prepare-rule-set', 'source': {}}, {'action': 'refresh-rule-sets'}):
            with self.assertRaises(ConfigurationError): bridge.dispatch(request)

    def test_unchanged_update_does_not_restart_the_engine(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(engine, 'BASE', pathlib.Path(folder)), \
                patch.object(engine, 'ASSETS', pathlib.Path(folder)), patch.object(bridge, 'transaction', side_effect=AssertionError('restart')):
            value = config(); value['groups'][0]['ruleSets'] = [item()]; value = normalize(value)
            with patch.object(engine, 'load_configuration', return_value=value):
                reply = bridge.dispatch({'action': 'update-rule-sets', 'revision': 0, 'updates': [item()]})
        self.assertTrue(reply['ok'])
        self.assertEqual(reply['configuration']['revision'], 0)

    def test_schema_mismatch_keeps_the_signed_update_capability(self):
        with patch.object(bridge.socket, 'socket') as sock:
            connection = sock.return_value.__enter__.return_value
            connection.recv.return_value = protocol.encode({'ok': True, 'version': VERSION - 1, 'signedUpdates': True})
            reply = bridge.client({'action': 'status'})
        self.assertTrue(reply['requiresUpdate'] and reply['signedUpdates'])


class StoreTests(AssetCase):
    def fake_run(self, args, input_text=None, timeout=30):
        if args[1:3] == ['rule-set', 'compile']:
            pathlib.Path(args[4]).write_bytes(b'SRS compiled')
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=0 if pathlib.Path(args[-1]).read_bytes()[:3] == b'SRS' else 1)

    def test_store_rejects_bad_input_before_touching_assets(self):
        with patch.object(engine, 'run', side_effect=lambda *a, **k: SimpleNamespace(returncode=1)):
            for bad in ('not base64!', '', base64.b64encode(b'SRS corrupt').decode(), base64.b64encode(b'{}').decode()):
                with self.assertRaises(ConfigurationError, msg=bad): rulesets.store(bad)
        self.assertEqual(list(self.assets.iterdir()), [])

    def test_store_keeps_validated_bytes_under_their_digest(self):
        with patch.object(engine, 'run', side_effect=self.fake_run):
            digest = rulesets.store(base64.b64encode(b'SRS synthetic').decode())
            self.assertEqual(digest, hashlib.sha256(b'SRS synthetic').hexdigest())
            self.assertEqual((self.assets / (digest + '.srs')).read_bytes(), b'SRS synthetic')
            self.assertEqual(rulesets.store(base64.b64encode(b'SRS synthetic').decode()), digest)
            compiled = rulesets.store(base64.b64encode(b'{"version":3,"rules":[]}').decode())
            self.assertEqual(compiled, hashlib.sha256(b'SRS compiled').hexdigest())

    def test_store_refuses_to_follow_a_planted_symlink(self):
        digest = hashlib.sha256(b'SRS synthetic').hexdigest()
        (self.assets / (digest + '.srs')).symlink_to('/etc/hosts')
        with patch.object(engine, 'run', side_effect=self.fake_run), self.assertRaises(ConfigurationError):
            rulesets.store(base64.b64encode(b'SRS synthetic').decode())

    def test_prune_removes_only_old_unreferenced_assets(self):
        kept = self.asset(DIGEST, age=10 * 86400)
        recent = self.asset('b' * 64)
        old = self.asset('c' * 64, age=10 * 86400)
        value = config(); value['groups'][0]['ruleSets'] = [item()]
        rulesets.prune(normalize(value))
        self.assertTrue(kept.exists() and recent.exists())
        self.assertFalse(old.exists())

    def test_prune_waits_for_an_unfinished_transaction(self):
        old = self.asset('c' * 64, age=10 * 86400)
        (self.base / 'transaction.json').write_text('{}')
        rulesets.prune(normalize(config()))
        self.assertTrue(old.exists())


class ListedTests(unittest.TestCase):
    def test_lists_flatten_single_values_and_multiple_rules(self):
        result = rulesets.listed({'version': 2, 'rules': [
            {'domain': 'exact.example.invalid', 'domain_suffix': ['suffix.example.invalid'], 'domain_keyword': 'kw', 'domain_regex': '^a.*b$'},
            {'ip_cidr': ['198.51.100.0/24', '2001:db8::/32']}]})
        self.assertEqual(result, {'domains': ['exact.example.invalid'], 'suffixes': ['suffix.example.invalid'], 'keywords': ['kw'],
                                  'regexes': ['^a.*b$'], 'cidrs': ['198.51.100.0/24', '2001:db8::/32']})

    def test_conditions_that_lists_would_widen_stay_a_rule_set(self):
        for rule in ({'domain': 'x.invalid', 'port': 443}, {'type': 'logical', 'mode': 'and', 'rules': []},
                     {'domain': 'x.invalid', 'invert': True}, {'domain_suffix': ['.dotted.invalid']},
                     {'process_name': 'x'}, {'domain': ['bad name']}, {'domain': [7]}):
            with self.assertRaises(ConfigurationError, msg=rule): rulesets.listed({'rules': [rule]})

    def test_oversized_lists_are_refused(self):
        rule = {'domain_keyword': ['k%d' % i for i in range(rulesets.MAX_EXPANDED + 1)]}
        with self.assertRaisesRegex(ConfigurationError, 'слишком большой'): rulesets.listed({'rules': [rule]})


@unittest.skipUnless(BINARY.is_file(), 'Run ./build.sh to prepare the pinned test runtime')
class PinnedRuntimeTests(AssetCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(engine, 'BIN', BINARY); patcher.start(); self.addCleanup(patcher.stop)

    def source(self, rules):
        return base64.b64encode(json.dumps({'version': 3, 'rules': rules}).encode()).decode()

    def test_json_source_compiles_stores_and_lists_back(self):
        digest = rulesets.store(self.source([{'domain': ['exact.example.invalid'], 'domain_suffix': ['suffix.example.invalid'], 'domain_keyword': ['kw'], 'domain_regex': ['^a.*b$']},
                                             {'ip_cidr': ['198.51.100.0/24']}]))
        self.assertEqual(rulesets.contents(digest), {'domains': ['exact.example.invalid'], 'suffixes': ['suffix.example.invalid'], 'keywords': ['kw'],
                                                     'regexes': ['^a.*b$'], 'cidrs': ['198.51.100.0/24']})

    def test_narrowing_and_dotted_rules_are_stored_but_not_listed(self):
        for rules in ([{'domain': ['x.invalid'], 'port': [443]}], [{'domain_suffix': ['.dotted.invalid']}],
                      [{'type': 'logical', 'mode': 'and', 'rules': [{'domain': ['y.invalid']}, {'port': [80]}]}]):
            with self.assertRaises(ConfigurationError, msg=rules): rulesets.contents(rulesets.store(self.source(rules)))

    def test_invalid_bytes_are_refused_by_the_real_parser(self):
        for data in (b'SRS corrupt', b'garbage', b'{"version":3,"rules":[{"unknown_field":["x"]}]}'):
            with self.assertRaises(ConfigurationError, msg=data): rulesets.store(base64.b64encode(data).decode())

    def test_runtime_with_every_kind_passes_check(self):
        domains = rulesets.store(self.source([{'domain_suffix': ['example.invalid']}]))
        addresses = rulesets.store(self.source([{'ip_cidr': ['198.51.100.0/24']}]))
        value = config()
        value['groups'][0].update(keywords=['tracker'], regexes=['^a.*b$'], enabled=False, killSwitch=True,
                                  ruleSets=[item(digest=domains), item('geoip', 'de', addresses), item('custom', 'Mine', domains)])
        path = self.base / 'runtime.json'; path.write_text(json.dumps(engine.build_runtime(value, self.assets)))
        result = subprocess.run([str(BINARY), 'check', '-c', str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_check_rejects_an_invalid_regex(self):
        value = config(); value['groups'][0]['regexes'] = ['(']
        path = self.base / 'runtime.json'; path.write_text(json.dumps(engine.build_runtime(value, self.assets)))
        self.assertNotEqual(subprocess.run([str(BINARY), 'check', '-c', str(path)], capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
