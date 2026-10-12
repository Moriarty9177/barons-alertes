"""Lecture publique de Pokémon / produits scellés chez InvestCollect."""
import math
import re
import time
from datetime import datetime
from urllib import parse
from zoneinfo import ZoneInfo

from lxml import html
import monitor as m

NAME = 'InvestCollect — La Ligue du Jeu'
ORIGIN = 'https://investcollect.com'
CATALOG = ORIGIN + '/eshop/produits-scelles.html'
SCOPE = 'investcollect_pokemon_sealed_v1'
MAX_PAGES = 12


def public_url(url):
    parts = parse.urlsplit(url)
    if parts.scheme != 'https' or parts.netloc != 'investcollect.com' or parts.fragment:
        raise m.WatchError('Lien InvestCollect inattendu : lecture annulée.')
    if parts.path == '/eshop/produits-scelles.html':
        if parts.query and not re.fullmatch(r'p=[1-9][0-9]*', parts.query):
            raise m.WatchError('Pagination InvestCollect inattendue.')
    elif re.fullmatch(r'/eshop/p/[a-zA-Z0-9_-]+\.html', parts.path):
        if parts.query:
            raise m.WatchError('Paramètre produit InvestCollect inattendu.')
    else:
        raise m.WatchError('Chemin InvestCollect inattendu : lecture annulée.')
    return url


def product_url(url):
    parts = parse.urlsplit(url)
    if parts.query and parts.query != 'key_cat=produits-scelles':
        raise m.WatchError('Lien produit InvestCollect inattendu.')
    cleaned = public_url(parse.urlunsplit(parts._replace(query='')))
    if not parse.urlsplit(cleaned).path.startswith('/eshop/p/'):
        raise m.WatchError('La fiche InvestCollect attendue est absente.')
    return cleaned


def fetch_public(url):
    return m.fetch_public(url, url_validator=public_url)


def release_date(title):
    match = re.match(r'^\[(\d{2}-\d{2}-\d{4})\]', title)
    if not match:
        return None
    try:
        return datetime.strptime(match[1], '%d-%m-%Y').date().isoformat()
    except ValueError:
        return None


def parse_catalog(text, url):
    public_url(url)
    tree = html.fromstring(text)
    cards = tree.xpath('//*[' + m.cls('products-item') + ']')
    counts = tree.xpath('//*[' + m.cls('nb-products-col') + ']')
    if not cards or len(counts) != 1:
        raise m.WatchError('Structure InvestCollect non reconnue : historique conservé.')
    count = re.fullmatch(r'\s*(\d+)\s+articles?\s*', ''.join(counts[0].itertext()))
    if not count:
        raise m.WatchError('Nombre de produits InvestCollect non reconnu.')
    records = {}
    for card in cards:
        links = card.xpath('.//*[' + m.cls('title') + ']//a[@href]')
        if len(links) != 1:
            raise m.WatchError('Titre InvestCollect manquant ou ambigu.')
        title = ' '.join(' '.join(links[0].itertext()).split())
        normalized = m.normalized(title)
        # Le périmètre est la catégorie scellée, pas les cartes individuelles/gradées.
        if ('pokemon' not in normalized or 'carte a l\'unite' in normalized
                or 'cartes a l\'unite' in normalized or 'gradee' in normalized):
            continue
        link = product_url(parse.urljoin(url, links[0].get('href')))
        key = card.get('data-gtm-item-key') or card.get('id')
        slug = parse.urlsplit(link).path.rsplit('/', 1)[-1][:-5]
        if not key or key != slug:
            raise m.WatchError('Identifiant InvestCollect absent ou incohérent.')
        statuses = card.xpath('.//*[' + m.cls('stock-block') + ']')
        prices = card.xpath('.//*[' + m.cls('price-block') + ']//*[' + m.cls('current') + ']')
        if len(statuses) != 1 or len(prices) != 1:
            raise m.WatchError('Stock ou prix InvestCollect absent ou ambigu.')
        status = m.normalized(' '.join(' '.join(statuses[0].itertext()).split()))
        controls = card.xpath('.//*[' + m.cls('flow-cart-quickbuy') + ']')
        enabled = any(c.get('disabled') is None and c.get('aria-disabled') != 'true'
                      and 'disabled' not in c.get('class', '').split() for c in controls)
        release = release_date(title)
        if status == 'indisponible':
            availability = 'indisponible' if not enabled else 'a_verifier'
        elif status in ('en stock', 'plus que quelques pieces !') or 'precommande' in status:
            future = release and release > datetime.now(ZoneInfo('Europe/Paris')).date().isoformat()
            availability = ('precommande' if future or 'precommande' in status else 'commandable') \
                if enabled else 'a_verifier'
        else:
            raise m.WatchError('État de stock InvestCollect non reconnu : cycle annulé.')
        price_text = ''.join(prices[0].itertext())
        compact = re.sub(r'\s', '', price_text)
        if not re.fullmatch(r'\d+(?:,\d{2})?€', compact):
            raise m.WatchError('Prix InvestCollect non reconnu.')
        price = float(compact[:-1].replace(',', '.'))
        if not math.isfinite(price) or price < 0:
            raise m.WatchError('Prix InvestCollect invalide.')
        product = dict(id=key, title=title, url=link, price=price,
                       availability=availability, item_category='Pokémon / Produits scellés',
                       release_date=release)
        if key in records:
            raise m.WatchError('Référence InvestCollect dupliquée : cycle annulé.')
        records[key] = product
    pages = set()
    for href in tree.xpath('//*[' + m.cls('pagination') + ']//a[@href]/@href'):
        candidate = public_url(parse.urljoin(url, href))
        if parse.urlsplit(candidate).path != '/eshop/produits-scelles.html':
            raise m.WatchError('Pagination hors du catalogue InvestCollect.')
        pages.add(candidate)
    return records, sorted(pages), int(count[1]), len(cards)


def collect(config, fetch=None):
    fetch = fetch or fetch_public
    queue, visited, records = [CATALOG], set(), {}
    expected, scanned = None, 0
    while queue:
        url = queue.pop(0)
        if url in visited:
            continue
        if len(visited) >= MAX_PAGES:
            raise m.WatchError('Limite InvestCollect atteinte : cycle incomplet annulé.')
        products, pages, count, cards = parse_catalog(fetch(url), url)
        if expected is not None and count != expected:
            raise m.WatchError('Catalogue InvestCollect modifié pendant la lecture : cycle annulé.')
        expected = count
        scanned += cards
        if records.keys() & products.keys():
            raise m.WatchError('Pagination InvestCollect incohérente : références répétées.')
        records.update(products)
        visited.add(url)
        queue.extend(p for p in pages if p not in visited and p not in queue)
        if queue:
            time.sleep(1)
    if not records or scanned != expected:
        raise m.WatchError('Catalogue InvestCollect incomplet : historique conservé.')
    return records, len(visited)


def update_state(state, records, observed_at):
    return m.update_state(state, records, observed_at, scope=SCOPE)


def parse_details(text, url):
    result = m.parse_details(text, url)
    headings = html.fromstring(text).xpath('//h1')
    if headings:
        result['release_date'] = release_date(' '.join(' '.join(headings[0].itertext()).split()))
    return result


def alert_text(event):
    return m.alert_text(event).replace('LE COIN DES BARONS — ',
                                      'INVESTCOLLECT — LA LIGUE DU JEU — ', 1)


def notify(config, text, url=None):
    if m.validate_notifications(config) == 'ntfy':
        return m.ntfy(config['ntfy_topic'], text, url,
                      title=NAME + ' — Pokémon', url_validator=public_url)
    return m.notify(config, text, url)
