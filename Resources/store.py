"""Serialized revision-checked mutations with a durable rollback journal."""
import copy
import json
import pathlib
import secrets
from configuration import ConfigurationError, import_vless, node, normalize, require, rule_set
import engine
import profiles


class RecoveryError(ConfigurationError):
    pass


def remote_candidate(current, raw, generate_keys):
    require(isinstance(raw, dict) and set(raw) == {'enabled', 'address', 'port', 'serverPublicKey', 'tunnelAddress', 'peerAddress', 'localPort', 'route', 'rotateKeys'},
            'Некорректные настройки удалённого доступа')
    require(type(raw['rotateKeys']) is bool, 'Некорректные настройки удалённого доступа')
    old = current.get('remoteAccess')
    result = {k: v for k, v in raw.items() if k != 'rotateKeys'}
    if old and not raw['rotateKeys']:
        result.update(privateKey=old['privateKey'], publicKey=old['publicKey'])
    else:
        require(generate_keys is not None, 'Не удалось создать ключи WireGuard')
        result['privateKey'], result['publicKey'] = generate_keys()
    if old and old['address'] == raw['address']:
        result['resolved'] = old.get('resolved', [])
    return result


def tailnet_candidate(current, raw):
    editable = {'enabled', 'controlURL', 'hostname', 'authKey', 'ephemeral', 'acceptRoutes', 'route', 'listenPort', 'services', 'resetLogin'}
    require(isinstance(raw, dict) and not set(raw) - editable and not (editable - {'authKey'}) - set(raw), 'Некорректные настройки tailnet')
    require(type(raw['resetLogin']) is bool, 'Некорректные настройки tailnet')
    old = current.get('tailnet')
    result = {k: v for k, v in raw.items() if k != 'resetLogin'}
    # The key is write-only: omitting it keeps the stored one, an empty string clears it.
    if 'authKey' not in raw:
        result['authKey'] = old['authKey'] if old else ''
    # A new state directory starts a new node identity; the old one is pruned after commit.
    same = old and old['controlURL'] == raw['controlURL'] and not raw['resetLogin']
    result['stateId'] = old['stateId'] if same else secrets.token_hex(8)
    if old and old['controlURL'] == raw['controlURL']:
        result['resolved'] = old.get('resolved', [])
    return result


def stored_rule_set(digest):
    path = engine.ASSETS / (digest + '.srs')
    return not path.is_symlink() and path.is_file()


def candidate(current, request, generate_keys=None):
    require(request['revision'] == current['revision'], 'Настройки изменились. Обновите данные перед сохранением.')
    result = copy.deepcopy(current)
    action = request['action']
    registry = {v['id']: v for v in result['nodes']}
    if action == 'save-node':
        raw = request['node']
        require(isinstance(raw, dict), 'Некорректный сервер')
        require(not set(raw) & {'resolved', 'private_address', 'private_network', 'packet_encoding'}, 'Служебные поля сервера недоступны для изменения')
        if 'uri' in raw:
            require(set(raw) == {'uri'}, 'Импорт ссылки не принимает дополнительные поля')
            replacement = import_vless(raw['uri'])
        else:
            replacement = copy.deepcopy(raw)
            clear_pin = replacement.pop('clearPin', False)
            require(type(clear_pin) is bool, 'Некорректное изменение pin')
            old = registry.get(raw.get('id'))
            if old:
                if not replacement.get('uuid'):
                    replacement['uuid'] = old['uuid']
                tls = replacement.get('tls', {})
                require(isinstance(tls, dict), 'Некорректная настройка TLS')
                require(not (clear_pin and tls.get('certificate_public_key_sha256')), 'Удаление pin нельзя совместить с новым pin')
                if not tls.get('certificate_public_key_sha256') and not clear_pin:
                    if old['tls'].get('certificate_public_key_sha256'):
                        tls['certificate_public_key_sha256'] = old['tls']['certificate_public_key_sha256']
                replacement['tls'] = tls
                if replacement.get('address') == old['address']:
                    replacement['resolved'] = old.get('resolved', [])
                for key in ('private_address', 'private_network', 'packet_encoding'):
                    if key in old:
                        replacement[key] = old[key]
            else:
                replacement.pop('clearPin', None)
        replacement = node(replacement)
        result['nodes'] = [replacement if v['id'] == replacement['id'] else v for v in result['nodes']]
        if replacement['id'] not in registry:
            result['nodes'].append(replacement)
    elif action == 'delete-node':
        used = [g['title'] for g in result['groups'] + [result['fallback']] if request['id'] in g['nodes']]
        require(not used, 'Сервер используется: ' + ', '.join(used))
        require(request['id'] in registry, 'Сервер не найден')
        result['nodes'] = [v for v in result['nodes'] if v['id'] != request['id']]
    elif action == 'save-group':
        group = copy.deepcopy(request['group'])
        require(isinstance(group, dict), 'Некорректная группа')
        existing = next((g for g in result['groups'] if g['id'] == group.get('id')), None)
        if group.get('id') == 'fallback':
            result['fallback'] = group
        else:
            if existing:
                result['groups'] = [group if v['id'] == group['id'] else v for v in result['groups']]
            else:
                result['groups'].append(group)
    elif action == 'delete-group':
        require(any(g['id'] == request['id'] for g in result['groups']), 'Группа не найдена')
        result['groups'] = [g for g in result['groups'] if g['id'] != request['id']]
    elif action == 'reorder-groups':
        ids = request['ids']
        require(isinstance(ids, list) and all(isinstance(i, str) for i in ids)
                and len(ids) == len(result['groups']) and set(ids) == {g['id'] for g in result['groups']}, 'Некорректный порядок групп')
        lookup = {g['id']: g for g in result['groups']}
        result['groups'] = [lookup[i] for i in ids]
    elif action in ('set-enabled', 'set-kill-switch', 'set-chain', 'reconnect-route'):
        group = next((g for g in result['groups'] + [result['fallback']] if g['id'] == request['id']), None)
        require(group is not None, 'Группа не найдена')
        if action == 'reconnect-route':
            require(group['enabled'] and not result.get('bypass', False), 'Цепочка отключена')
        elif action == 'set-chain':
            group['nodes'] = request['nodes']
        else:
            group['enabled' if action == 'set-enabled' else 'killSwitch'] = request['enabled']
    elif action == 'set-bypass':
        result['bypass'] = request['enabled']
    elif action == 'save-remote-access':
        result['remoteAccess'] = remote_candidate(current, request['remote'], generate_keys)
    elif action == 'set-remote-access':
        require('remoteAccess' in result, 'Удалённый доступ не настроен')
        result['remoteAccess']['enabled'] = request['enabled']
    elif action == 'delete-remote-access':
        require('remoteAccess' in result, 'Удалённый доступ не настроен')
        del result['remoteAccess']
    elif action == 'save-tailnet':
        result['tailnet'] = tailnet_candidate(current, request['tailnet'])
    elif action == 'set-tailnet':
        require('tailnet' in result, 'Tailnet не настроен')
        result['tailnet']['enabled'] = request['enabled']
    elif action == 'delete-tailnet':
        require('tailnet' in result, 'Tailnet не настроен')
        require(not any(g.get('exit') == 'tailnet' for g in result['groups']), 'Сначала переключите маршруты, которые выходят через tailnet')
        del result['tailnet']
    elif action == 'update-rule-sets':
        updates = request['updates']
        require(isinstance(updates, list) and len(updates) <= 1024, 'Некорректный набор правил')
        lookup = {}
        for raw in map(rule_set, updates):
            require(raw['kind'] != 'custom' or 'url' in raw, 'Некорректный набор правил')
            lookup[(raw['kind'], raw['name'], raw.get('url'))] = raw['digest']
        for group in result['groups']:
            for item in group.get('ruleSets', []):
                item['digest'] = lookup.get((item['kind'], item['name'], item.get('url')), item['digest'])
    else:
        raise ConfigurationError('Неизвестное изменение конфигурации')
    result['revision'] += 1
    result = normalize(result)
    # A digest names only a file the helper validated and stored, never a caller-chosen path.
    added = profiles.references(result) - profiles.references(normalize(current))
    require(all(stored_rule_set(digest) for digest in added), 'Набор правил недоступен')
    return result


class Transaction:
    def __init__(self, check, stop, guard, start, ready, report=lambda event: None):
        self.check, self.stop, self.guard, self.start, self.ready = check, stop, guard, start, ready
        self.report = report

    def report_failure(self, event):
        try:
            self.report(event)
        except Exception:
            pass  # A logging failure must never interrupt rollback.

    def apply(self, old, new):
        with engine.configuration_lock():
            self.recover_locked()
            require(engine.load_configuration()['revision'] == old['revision'], 'Настройки изменились. Обновите данные перед сохранением.')
            return self._apply(old, new)

    def recover(self):
        if not (engine.BASE / 'transaction.json').exists():
            return
        with engine.configuration_lock():
            self.recover_locked()

    def recover_locked(self):
        journal = engine.BASE / 'transaction.json'
        if not journal.exists():
            return
        protection = None
        try:
            saved = json.loads(journal.read_text())
            old = normalize(saved['old'])
            protection = saved['guard']
            self.stop(); self.guard(protection)
            engine.atomic_json(engine.CONFIG, old)
            self.start(); self.ready(old['revision'])
            self.guard(old)
            journal.unlink()
            self.report_failure('transaction-recovered')
        except Exception:
            self.report_failure('transaction-recovery-failed')
            if protection is not None:
                try:
                    self.guard(protection)
                except Exception:
                    self.report_failure('transaction-recovery-guard-failed')
            raise RecoveryError('Не удалось восстановить предыдущее состояние. Защита сохранена. Восстановите помощник через меню.') from None

    def _apply(self, old, new):
        self.check(new)
        journal = engine.BASE / 'transaction.json'
        # Keep the stricter guard until commit or successful rollback.
        protection = copy.deepcopy(new)
        protection['bypass'] = False
        protection['killSwitch'] = engine.protected(old) or engine.protected(new)
        protection['nodes'] = old['nodes'] + new['nodes']
        engine.atomic_json(journal, {'old': old, 'guard': protection})
        phase = 'stop'
        try:
            self.stop()
            phase = 'guard'; self.guard(protection)
            phase = 'write'; engine.atomic_json(engine.CONFIG, new)
            phase = 'start'; self.start()
            phase = 'ready'; self.ready(new['revision'])
            phase = 'commit-guard'; self.guard(new)
            journal.unlink()
        except Exception:
            self.report_failure('transaction-' + phase + '-failed')
            try:
                self.stop(); self.guard(protection)
                engine.atomic_json(engine.CONFIG, old)
                self.start(); self.ready(old['revision'])
                self.guard(old); journal.unlink()
            except Exception:
                self.report_failure('transaction-rollback-failed')
                self.guard(protection)
                raise ConfigurationError('Не удалось подтвердить откат. Предыдущее состояние сохранено; помощник повторит восстановление автоматически.') from None
            raise ConfigurationError('Изменения не применены; восстановлена предыдущая конфигурация.') from None
