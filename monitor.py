#!/usr/bin/env python3
"""Moniteur personnel des pages publiques du Coin des Barons. Python >= 3.10."""
import argparse
import getpass
import html as html_text
import json
import os
from pathlib import Path
import re
import secrets
import time
import unicodedata
from datetime import datetime, timezone
from urllib import request, error, parse
from zoneinfo import ZoneInfo
from lxml import html

ROOT = Path(__file__).resolve().parent
ORIGIN = 'https://lecoindesbarons.com'
CONFIG = ROOT / 'config-privee.json'
STATE = ROOT / 'etat.json'
CATEGORIES = ['/les-tcg/cartes-pokemon/']
SCOPE = 'pokemon_category_without_singles_v3'
MAX_PAGES = 24
USER_AGENT = 'BaronsPersonalWatch/0.4 (public product pages; no checkout)'
NTFY_SERVER = 'https://ntfy.sh'


class WatchError(Exception):
    pass


class AccessStopped(WatchError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def paris(value):
    if not value:
        return 'non indiquée'
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(
            ZoneInfo('Europe/Paris')).strftime('%d/%m/%Y %H:%M:%S')
    except (ValueError, TypeError):
        return 'non indiquée'


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFD', value.lower())
                   if unicodedata.category(c) != 'Mn')


def cls(name):
    return 'contains(concat(" ", normalize-space(@class), " "), " ' + name + ' ")'


def save_json(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)


def read_json(path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        raise WatchError(f'{path.name} illisible : arrêt sans effacer les données.') from None


def public_url(url):
    parts = parse.urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc != 'lecoindesbarons.com'
            or parts.query or parts.fragment):
        raise WatchError('Lien public inattendu : lecture annulée.')
    if not parts.path.startswith(('/flags/', '/les-tcg/', '/tradingcard-game/')):
        raise WatchError('Chemin inattendu : lecture annulée.')
    return url


class SameOriginRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Aucune redirection vers une connexion, un panier ou un autre domaine.
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_public(url):
    public_url(url)
    req = request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html'})
    try:
        with request.build_opener(SameOriginRedirect()).open(req, timeout=25) as response:
            if 'text/html' not in response.headers.get('Content-Type', '').lower():
                raise AccessStopped('Réponse inattendue du site : surveillance arrêtée.')
            data = response.read(3_000_001)
            if len(data) > 3_000_000:
                raise WatchError('Page trop volumineuse : lecture annulée.')
            text = data.decode('utf-8', errors='replace')
    except error.HTTPError as exc:
        if exc.code in (401, 403, 429):
            raise AccessStopped(f'Le site répond HTTP {exc.code} : surveillance arrêtée.') from None
        raise WatchError(f'Page inaccessible (HTTP {exc.code}).') from None
    except (error.URLError, TimeoutError, OSError):
        raise WatchError('Connexion au site indisponible.') from None
    if any(marker in text.lower() for marker in (
            '<title>just a moment', 'cf-chl-', 'access denied', 'verify you are human')):
        raise AccessStopped('Une protection du site demande une vérification : arrêt.')
    return text


def is_single_card(data):
    # Le titre « cartes promos » d'un coffret ne doit pas le faire exclure.
    # La taxonomie publique et le chemin de la fiche identifient les cartes à l'unité.
    categories = [str(value) for key, value in data.items() if key.startswith('item_category')]
    for value in categories:
        compact = re.sub(r'[^a-z0-9]', '', normalized(html_text.unescape(value)))
        if compact.startswith(('cartesalunite', 'cartealunite')):
            return True
    url = str(data.get('productlink') or data.get('url') or '')
    segments = parse.unquote(parse.urlsplit(url).path).lower().split('/')
    return any(segment in ('cartes-a-lunite-pokemon', 'carte-a-lunite-pokemon') for segment in segments)


def parse_catalog(text, url):
    tree = html.fromstring(text)
    containers = tree.xpath('//*[' + cls('products') + ']')
    if not containers:
        raise WatchError('Structure du catalogue non reconnue : aucun faux état de rupture enregistré.')
    records = {}
    for container in containers:
        cards = container.xpath('.//*[' + cls('card-game') + ']')
        for card in cards:
            payloads = card.xpath('.//*[@data-gtm4wp_product_data]/@data-gtm4wp_product_data')
            if len(payloads) != 1:
                raise WatchError('Données produit manquantes ou ambiguës dans le catalogue.')
            try:
                data = json.loads(payloads[0])
            except (json.JSONDecodeError, TypeError):
                raise WatchError('Données produit invalides.') from None
            if is_single_card(data):
                continue
            title = html_text.unescape(str(data.get('item_name', '')))
            if not title.strip():
                raise WatchError('Nom produit absent : cycle annulé.')
            product_id = str(data.get('id') or data.get('internal_id') or '')
            link = public_url(str(data.get('productlink', '')))
            status = data.get('stockstatus')
            if not product_id or status not in ('instock', 'outofstock', 'onbackorder'):
                raise WatchError('Identifiant ou disponibilité produit non reconnus.')
            buttons = card.xpath('.//*[' + cls('add_to_cart_button') + ']')
            enabled = any(b.get('disabled') is None and b.get('aria-disabled') != 'true'
                          and 'disabled' not in b.get('class', '').split() for b in buttons)
            preorder = 'precommande' in normalized(' '.join(card.itertext()))
            # L'état inconnu n'est jamais interprété comme une rupture.
            if status == 'outofstock':
                availability = 'indisponible'
            elif enabled:
                availability = 'precommande' if preorder or status == 'onbackorder' else 'commandable'
            else:
                availability = 'a_verifier'
            product = dict(id=product_id, title=title, url=link,
                           price=data.get('price'), availability=availability,
                           item_category=data.get('item_category'))
            if product_id in records and records[product_id] != product:
                raise WatchError('Deux états différents pour une référence : cycle annulé.')
            records[product_id] = product
    next_pages = set()
    for href in tree.xpath('//*[' + cls('woocommerce-pagination') + ']//*[@href]/@href'):
        candidate = parse.urljoin(url, href)
        public_url(candidate)
        if re.fullmatch(re.escape(parse.urlsplit(url).path.split('/page/')[0].rstrip('/'))
                        + r'/page/\d+/', parse.urlsplit(candidate).path):
            next_pages.add(candidate)
    return records, sorted(next_pages)


def parse_details(text, url):
    tree = html.fromstring(text)
    result = {'published_at': None, 'modified_at': None, 'release_date': None}
    for script in tree.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            data = json.loads(script)
        except json.JSONDecodeError:
            continue
        nodes = data.get('@graph', [data]) if isinstance(data, dict) else []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get('@type') == 'WebPage' and node.get('url', '').rstrip('/') == url.rstrip('/'):
                result['published_at'] = node.get('datePublished')
                result['modified_at'] = node.get('dateModified')
    for heading in tree.xpath('//h2 | //h3'):
        value = ' '.join(heading.itertext()).strip()
        if 'date de sortie' in normalized(value):
            result['release_date'] = value.split(':', 1)[-1].strip()
            break
    return result


def collect(config, fetch=fetch_public):
    queue = [ORIGIN + path for path in CATEGORIES]
    visited = set()
    records = {}
    while queue:
        url = queue.pop(0)
        if url in visited:
            continue
        if len(visited) >= MAX_PAGES:
            raise WatchError('Limite de pages atteinte : cycle incomplet annulé.')
        text = fetch(url)
        products, pages = parse_catalog(text, url)
        for key, product in products.items():
            if key in records and records[key] != product:
                raise WatchError('Catalogue incohérent entre catégories : cycle annulé.')
            records[key] = product
        visited.add(url)
        queue.extend(p for p in pages if p not in visited and p not in queue)
        if queue:
            time.sleep(1)
    if not records:
        raise WatchError('Aucun produit correspondant : état précédent conservé.')
    return records, len(visited)


def update_state(state, records, observed_at):
    # L'élargissement crée une nouvelle base sans annoncer tout l'ancien catalogue.
    # Les identifiants et alertes déjà enregistrés sont conservés.
    initialized = state.get('initialized', False) and state.get('scope') == SCOPE
    known = state.setdefault('products', {})
    pending = state.setdefault('pending', [])
    pending[:] = [event for event in pending if not is_single_card(event)]
    events = []
    for key, product in records.items():
        previous = known.get(key)
        cause = None
        if initialized and previous is None:
            cause = 'nouvelle_fiche'
        elif initialized and previous and previous.get('availability') == 'indisponible' \
                and product['availability'] in ('precommande', 'commandable'):
            cause = 'commandes_ouvertes'
        if cause:
            event = {**product, 'cause': cause, 'observed_at': observed_at,
                     'event_id': secrets.token_hex(8)}
            events.append(event)
            pending.append(event)
        known[key] = {**(previous or {}), **product,
                      'first_seen_at': (previous or {}).get('first_seen_at', observed_at),
                      'last_seen_at': observed_at}
    # Une fiche disparue conserve son historique. Elle n'est pas marquée en rupture.
    state['initialized'] = True
    state['scope'] = SCOPE
    state['last_success_at'] = observed_at
    return events


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise WatchError('Redirection du service de notification refusée.')


def telegram(token, method, data=None):
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token or ''):
        raise WatchError('Clé Telegram invalide.')
    req = request.Request('https://api.telegram.org/bot' + token + '/' + method,
                          data=json.dumps(data or {}).encode(),
                          headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with request.build_opener(NoRedirect()).open(req, timeout=20) as response:
            payload = json.load(response)
    except error.HTTPError as exc:
        raise WatchError(f'Telegram répond HTTP {exc.code}. Clé et destinataire à vérifier.') from None
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise WatchError('Envoi Telegram indisponible. Alerte conservée en attente.') from None
    if not payload.get('ok'):
        raise WatchError('Telegram n’a pas confirmé la requête.')
    return payload['result']


def notification_provider(config):
    # Les configurations Telegram des versions précédentes restent utilisables.
    return config.get('notification_provider') or (
        'telegram' if config.get('telegram_token') else 'ntfy')


def validate_notifications(config):
    provider = notification_provider(config)
    if provider == 'ntfy':
        topic = config.get('ntfy_topic')
        if not isinstance(topic, str) or not re.fullmatch(r'barons-[a-f0-9]{32,64}', topic):
            raise WatchError('Configure ntfy avec 1_CONFIGURER.bat avant de démarrer.')
    elif provider == 'telegram':
        if not config.get('telegram_token') or not config.get('chat_id'):
            raise WatchError('Configure Telegram avec 1_CONFIGURER.bat avant de démarrer.')
    else:
        raise WatchError('Service de notification inconnu. Relance 1_CONFIGURER.bat.')
    return provider


def ntfy(topic, text, url=None):
    validate_notifications({'notification_provider': 'ntfy', 'ntfy_topic': topic})
    payload = {'topic': topic, 'title': 'Le Coin des Barons — Pokémon',
               'message': text, 'priority': 4, 'tags': ['bell']}
    if url:
        payload['click'] = public_url(url)
    if len(text.encode('utf-8')) > 4096:
        raise WatchError('Notification trop longue. Alerte conservée en attente.')
    req = request.Request(NTFY_SERVER + '/',
                          data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                          headers={'Content-Type': 'application/json; charset=utf-8'},
                          method='POST')
    try:
        with request.build_opener(NoRedirect()).open(req, timeout=20) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        raise WatchError(f'ntfy répond HTTP {exc.code}. Alerte conservée en attente.') from None
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise WatchError('Envoi ntfy indisponible. Alerte conservée en attente.') from None
    if not isinstance(result, dict) or result.get('event') != 'message' \
            or result.get('topic') != topic or not result.get('id'):
        raise WatchError('ntfy n’a pas confirmé la notification. Alerte conservée en attente.')
    return result


def notify(config, text, url=None):
    if validate_notifications(config) == 'ntfy':
        return ntfy(config['ntfy_topic'], text, url)
    return telegram(config['telegram_token'], 'sendMessage', {
        'chat_id': config['chat_id'], 'text': text,
        'link_preview_options': {'is_disabled': True}})


def test_notification(config):
    notify(config, 'Message de test : les alertes Pokémon du Coin des Barons '
           'utiliseront ce canal. Vérifie sa réception sur ton téléphone.',
           ORIGIN + CATEGORIES[0])
    print('Message accepté par le service. Vérifie sa réception sur ton téléphone.')


def alert_text(event):
    labels = {'nouvelle_fiche': 'NOUVELLE FICHE POKÉMON',
              'commandes_ouvertes': 'COMMANDES OUVERTES'}
    status = {'precommande': 'Précommande accessible', 'commandable': 'Commandable',
              'indisponible': 'Encore indisponible', 'a_verifier': 'Disponibilité à vérifier'}
    price = event.get('price')
    amount = f'{price:.2f} €'.replace('.', ',') if isinstance(price, (float, int)) else 'à vérifier'
    return ('LE COIN DES BARONS — ' + labels[event['cause']] + '\n\n'
            + event['title'][:900] + '\n' + amount + ' — ' + status[event['availability']]
            + '\nDétecté : ' + paris(event['observed_at']) + ' (Paris)'
            + '\nPublication déclarée : ' + paris(event.get('published_at'))
            + ('\nSortie annoncée : ' + event['release_date'] if event.get('release_date') else '')
            + '\n\n' + event['url'] + '\n\nÉtat observé sur le site, à confirmer en ouvrant la fiche.')


def deliver_pending(state, config, save_path=STATE):
    validate_notifications(config)
    while state.get('pending'):
        event = state['pending'][0]
        notify(config, alert_text(event), event['url'])
        state['pending'].pop(0)
        state.setdefault('sent', []).append(event)
        state['sent'] = state['sent'][-200:]
        save_json(save_path, state)
        time.sleep(1)


def setup_telegram():
    print('Crée un bot personnel via https://t.me/BotFather avec /newbot.')
    print('La clé reste dans ce dossier. Ne la colle pas dans une conversation publique.')
    token = getpass.getpass('Clé fournie par BotFather (saisie masquée) : ').strip()
    identity = telegram(token, 'getMe')
    if not identity.get('is_bot'):
        raise WatchError('Identité du bot non confirmée.')
    print('Ouvre https://t.me/' + identity['username'] + ' sur ton téléphone et appuie sur Démarrer.')
    code = 'barons-' + secrets.token_hex(6)
    print('Envoie-lui exactement ce message : ' + code)
    input('Une fois le message envoyé, appuie sur Entrée ici... ')
    updates = telegram(token, 'getUpdates', {'timeout': 0, 'limit': 100})
    candidates = {u['message']['chat']['id'] for u in updates
                  if u.get('message', {}).get('text', '').strip() == code
                  and u.get('message', {}).get('chat', {}).get('type') == 'private'
                  and u['message'].get('from', {}).get('id') == u['message']['chat']['id']}
    if len(candidates) != 1:
        raise WatchError('Message de liaison non retrouvé. Relance 1_CONFIGURER.bat avec un bot personnel neuf.')
    config = {'notification_provider': 'telegram',
              'telegram_token': token, 'chat_id': candidates.pop(),
              'interval_seconds': 120}
    save_json(CONFIG, config)
    test_notification(config)


def setup_ntfy():
    previous = read_json(CONFIG, {})
    topic = previous.get('ntfy_topic', '')
    if not isinstance(topic, str) or not re.fullmatch(r'barons-[a-f0-9]{32,64}', topic):
        topic = 'barons-' + secrets.token_hex(16)
    config = {'notification_provider': 'ntfy', 'ntfy_topic': topic,
              'interval_seconds': 120}
    print('\nInstalle ntfy sur Android : https://play.google.com/store/apps/details?id=io.heckel.ntfy')
    print('Autorise ses notifications. Dans ntfy, ajoute un abonnement avec le bouton +.')
    print('Serveur : ' + NTFY_SERVER)
    print('Nom du sujet (topic), à recopier exactement : ' + topic)
    print('Garde ce nom pour toi : toute personne qui le connaît peut lire et envoyer des messages.')
    print('Active « Instant delivery » / livraison instantanée si cette option est proposée.')
    input('Une fois l’abonnement ajouté sur le téléphone, appuie sur Entrée ici... ')
    save_json(CONFIG, config)
    test_notification(config)
    print('Configuration enregistrée. Après réception du test, lance 2_DEMARRER.bat.')


def setup():
    print('1 — ntfy pour Android (recommandé, sans Telegram ni compte à créer)')
    print('2 — Telegram (optionnel, compatible avec les anciennes versions)')
    choice = input('Choisis 1 ou 2, puis Entrée [1] : ').strip() or '1'
    if choice == '1':
        setup_ntfy()
    elif choice == '2':
        setup_telegram()
    else:
        raise WatchError('Choix invalide. Relance la configuration et choisis 1 ou 2.')


def run(once=False, local_only=False):
    config = read_json(CONFIG, {})
    if not local_only:
        validate_notifications(config)
    interval = max(120, int(config.get('interval_seconds', 120)))
    state = read_json(STATE, {'initialized': False, 'products': {}, 'pending': []})
    failures = 0
    if state.get('paused_reason'):
        raise AccessStopped('Surveillance précédemment arrêtée : ' + state['paused_reason']
                            + '\nVérifie le site, puis utilise --resume pour reprendre manuellement.')
    print('Surveillance du Coin des Barons. Arrêt : Ctrl+C. Ordinateur allumé et connecté requis.')
    while True:
        start = time.monotonic()
        try:
            records, pages = collect(config)
            observed = utc_now()
            first_run = not state.get('initialized') or state.get('scope') != SCOPE
            events = update_state(state, records, observed)
            save_json(STATE, state)
            # Métadonnées récupérées seulement pour les nouvelles alertes, pas pour tout le catalogue.
            for event in events:
                try:
                    event.update(parse_details(fetch_public(event['url']), event['url']))
                    state['products'][event['id']].update({key: event[key] for key in
                        ('published_at', 'modified_at', 'release_date')})
                except AccessStopped:
                    raise
                except WatchError:
                    pass  # La notification part quand même ; date inconnue indiquée explicitement.
                save_json(STATE, state)
                time.sleep(1)
                print(alert_text(event), flush=True)
            print(paris(observed), ':', len(records), 'fiches Pokémon sur', pages,
                  'pages ;', len(events), 'nouveaux événements.', flush=True)
            if first_run:
                print('État de départ enregistré : les anciennes fiches ne déclenchent pas d’alerte.')
            if not local_only:
                deliver_pending(state, config)
            failures = 0
        except AccessStopped as exc:
            state['paused_reason'] = str(exc)
            save_json(STATE, state)
            if not local_only:
                try:
                    notify(config, 'Moniteur arrêté : ' + str(exc))
                except WatchError:
                    pass
            raise
        except WatchError as exc:
            failures += 1
            print('Cycle incomplet :', str(exc), flush=True)
            if failures >= 3:
                state['paused_reason'] = 'Trois cycles consécutifs incomplets.'
                save_json(STATE, state)
                if not local_only:
                    try:
                        notify(config, 'Moniteur arrêté après trois erreurs. '
                               'Vérifie la fenêtre du programme et ta connexion.')
                    except WatchError:
                        pass
                raise AccessStopped(state['paused_reason'])
            if once:
                raise
        if once:
            return
        time.sleep(max(1, interval * min(2 ** failures, 4) - (time.monotonic() - start)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--local-only', action='store_true', help='Test sans notification mobile.')
    parser.add_argument('--test-notification', action='store_true', help='Envoie un test sans lire le catalogue.')
    parser.add_argument('--resume', action='store_true', help='Reprise manuelle après avoir vérifié le site.')
    args = parser.parse_args()
    try:
        if args.setup:
            setup()
        elif args.test_notification:
            test_notification(read_json(CONFIG, {}))
        else:
            if args.resume:
                state = read_json(STATE, {})
                state.pop('paused_reason', None)
                save_json(STATE, state)
            run(args.once, args.local_only)
    except KeyboardInterrupt:
        print('\nSurveillance arrêtée.')
    except WatchError as exc:
        print('ARRÊT :', str(exc))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
