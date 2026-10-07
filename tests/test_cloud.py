import base64
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloud_runner as c
import monitor as m

CONFIG = {'notification_provider': 'ntfy', 'ntfy_topic': 'barons-' + 'b' * 32}
PRODUCT = {'id': '123', 'title': 'Pokémon test', 'price': 20,
           'url': m.ORIGIN + '/tradingcard-game/cartes-pokemon/test/',
           'availability': 'indisponible', 'item_category': 'Display Pokémon'}


class MemoryStore:
    def __init__(self, state=None):
        self.state = copy.deepcopy(state if state is not None else {
            'initialized': False, 'products': {}, 'pending': [], 'consecutive_failures': 0})
        self.snapshots = []

    def load(self):
        return copy.deepcopy(self.state)

    def save(self, state):
        self.state = copy.deepcopy(c.public_state(state))
        self.snapshots.append(copy.deepcopy(self.state))


class CloudTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(c.os.environ, {'GITHUB_STEP_SUMMARY': ''})
        environment.start()
        self.addCleanup(environment.stop)
        printer = patch('builtins.print')
        printer.start()
        self.addCleanup(printer.stop)
        sleeper = patch.object(c.time, 'sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def baseline(self):
        store = MemoryStore()
        with patch.object(m, 'collect', return_value=({'123': PRODUCT}, 1)), \
                patch.object(m, 'notify') as sender:
            self.assertEqual(c.one_cycle(CONFIG, store), 'ok')
        sender.assert_not_called()
        return store

    def test_baseline_and_later_new_reference_are_distinct(self):
        store = self.baseline()
        new = {**PRODUCT, 'id': '456'}
        observations_at_send = []
        def send(*args):
            observations_at_send.append(copy.deepcopy(store.state))
        with patch.object(m, 'collect', return_value=({'123': PRODUCT, '456': new}, 1)), \
                patch.object(m, 'fetch_public', side_effect=m.WatchError('date indisponible')), \
                patch.object(m, 'notify', side_effect=send) as sender:
            self.assertEqual(c.one_cycle(CONFIG, store), 'ok')
            self.assertEqual(c.one_cycle(CONFIG, store), 'ok')
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(observations_at_send[0]['pending'][0]['id'], '456')
        self.assertEqual(store.state['pending'], [])
        self.assertEqual(store.state['sent'][0]['id'], '456')

    def test_failed_mobile_send_retains_event_for_next_run(self):
        store = self.baseline()
        records = {'123': {**PRODUCT, 'availability': 'commandable'}}
        with patch.object(m, 'collect', return_value=(records, 1)), \
                patch.object(m, 'fetch_public', side_effect=m.WatchError('date inconnue')), \
                patch.object(m, 'notify', side_effect=m.WatchError('ntfy indisponible')):
            self.assertEqual(c.one_cycle(CONFIG, store), 'error')
        self.assertEqual(len(store.state['pending']), 1)
        self.assertEqual(store.state['consecutive_failures'], 1)
        with patch.object(m, 'collect', return_value=(records, 1)), patch.object(m, 'notify') as sender:
            self.assertEqual(c.one_cycle(CONFIG, store), 'ok')
        sender.assert_called_once()
        self.assertEqual(store.state['pending'], [])
        self.assertEqual(store.state['consecutive_failures'], 0)

    def test_site_failure_count_survives_separate_runs(self):
        store = self.baseline()
        with patch.object(m, 'collect', side_effect=m.WatchError('site temporairement indisponible')), \
                patch.object(m, 'notify') as sender:
            for _ in range(3):
                self.assertEqual(c.one_cycle(CONFIG, store), 'error')
        self.assertEqual(store.state['consecutive_failures'], 3)
        self.assertTrue(store.state['paused_reason'])
        sender.assert_called_once()
        self.assertEqual(store.state['products']['123']['availability'], 'indisponible')

    def test_mobile_failures_also_pause_after_three_runs(self):
        store = self.baseline()
        records = {'123': {**PRODUCT, 'availability': 'commandable'}}
        with patch.object(m, 'collect', return_value=(records, 1)), \
                patch.object(m, 'fetch_public', side_effect=m.WatchError('date inconnue')), \
                patch.object(m, 'notify', side_effect=m.WatchError('ntfy indisponible')):
            for _ in range(3):
                self.assertEqual(c.one_cycle(CONFIG, store), 'error')
        self.assertTrue(store.state['paused_reason'])
        self.assertEqual(len(store.state['pending']), 1)

    def test_refused_access_pauses_and_next_run_never_reads_site(self):
        store = self.baseline()
        with patch.object(m, 'collect', side_effect=m.AccessStopped('HTTP 403')), \
                patch.object(m, 'notify'):
            self.assertEqual(c.one_cycle(CONFIG, store), 'error')
        with patch.object(m, 'collect') as reader, patch.object(m, 'notify') as sender:
            self.assertEqual(c.one_cycle(CONFIG, store), 'paused')
        reader.assert_not_called()
        sender.assert_not_called()
        with patch.object(m, 'collect', return_value=({'123': PRODUCT}, 1)):
            self.assertEqual(c.one_cycle(CONFIG, store, resume=True), 'ok')
        self.assertNotIn('paused_reason', store.state)

    def test_store_failure_before_send_never_notifies(self):
        store = self.baseline()
        with patch.object(m, 'collect', return_value=({'123': PRODUCT, '456': {**PRODUCT, 'id': '456'}}, 1)), \
                patch.object(store, 'save', side_effect=c.StateError('GitHub indisponible')), \
                patch.object(m, 'notify') as sender:
            with self.assertRaises(c.StateError):
                c.one_cycle(CONFIG, store)
        sender.assert_not_called()

    def test_secret_fields_never_written_to_state(self):
        state = {'initialized': True, 'ntfy_topic': CONFIG['ntfy_topic'], 'github_token': 'private',
                 'products': {'123': {**PRODUCT, 'telegram_token': 'private'}},
                 'pending': [{**PRODUCT, 'config': CONFIG}]}
        filtered = json.dumps(c.public_state(state))
        self.assertNotIn('private', filtered)
        self.assertNotIn(CONFIG['ntfy_topic'], filtered)
        self.assertNotIn('ntfy_topic', filtered)

    def test_missing_branch_bootstraps_state_without_secret(self):
        api = Mock(side_effect=[None, None, {'ref': 'refs/heads/monitor-state'},
                                {'content': {'sha': 'c' * 40}}])
        store = c.GitHubState('owner/repo', 'fake-token', 'a' * 40, api)
        state = store.load()
        self.assertFalse(state['initialized'])
        body = api.call_args_list[3].args[2]
        self.assertEqual(body['branch'], 'monitor-state')
        self.assertNotIn('sha', body)
        self.assertNotIn('fake-token', base64.b64decode(body['content']).decode())

    def test_missing_state_in_existing_branch_is_not_reinitialized(self):
        api = Mock(side_effect=[None, {'ref': 'refs/heads/monitor-state'}])
        with self.assertRaises(c.StateError):
            c.GitHubState('owner/repo', 'fake-token', 'a' * 40, api).load()
        self.assertEqual(api.call_count, 2)

    def test_saved_blob_sha_is_used_as_write_guard(self):
        state = {'initialized': True, 'scope': m.SCOPE, 'products': {}, 'pending': []}
        entry = {'type': 'file', 'encoding': 'base64', 'sha': 'b' * 40,
                 'content': base64.b64encode(json.dumps(state).encode()).decode()}
        api = Mock(side_effect=[entry, {'content': {'sha': 'c' * 40}}])
        store = c.GitHubState('owner/repo', 'fake-token', 'a' * 40, api)
        store.save(store.load())
        self.assertEqual(api.call_args.args[2]['sha'], 'b' * 40)
        self.assertEqual(store.sha, 'c' * 40)

    def test_corrupt_state_is_never_overwritten(self):
        api = Mock(return_value={'type': 'file', 'encoding': 'base64',
                                'sha': 'b' * 40, 'content': 'not base64'})
        with self.assertRaises(c.StateError):
            c.GitHubState('owner/repo', 'fake-token', 'a' * 40, api).load()
        api.assert_called_once()

    def test_github_error_does_not_expose_token(self):
        opener = Mock()
        opener.open.side_effect = c.error.HTTPError('https://api.github.com/', 403,
                                                   'private fake-token', {}, None)
        store = c.GitHubState('owner/repo', 'fake-token', 'a' * 40)
        with patch.object(c.request, 'build_opener', return_value=opener):
            with self.assertRaises(c.StateError) as caught:
                store.load()
        self.assertIn('403', str(caught.exception))
        self.assertNotIn('fake-token', str(caught.exception))


class SummaryTests(unittest.TestCase):
    def test_baseline_summary_confirms_read_without_implying_product_alert(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'summary.md'
            with patch.dict(c.os.environ, {'GITHUB_STEP_SUMMARY': str(path)}), \
                    patch.object(m, 'collect', return_value=({'123': PRODUCT}, 1)), \
                    patch.object(m, 'notify') as sender, patch('builtins.print'):
                self.assertEqual(c.one_cycle(CONFIG, MemoryStore()), 'ok')
            content = path.read_text(encoding='utf-8')
        sender.assert_not_called()
        self.assertIn('Catalogue vérifié', content)
        self.assertIn('Fiches suivies : **1** sur **1** pages', content)
        self.assertIn('Nouveaux événements : **0**', content)
        self.assertIn('(Paris)', content)
        self.assertNotIn(CONFIG['ntfy_topic'], content)

    def test_pause_and_failed_collection_never_claim_a_successful_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'summary.md'
            with patch.dict(c.os.environ, {'GITHUB_STEP_SUMMARY': str(path)}), \
                    patch('builtins.print'), patch.object(m, 'collect') as reader:
                store = MemoryStore({'initialized': True, 'products': {},
                                     'paused_reason': 'refus', 'pending': []})
                self.assertEqual(c.one_cycle(CONFIG, store), 'paused')
                reader.assert_not_called()
                content = path.read_text(encoding='utf-8')
                self.assertIn('EN PAUSE', content)
                self.assertNotIn('Catalogue vérifié', content)
                path.write_text('', encoding='utf-8')
                reader.side_effect = m.WatchError('panne')
                self.assertEqual(c.one_cycle(CONFIG, MemoryStore()), 'error')
            content = path.read_text(encoding='utf-8')
        self.assertIn('PASSAGE INCOMPLET', content)
        self.assertNotIn('Catalogue vérifié', content)
        self.assertNotIn('Nouveaux événements : **0**', content)

    def test_summary_write_failure_does_not_fail_cycle_or_modify_history(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(c.os.environ, {'GITHUB_STEP_SUMMARY': folder}), \
                    patch.object(m, 'collect', return_value=({'123': PRODUCT}, 1)), \
                    patch.object(m, 'notify') as sender, patch('builtins.print'):
                store = MemoryStore()
                self.assertEqual(c.one_cycle(CONFIG, store), 'ok')
        sender.assert_not_called()
        self.assertEqual(store.state['products']['123']['id'], '123')


if __name__ == '__main__':
    unittest.main()
