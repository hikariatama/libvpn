"""Serialized revision-checked mutations with a durable rollback journal."""
import copy
import json
import pathlib
from configuration import ConfigurationError, import_vless, node, normalize, require
import engine


class RecoveryError(ConfigurationError):
    pass


def candidate(current, request):
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
            # Snapshots may only be retained from an existing group, never named by a caller.
            require(set(group.get('snapshots', [])) <= set((existing or {}).get('snapshots', [])), 'Неизвестный снимок правил')
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
    else:
        raise ConfigurationError('Неизвестное изменение конфигурации')
    result['revision'] += 1
    return normalize(result)


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
