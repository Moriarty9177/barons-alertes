import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor as m

FIXTURES = Path(__file__).parent
URL = m.ORIGIN + '/les-tcg/cartes-pokemon/display-pokemon/'
PRODUCT = (m.ORIGIN + '/tradingcard-game/cartes-pokemon/pokebox-pokemon/'
           'pokemon-pack-1-pokebox-30e-ans-nymphali-ex-1-bundle-nuit-noire-1-tripack-nuit-noire-en-francais/')


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.text = (FIXTURES / 'catalogue-reduit.html').read_text(encoding='utf-8')
        self.products, self.pages = m.parse_catalog(self.text, URL)
        self.product = next(iter(self.products.values()))

    def test_live_catalogue_fixture_all_languages(self):
        self.assertEqual(len(self.products), 16)
        self.assertEqual(self.products['202173']['price'], 349.99)
        self.assertEqual(self.products['202173']['availability'], 'commandable')
        self.assertTrue(any('CASE' in item['title'] for item in self.products.values()))
        self.assertTrue(any('japonais' in item['title'] for item in self.products.values()))
        self.assertTrue(any('coréen' in item['title'] for item in self.products.values()))

    def test_accessory_without_pokemon_or_language_in_title_is_kept(self):
        payload = {'id': 321, 'item_name': 'Portfolio Gardevoir', 'price': 19.99,
                   'stockstatus': 'instock', 'productlink': m.ORIGIN + '/tradingcard-game/accessoires/portfolio-gardevoir/'}
        from html import escape
        text = '<div class="products"><div class="card-game"><span data-gtm4wp_product_data="' + escape(json.dumps(payload), quote=True) + '"></span><button class="add_to_cart_button"></button></div></div>'
        products, _ = m.parse_catalog(text, URL)
        self.assertEqual(products['321']['title'], 'Portfolio Gardevoir')

    def test_single_cards_excluded_by_category_or_url(self):
        from html import escape
        base = {'id': 321, 'item_name': 'Pikachu Illustration Rare', 'price': 19.99,
                'stockstatus': 'instock', 'productlink': m.ORIGIN + '/tradingcard-game/cartes-pokemon/exemple/'}
        for category in ["Cartes à l'unité Pokémon", 'Cartes à l’unité Pokémon']:
            data = {**base, 'item_category': category}
            text = '<div class="products"><div class="card-game"><span data-gtm4wp_product_data="' + escape(json.dumps(data), quote=True) + '"></span></div></div>'
            products, _ = m.parse_catalog(text, URL)
            self.assertEqual(products, {})
        self.assertTrue(m.is_single_card({**base, 'productlink': m.ORIGIN + '/tradingcard-game/cartes-pokemon/cartes-a-lunite-pokemon/pikachu/'}))
        self.assertFalse(m.is_single_card({**base, 'item_name': 'Duopack + 3 cartes promos', 'item_category': 'Duo-Pack Pokémon'}))

    def test_pending_single_card_alert_removed(self):
        card = {**self.product, 'item_category': "Cartes à l'unité Pokémon"}
        state = {'initialized': True, 'scope': m.SCOPE, 'products': {}, 'pending': [card]}
        m.update_state(state, {}, '2026-10-06T08:00:00+00:00')
        self.assertEqual(state['pending'], [])

    def test_migration_keeps_history_without_catalogue_flood(self):
        state = {'initialized': True, 'products': {}, 'pending': []}
        self.assertEqual(m.update_state(state, self.products, '2026-10-06T08:00:00+00:00'), [])
        self.assertEqual(state['scope'], m.SCOPE)
        new_product = {**self.product, 'id': '1000'}
        events = m.update_state(state, {**self.products, '1000': new_product}, '2026-10-06T08:02:00+00:00')
        self.assertEqual([e['id'] for e in events], ['1000'])

    def test_pagination_span_not_link(self):
        self.assertEqual(self.pages, [URL + 'page/2/'])

    def test_marketing_pagination_is_read_without_tracking_parameters(self):
        tree = m.html.fromstring(self.text)
        for element in tree.xpath('//*[' + m.cls('woocommerce-pagination') + ']//*[@href]'):
            element.set('href', element.get('href') + '?utm_source=google&utm_medium=cpc'
                        '&utm_campaign=test&gad_source=1&gad_campaignid=123&gclid=example')
        text = m.html.tostring(tree, encoding='unicode')
        products, pages = m.parse_catalog(text, URL)
        self.assertEqual(products, self.products)
        self.assertEqual(pages, [URL + 'page/2/'])
        requested = []
        with patch.object(m, 'CATEGORIES', [m.parse.urlsplit(URL).path]), \
                patch.object(m.time, 'sleep'):
            def fetch_cycle(url):
                requested.append(url)
                return text if url == URL else self.text.replace(URL + 'page/2/', URL)
            m.collect({}, fetch=fetch_cycle)
        self.assertEqual(requested, [URL, URL + 'page/2/'])

    def test_marketing_product_link_is_canonical_but_actions_remain_rejected(self):
        self.assertEqual(m.catalog_url(PRODUCT + '?utm_source=google&gclid=example'), PRODUCT)
        tree = m.html.fromstring(self.text)
        element = tree.xpath('//*[@data-gtm4wp_product_data]')[0]
        data = json.loads(element.get('data-gtm4wp_product_data'))
        data['productlink'] += '?utm_source=google&gclid=example'
        element.set('data-gtm4wp_product_data', json.dumps(data))
        products, _ = m.parse_catalog(m.html.tostring(tree, encoding='unicode'), URL)
        self.assertEqual(products, self.products)
        for link in [PRODUCT + '?utm_source=google&add-to-cart=123',
                     PRODUCT + '?orderby=price', 'https://evil.test/p/?utm_source=google',
                     m.ORIGIN + '/mon-compte/?utm_source=google',
                     PRODUCT + '?utm_source=google#fragment']:
            with self.subTest(link=link), self.assertRaises(m.WatchError):
                m.catalog_url(link)
        with self.assertRaises(m.WatchError):
            m.public_url(PRODUCT + '?utm_source=google')

    def test_publication_not_release_date(self):
        detail = m.parse_details((FIXTURES / 'fiche-reduite.html').read_text(), PRODUCT)
        self.assertEqual(detail['published_at'], '2026-09-30T16:02:16+00:00')
        self.assertEqual(m.paris(detail['published_at']), '30/09/2026 18:02:16')
        self.assertEqual(detail['release_date'], '16 Octobre 2026')

    def test_baseline_then_new_and_no_duplicate(self):
        state = {}
        self.assertEqual(m.update_state(state, self.products, '2026-10-06T08:00:00+00:00'), [])
        new_product = {**self.product, 'id': '999', 'availability': 'indisponible'}
        records = {**self.products, '999': new_product}
        events = m.update_state(state, records, '2026-10-06T08:02:00+00:00')
        self.assertEqual([e['cause'] for e in events], ['nouvelle_fiche'])
        self.assertEqual(len(state['pending']), 1)
        self.assertEqual(m.update_state(state, records, '2026-10-06T08:04:00+00:00'), [])
        self.assertEqual(len(state['pending']), 1)

    def test_opening_and_price_change(self):
        state = {}
        key = self.product['id']
        m.update_state(state, {key: {**self.product, 'availability': 'indisponible'}}, '2026-10-06T08:00:00+00:00')
        events = m.update_state(state, {key: {**self.product, 'availability': 'precommande'}}, '2026-10-06T08:02:00+00:00')
        self.assertEqual(events[0]['cause'], 'commandes_ouvertes')
        self.assertEqual(m.update_state(state, {key: {**self.product, 'availability': 'precommande', 'price': 1}}, '2026-10-06T08:04:00+00:00'), [])

    def test_disappearance_does_not_erase_history(self):
        state = {}
        m.update_state(state, self.products, '2026-10-06T08:00:00+00:00')
        m.update_state(state, {}, '2026-10-06T08:02:00+00:00')
        self.assertEqual(m.update_state(state, self.products, '2026-10-06T08:04:00+00:00'), [])

    def test_invalid_and_empty_responses(self):
        with self.assertRaises(m.WatchError):
            m.parse_catalog('<html>Connexion</html>', URL)
        broken = self.text.replace('&quot;instock&quot;', '&quot;unknown&quot;')
        with self.assertRaises(m.WatchError):
            m.parse_catalog(broken, URL)

    def test_no_cart_or_external_url(self):
        for url in [URL + '?add-to-cart=202173', 'https://evil.test/p/', m.ORIGIN + '/mon-compte/']:
            with self.assertRaises(m.WatchError):
                m.public_url(url)

    def test_failed_telegram_retains_pending(self):
        state = {'pending': [{**self.product, 'cause': 'nouvelle_fiche',
                             'observed_at': '2026-10-06T08:00:00+00:00'}]}
        before = copy.deepcopy(state)
        with patch.object(m, 'telegram', side_effect=m.WatchError('réseau indisponible')):
            with self.assertRaises(m.WatchError):
                m.deliver_pending(state, {'telegram_token': 'secret', 'chat_id': 1})
        self.assertEqual(state, before)


class NotificationTests(unittest.TestCase):
    TOPIC = 'barons-' + 'a' * 32
    CONFIG = {'notification_provider': 'ntfy', 'ntfy_topic': TOPIC}

    def test_ntfy_json_accents_and_product_click(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps({
            'event': 'message', 'topic': self.TOPIC, 'id': 'accepted'}).encode())
        with patch.object(m.request, 'build_opener', return_value=opener):
            m.notify(self.CONFIG, 'Précommande — Pokémon', PRODUCT)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://ntfy.sh/')
        self.assertEqual(req.get_method(), 'POST')
        payload = json.loads(req.data.decode('utf-8'))
        self.assertEqual(payload['topic'], self.TOPIC)
        self.assertEqual(payload['message'], 'Précommande — Pokémon')
        self.assertEqual(payload['priority'], 4)
        self.assertEqual(payload['click'], PRODUCT)

    def test_invalid_topic_never_sends(self):
        with patch.object(m.request, 'build_opener') as opener:
            for topic in ['pokemon', 'barons-short', '../secret', '', None]:
                with self.assertRaises(m.WatchError):
                    m.ntfy(topic, 'test')
            opener.assert_not_called()

    def test_http_failure_does_not_expose_topic(self):
        opener = Mock()
        opener.open.side_effect = m.error.HTTPError(
            'https://ntfy.sh/' + self.TOPIC, 429, 'rate limited', {}, None)
        with patch.object(m.request, 'build_opener', return_value=opener):
            with self.assertRaises(m.WatchError) as caught:
                m.ntfy(self.TOPIC, 'test')
        self.assertIn('429', str(caught.exception))
        self.assertNotIn(self.TOPIC, str(caught.exception))

    def test_wrong_acknowledgement_rejected(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps({
            'event': 'message', 'topic': 'another-topic', 'id': 'accepted'}).encode())
        with patch.object(m.request, 'build_opener', return_value=opener):
            with self.assertRaises(m.WatchError):
                m.ntfy(self.TOPIC, 'test')

    def test_ntfy_failure_retains_pending_and_retry_saves(self):
        event = {'title': 'Pokémon test', 'cause': 'nouvelle_fiche', 'price': 20,
                 'availability': 'indisponible', 'url': PRODUCT,
                 'observed_at': '2026-10-06T08:00:00+00:00'}
        state = {'pending': [event]}
        before = copy.deepcopy(state)
        with patch.object(m, 'ntfy', side_effect=m.WatchError('hors ligne')):
            with self.assertRaises(m.WatchError):
                m.deliver_pending(state, self.CONFIG)
        self.assertEqual(state, before)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch.object(m, 'ntfy') as sender, patch.object(m.time, 'sleep'):
                m.deliver_pending(state, self.CONFIG, path)
            sender.assert_called_once_with(self.TOPIC, m.alert_text(event), PRODUCT)
            saved = json.loads(path.read_text())
            self.assertEqual(saved['pending'], [])
            self.assertEqual(saved['sent'], [event])

    def test_explicit_ntfy_overrides_old_telegram_fields(self):
        config = {**self.CONFIG, 'telegram_token': 'old', 'chat_id': 123}
        with patch.object(m, 'ntfy') as ntfy, patch.object(m, 'telegram') as telegram:
            m.notify(config, 'test')
        ntfy.assert_called_once_with(self.TOPIC, 'test', None)
        telegram.assert_not_called()

    def test_setup_creates_topic_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            with patch.object(m, 'CONFIG', path), patch('builtins.input', return_value=''), \
                    patch('builtins.print'), patch.object(m, 'notify') as sender:
                m.setup_ntfy()
                first = json.loads(path.read_text())
                self.assertEqual(m.validate_notifications(first), 'ntfy')
                self.assertEqual(first['interval_seconds'], 120)
                self.assertEqual(sender.call_args.args[0], first)
                m.setup_ntfy()
                self.assertEqual(json.loads(path.read_text())['ntfy_topic'], first['ntfy_topic'])


if __name__ == '__main__':
    unittest.main()
