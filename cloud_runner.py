#!/usr/bin/env python3
"""Un passage du moniteur ; l'historique est conservé dans une branche GitHub."""
import argparse
import base64
import binascii
import json
import os
import re
import time
from urllib import request, error

import monitor as m

STATE_BRANCH = 'monitor-state'
STATE_FILE = 'etat.json'
MAX_STATE_BYTES = 900_000
PRODUCT_FIELDS = {
    'id', 'title', 'url', 'price', 'availability', 'item_category', 'published_at',
    'modified_at', 'release_date', 'first_seen_at', 'last_seen_at', 'cause',
    'observed_at', 'event_id',
}
STATE_FIELDS = {
    'initialized', 'scope', 'products', 'pending', 'sent', 'last_success_at',
    'paused_reason', 'consecutive_failures', 'last_failure_at',
}


class StateError(m.WatchError):
    pass


def public_state(state):
    """Ne stocke aucune configuration ou clé dans le dépôt public."""
    result = {k: v for k, v in state.items() if k in STATE_FIELDS}
    products = result.get('products', {})
    if not isinstance(products, dict):
        raise StateError('Historique produit invalide : arrêt sans remise à zéro.')
    def clean(product):
        if not isinstance(product, dict):
            raise StateError('Événement invalide dans l’historique.')
        return {k: v for k, v in product.items() if k in PRODUCT_FIELDS}
    result['products'] = {k: clean(v) for k, v in products.items()}
    for field in ('pending', 'sent'):
        values = result.get(field, [])
        if not isinstance(values, list):
            raise StateError('Liste d’événements invalide dans l’historique.')
        result[field] = [clean(v) for v in values]
    return result


class GitHubState:
    def __init__(self, repository, token, starting_sha, api=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository or ''):
            raise StateError('Dépôt GitHub non indiqué ou invalide.')
        if not token or not re.fullmatch(r'[a-f0-9]{40}', starting_sha or ''):
            raise StateError('Lance cette version depuis GitHub Actions.')
        self.repository = repository
        self.token = token
        self.starting_sha = starting_sha
        self.sha = None
        self.api = api or self._api

    def _api(self, method, suffix, data=None, allow_missing=False):
        req = request.Request('https://api.github.com/repos/' + self.repository + suffix,
            data=json.dumps(data).encode('utf-8') if data is not None else None,
            headers={'Authorization': 'Bearer ' + self.token,
                     'Accept': 'application/vnd.github+json',
                     'Content-Type': 'application/json',
                     'X-GitHub-Api-Version': '2022-11-28',
                     'User-Agent': 'BaronsPersonalWatch-GitHub/0.5'}, method=method)
        try:
            with request.build_opener(m.NoRedirect()).open(req, timeout=25) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise StateError('Réponse GitHub trop volumineuse.')
                return json.loads(raw)
        except error.HTTPError as exc:
            if exc.code == 404 and allow_missing:
                return None
            raise StateError(f'Historique GitHub inaccessible (HTTP {exc.code}). '
                             'Aucun historique n’est effacé.') from None
        except (error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise StateError('Connexion à l’historique GitHub indisponible. '
                             'Aucun nouvel envoi n’est tenté pendant ce passage.') from None

    def load(self):
        entry = self.api('GET', '/contents/' + STATE_FILE + '?ref=' + STATE_BRANCH,
                         allow_missing=True)
        if entry is None:
            branch = self.api('GET', '/git/ref/heads/' + STATE_BRANCH, allow_missing=True)
            if branch is not None:
                raise StateError('La branche d’historique existe mais son fichier manque. '
                                 'Arrêt sans recréer une base silencieusement.')
            self.api('POST', '/git/refs', {'ref': 'refs/heads/' + STATE_BRANCH,
                                         'sha': self.starting_sha})
            initial = {'initialized': False, 'scope': m.SCOPE,
                       'products': {}, 'pending': [], 'sent': [],
                       'consecutive_failures': 0}
            self.save(initial)
            return initial
        try:
            if entry.get('type') != 'file' or entry.get('encoding') != 'base64':
                raise ValueError
            self.sha = entry['sha']
            if not re.fullmatch(r'[a-f0-9]{40}', self.sha):
                raise ValueError
            raw = base64.b64decode(''.join(entry['content'].split()), validate=True)
            if len(raw) > MAX_STATE_BYTES:
                raise ValueError
            state = json.loads(raw)
            if not isinstance(state, dict) or not isinstance(state.get('initialized'), bool):
                raise ValueError
            return public_state(state)
        except (KeyError, ValueError, TypeError, AttributeError, binascii.Error):
            raise StateError('Historique GitHub illisible : arrêt sans effacer les données.') from None

    def save(self, state):
        raw = json.dumps(public_state(state), ensure_ascii=False, indent=2).encode('utf-8')
        if len(raw) > MAX_STATE_BYTES:
            raise StateError('Historique trop volumineux : arrêt sans tronquer les références.')
        body = {'message': 'Actualiser l’historique Pokémon',
                'branch': STATE_BRANCH, 'content': base64.b64encode(raw).decode('ascii')}
        if self.sha:
            body['sha'] = self.sha  # GitHub refuse un historique modifié entre-temps.
        result = self.api('PUT', '/contents/' + STATE_FILE, body)
        content = result.get('content') if isinstance(result, dict) else None
        sha = content.get('sha') if isinstance(content, dict) else None
        if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{40}', sha):
            raise StateError('Écriture de l’historique non confirmée : passage arrêté.')
        self.sha = sha


def drain_pending(state, config, store):
    while state.get('pending'):
        event = state['pending'][0]
        m.notify(config, m.alert_text(event), event['url'])
        state['pending'].pop(0)
        state.setdefault('sent', []).append(event)
        state['sent'] = state['sent'][-200:]
        store.save(state)
        time.sleep(1)


def write_summary(status, observed=None, records=None, pages=None, events=None, pending=None):
    """Résultat lisible dans Actions, sans configuration ni détail d'exception."""
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path:
        return
    messages = {
        'ok': 'Catalogue vérifié ; traitement des alertes terminé.',
        'paused': 'EN PAUSE : aucune lecture du catalogue pendant ce passage.',
        'error': 'PASSAGE INCOMPLET : consulter le journal avant de conclure à une absence de nouveautés.',
        'stopped': 'ARRÊT : ce passage ne confirme pas le bon fonctionnement de la surveillance.',
        'test': 'Test accepté par ntfy ; réception sur le téléphone à confirmer. Le catalogue n’a pas été vérifié.',
    }
    lines = ['## Surveillance Pokémon', '', messages[status], '']
    if observed:
        lines.append('Lecture du catalogue : **' + m.paris(observed) + ' (Paris)**.')
    if records is not None:
        lines.append(f'Fiches suivies : **{records}** sur **{pages}** pages.')
        lines.append(f'Nouveaux événements : **{events}**.')
    if pending is not None:
        lines.append(f'Alertes restant en attente : **{pending}**.')
    lines.extend(['', 'Ce résumé décrit ce passage uniquement. Il ne confirme pas la cadence des suivants.', ''])
    try:
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write('\n'.join(lines))
    except OSError:
        # Le résultat visuel ne doit pas perturber la sauvegarde ni l'envoi.
        print('Résumé GitHub indisponible ; consulter le journal de ce passage.', flush=True)


def one_cycle(config, store, resume=False):
    m.validate_notifications(config)
    state = store.load()
    if state.get('paused_reason') and not resume:
        print('EN PAUSE :', state['paused_reason'], flush=True)
        print('Aucune lecture du site. Après vérification, lance le mode reprendre.', flush=True)
        write_summary('paused', pending=len(state.get('pending', [])))
        return 'paused'
    if resume:
        state.pop('paused_reason', None)
        state['consecutive_failures'] = 0
        store.save(state)
    try:
        records, pages = m.collect({})
        observed = m.utc_now()
        baseline = not state.get('initialized') or state.get('scope') != m.SCOPE
        events = m.update_state(state, records, observed)
        # L'observation et la file d'attente sont durables AVANT toute notification.
        store.save(state)
        for event in events:
            try:
                event.update(m.parse_details(m.fetch_public(event['url']), event['url']))
                state['products'][event['id']].update({k: event[k] for k in
                    ('published_at', 'modified_at', 'release_date')})
            except m.AccessStopped:
                raise
            except m.WatchError:
                pass
            time.sleep(1)
        if events:
            store.save(state)
        print(m.paris(observed), ':', len(records), 'fiches Pokémon sur', pages,
              'pages ;', len(events), 'nouveaux événements.', flush=True)
        if baseline:
            print('Base distante enregistrée sans alerter sur les anciennes fiches.', flush=True)
        drain_pending(state, config, store)
        if state.get('consecutive_failures') or state.get('last_failure_at'):
            state['consecutive_failures'] = 0
            state.pop('last_failure_at', None)
            store.save(state)
        write_summary('ok', observed=observed, records=len(records), pages=pages,
                      events=len(events), pending=len(state.get('pending', [])))
        return 'ok'
    except StateError:
        # Une écriture peut avoir abouti malgré un délai réseau ; pas de réessai aveugle.
        raise
    except m.WatchError as exc:
        count = state.get('consecutive_failures', 0)
        state['consecutive_failures'] = count + 1 if isinstance(count, int) else 1
        state['last_failure_at'] = m.utc_now()
        if isinstance(exc, m.AccessStopped):
            state['paused_reason'] = str(exc)
        elif state['consecutive_failures'] >= 3:
            state['paused_reason'] = 'Trois passages consécutifs incomplets.'
        store.save(state)
        print('Passage incomplet :', str(exc), flush=True)
        if state.get('paused_reason'):
            try:
                m.notify(config, 'Surveillance GitHub arrêtée : ' + state['paused_reason']
                         + '\nVérifie le site et les journaux avant une reprise manuelle.')
            except m.WatchError:
                pass
        write_summary('error', pending=len(state.get('pending', [])))
        return 'error'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['surveiller', 'tester_notification', 'reprendre'],
                        default='surveiller')
    args = parser.parse_args()
    config = {'notification_provider': 'ntfy', 'ntfy_topic': os.environ.get('NTFY_TOPIC', '').strip()}
    try:
        m.validate_notifications(config)
        if args.mode == 'tester_notification':
            m.test_notification(config)
            write_summary('test')
            return
        store = GitHubState(os.environ.get('GITHUB_REPOSITORY', ''),
                            os.environ.get('GITHUB_TOKEN', ''), os.environ.get('GITHUB_SHA', ''))
        status = one_cycle(config, store, resume=args.mode == 'reprendre')
        if status == 'error':
            raise SystemExit(1)
    except m.WatchError as exc:
        print('ARRÊT :', str(exc), flush=True)
        write_summary('stopped')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
