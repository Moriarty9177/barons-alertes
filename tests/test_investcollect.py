import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor as m
import investcollect as i
import cloud_runner as c
from test_cloud import MemoryStore, CONFIG

CASE = 'case-10-etb-pokemon-regne-delta-mega-rayquaza-me06-scelle-francais'
TITLE = '[06-11-2026] Case 10 ETB - Pokemon - Règne Delta - Méga-Rayquaza - ME06 - Scellé - Français'

def card(key=CASE, title=TITLE, status='Indisponible', price='999,90', enabled=False):
    # Structure reduced from the public DOM inspected on 12 October 2026.
    control = '<a class="flow-cart-quickbuy display-quickbuy"></a>' if enabled else ''
    return f'''<div class="products-item gtm-click" id="{key}" data-gtm-item-key="{key}">
    <div class="title"><a href="{i.ORIGIN}/eshop/p/{key}.html?key_cat=produits-scelles">{title}</a></div>
    <div class="stock-block"><p>{status}</p></div>
    <div class="price-block"><span class="current">{price}&nbsp;€</span></div>{control}</div>'''

def page(cards, total=None, links=()):
    return (f'<div class="nb-products-col">{len(cards) if total is None else total} articles</div>'
            + ''.join(cards) + '<span class="pagination">'
            + ''.join(f'<a href="{link}">page</a>' for link in links) + '</span>')

class InvestTests(unittest.TestCase):
    def parse(self, markup):
        return i.parse_catalog(markup, i.CATALOG)[0]

    def test_case_price_link_and_release(self):
        p = self.parse(page([card()]))[CASE]
        self.assertEqual((p['price'], p['release_date'], p['availability']), (999.9, '2026-11-06', 'indisponible'))
        self.assertEqual(p['url'], i.ORIGIN + '/eshop/p/' + CASE + '.html')

    def test_stock_controls_and_preorder(self):
        for status in ('En stock', 'Plus que quelques pièces !'):
            p = self.parse(page([card(title='Case Pokémon scellé', status=status, enabled=True)]))[CASE]
            self.assertEqual(p['availability'], 'commandable')
        p = self.parse(page([card(title='[06-11-2099] Case Pokémon scellé', status='En stock', enabled=True)]))[CASE]
        self.assertEqual(p['availability'], 'precommande')
        self.assertEqual(self.parse(page([card(status='En stock')]))[CASE]['availability'], 'a_verifier')
        self.assertEqual(self.parse(page([card(enabled=True)]))[CASE]['availability'], 'a_verifier')

    def test_unknown_or_incomplete_markup_stops(self):
        for markup in (page([card(status='Inconnu')]), page([card(price='?')]),
                       page([card().replace('stock-block', 'changed-stock')]), '<html>erreur</html>'):
            with self.subTest(markup=markup[:40]), self.assertRaises(m.WatchError):
                self.parse(markup)

    def test_singles_graded_and_other_games_excluded(self):
        r = self.parse(page([card(), card('single', "Carte à l'unité Pokémon"),
                             card('graded', 'Carte Pokémon gradée'), card('other', 'Display One Piece')]))
        self.assertEqual(list(r), [CASE])

    def test_urls_reject_cart_actions_external_hosts(self):
        for u in (i.ORIGIN+'/cart', i.CATALOG+'?action=add', i.CATALOG+'#login',
                  i.CATALOG.replace('investcollect.com', 'example.com'), i.ORIGIN+'/eshop/p/case.html?add=1'):
            with self.subTest(url=u), self.assertRaises(m.WatchError):
                i.public_url(u)
        i.public_url(i.CATALOG+'?p=2')

    def test_complete_pagination_and_cycles(self):
        second, third = i.CATALOG+'?p=2', i.CATALOG+'?p=3'
        pages = {i.CATALOG: page([card()], 3, [second, third, second]),
                 second: page([card('second')], 3, [i.CATALOG, third]),
                 third: page([card('third')], 3, [i.CATALOG, second])}
        fetch = Mock(side_effect=lambda u: pages[u])
        with patch.object(i.time, 'sleep'):
            records, count = i.collect({}, fetch=fetch)
        self.assertEqual((len(records), count, fetch.call_count), (3, 3, 3))

    def test_incomplete_changed_or_duplicated_pages_stop(self):
        with self.assertRaises(m.WatchError):
            i.collect({}, fetch=lambda u: page([card()], 2))
        second = i.CATALOG+'?p=2'
        for last in (page([card('second')], 3), page([card()], 2)):
            with patch.object(i.time, 'sleep'), self.assertRaises(m.WatchError):
                i.collect({}, fetch=lambda u: page([card()], 2, [second]) if u == i.CATALOG else last)

    def test_baseline_restock_new_case_and_no_duplicates(self):
        state = {}
        r = self.parse(page([card()]))
        self.assertEqual(i.update_state(state, r, '2026-10-12T15:00:00+00:00'), [])
        r = self.parse(page([card(status='En stock', enabled=True), card('new-case')]))
        e = i.update_state(state, r, '2026-10-12T15:05:00+00:00')
        self.assertEqual([x['cause'] for x in e], ['commandes_ouvertes', 'nouvelle_fiche'])
        self.assertEqual(i.update_state(state, r, '2026-10-12T15:10:00+00:00'), [])
        self.assertEqual(state['scope'], i.SCOPE)

    def test_notification_title_and_safe_click(self):
        opener = MagicMock()
        with patch.object(m.request, 'build_opener', return_value=opener), \
             patch.object(m.json, 'load', return_value={'event':'message', 'topic':CONFIG['ntfy_topic'], 'id':'ack'}):
            i.notify(CONFIG, 'Case Pokémon', i.ORIGIN+'/eshop/p/'+CASE+'.html')
        p = json.loads(opener.open.call_args.args[0].data)
        self.assertIn('InvestCollect', p['title'])
        self.assertEqual(p['click'], i.ORIGIN+'/eshop/p/'+CASE+'.html')

    def test_independent_baseline_preserves_barons(self):
        barons = MemoryStore({'initialized':True, 'scope':m.SCOPE, 'products':{'old':{'id':'old'}}, 'pending':[]})
        before = copy.deepcopy(barons.state)
        store = MemoryStore()
        with patch.object(i, 'collect', return_value=(self.parse(page([card()])), 1)), \
             patch.object(i, 'notify') as sender, patch.dict(c.os.environ, {'GITHUB_STEP_SUMMARY':''}), patch('builtins.print'):
            self.assertEqual(c.one_cycle(CONFIG, store, source=i), 'ok')
        sender.assert_not_called()
        self.assertEqual(barons.state, before)

    def test_one_site_failure_does_not_skip_other(self):
        with patch.object(sys, 'argv', ['cloud_runner.py']), patch.dict(c.os.environ, {'NTFY_TOPIC':CONFIG['ntfy_topic']}), \
             patch.object(c, 'GitHubState') as store, patch.object(c, 'one_cycle', side_effect=[c.StateError('failure'), 'ok']) as cycle, \
             patch.object(c, 'write_summary'), patch('builtins.print'):
            with self.assertRaises(SystemExit) as stopped:
                c.main()
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(cycle.call_count, 2)
        self.assertIs(cycle.call_args.kwargs['source'], i)
        self.assertEqual(store.call_args.kwargs['branch'], 'investcollect-state')

if __name__ == '__main__':
    unittest.main()
