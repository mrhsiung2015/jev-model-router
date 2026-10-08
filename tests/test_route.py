"""Offline behavior tests; no live API or real credentials."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
import urllib.error
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'route.py'
spec = importlib.util.spec_from_file_location('route', SCRIPT)
route = importlib.util.module_from_spec(spec)
spec.loader.exec_module(route)


class CleanEnvironment(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)


class PolicyTests(CleanEnvironment):
    def test_default_tiers(self):
        os.environ['JEV_ROUTER_ALLOW_LONG'] = '1'
        for tier, pair in {'fast': ('gpt-6-luna', 'low'), 'balanced': ('gpt-6-luna', 'medium'),
                           'strong': ('gpt-6.1-sol', 'high'), 'long': ('gpt-6-astra', 'max')}.items():
            with self.subTest(tier=tier):
                result = route.apply_policy({}, tier, .9, None)
                self.assertEqual(result['status'], 'routed')
                self.assertEqual((result['model'], result['reasoning_effort']), pair)

    def test_confidence_boundaries(self):
        request = {'current_model': 'gpt-6-sol', 'current_reasoning_effort': 'high'}
        for confidence, status in ((0, 'keep_current'), (.5999, 'keep_current'), (.6, 'keep_current'),
                                   (.7999, 'keep_current'), (.8, 'routed'), (1, 'routed')):
            with self.subTest(confidence=confidence):
                self.assertEqual(route.apply_policy(request, 'fast', confidence, None)['status'], status)

    def test_old_and_new_sol_refuse_medium_confidence_downgrades(self):
        for model in ('gpt-6-sol', 'gpt-6.1-sol'):
            for effort in ('high', '', 'unknown'):
                for tier in ('fast', 'balanced'):
                    with self.subTest(model=model, effort=effort, tier=tier):
                        result = route.apply_policy({'current_model': model, 'current_reasoning_effort': effort}, tier, .7, None)
                        self.assertEqual(result['reason'], 'medium_confidence_no_downgrade')

    def test_new_sol_can_route_light_work_at_high_confidence(self):
        for tier, effort in (('fast', 'low'), ('balanced', 'medium')):
            result = route.apply_policy({'current_model': 'gpt-6.1-sol', 'current_reasoning_effort': 'high'}, tier, .8, None)
            self.assertEqual((result['status'], result['model'], result['reasoning_effort']), ('routed', 'gpt-6-luna', effort))

    def test_legacy_sol_override_preserves_both_current_sol_roles(self):
        os.environ['JEV_CODEX_STRONG_MODEL'] = 'gpt-6-sol'
        result = route.apply_policy({'risk': 'high'}, 'fast', .3, None)
        self.assertEqual((result['model'], result['reasoning_effort']), ('gpt-6-sol', 'high'))
        for model in ('gpt-6-sol', 'gpt-6.1-sol'):
            self.assertEqual(route.current_tier(model, route.models(), 'high'), 'strong')
            self.assertEqual(route.apply_policy({'current_model': model}, 'balanced', .7, None)['reason'], 'medium_confidence_no_downgrade')

    def test_canonical_roles_when_all_targets_are_overridden(self):
        for tier in route.TIERS:
            os.environ[f'JEV_CODEX_{tier.upper()}_MODEL'] = 'gpt-6-luna'
        for model, expected in (('gpt-6-sol', 'strong'), ('gpt-6.1-sol', 'strong'), ('gpt-6-astra', 'long')):
            self.assertEqual(route.current_tier(model, route.models()), expected)
        self.assertIsNone(route.current_tier('external-model', route.models()))

    def test_explicit_targets_take_precedence_over_canonical_roles(self):
        os.environ['JEV_CODEX_FAST_MODEL'] = 'gpt-6.1-sol'
        os.environ['JEV_CODEX_STRONG_MODEL'] = 'gpt-6-sol'
        self.assertEqual(route.current_tier('gpt-6.1-sol', route.models(), 'low'), 'fast')
        self.assertEqual(route.apply_policy({'current_model': 'gpt-6.1-sol', 'current_reasoning_effort': 'low'}, 'balanced', .7, None)['status'], 'routed')

    def test_new_sol_multi_tier_assignment_uses_effort_or_highest_match(self):
        os.environ['JEV_CODEX_FAST_MODEL'] = 'gpt-6.1-sol'
        for effort, expected in (('low', 'fast'), ('high', 'strong'), ('', 'strong'), ('unknown', 'strong')):
            self.assertEqual(route.current_tier('gpt-6.1-sol', route.models(), effort), expected)

    def test_medium_confidence_upgrade_and_same_tier(self):
        request = {'current_model': 'gpt-6-luna', 'current_reasoning_effort': 'low'}
        for tier in ('fast', 'balanced', 'strong'):
            self.assertEqual(route.apply_policy(request, tier, .7, None)['status'], 'routed')

    def test_same_model_effort_downgrade_is_rejected(self):
        for effort in ('medium', '', 'unknown'):
            result = route.apply_policy({'current_model': 'gpt-6-luna', 'current_reasoning_effort': effort}, 'fast', .7, None)
            self.assertEqual(result['reason'], 'medium_confidence_no_downgrade')

    def test_unknown_current_model_cannot_be_compared(self):
        self.assertEqual(route.apply_policy({'current_model': 'external-model'}, 'fast', .7, None)['status'], 'routed')

    def test_high_risk_floor_at_low_and_high_confidence(self):
        for confidence in (0, .59, .6, .8, 1):
            for tier in route.TIERS:
                with self.subTest(confidence=confidence, tier=tier):
                    result = route.apply_policy({'risk': 'high'}, tier, confidence, None)
                    self.assertEqual((result['status'], result['tier']), ('routed', 'strong'))
                    self.assertEqual((result['model'], result['reasoning_effort']), ('gpt-6.1-sol', 'high'))

    def test_long_requires_exact_opt_in(self):
        for value, expected in (('', 'strong'), ('true', 'strong'), ('1', 'long')):
            os.environ['JEV_ROUTER_ALLOW_LONG'] = value
            self.assertEqual(route.apply_policy({}, 'long', .9, None)['tier'], expected)

    def test_invalid_confidence_and_tier_keep_current(self):
        for value in (-.1, 1.1, float('nan'), float('inf')):
            self.assertEqual(route.apply_policy({}, 'strong', value, None)['reason'], 'invalid_confidence')
        self.assertEqual(route.apply_policy({}, 'unrecognized', .9, None)['reason'], 'unknown_tier')

    def test_override_allowlist_and_conservative_ambiguity(self):
        os.environ['JEV_CODEX_FAST_MODEL'] = 'unapproved-model'
        self.assertEqual(route.models()['fast'], 'gpt-6-luna')
        os.environ['JEV_CODEX_STRONG_MODEL'] = 'gpt-6-luna'
        self.assertEqual(route.models()['strong'], 'gpt-6-luna')
        self.assertEqual(route.apply_policy({'current_model': 'gpt-6-luna'}, 'balanced', .7, None)['status'], 'keep_current')


class APITests(CleanEnvironment):
    def setUp(self):
        super().setUp()
        os.environ['TYPESAFE_API_KEY'] = 'test-placeholder-primary'
        opener_patch = patch.object(route.urllib.request, 'build_opener')
        self.opener = opener_patch.start().return_value
        self.addCleanup(opener_patch.stop)

    def response(self, payload):
        self.opener.open.return_value.__enter__.return_value = io.StringIO(json.dumps(payload))

    def emitted(self, request=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as info:
            route.call_jev(request or {'task': 'A minimal summary'}, 1)
        self.assertEqual(info.exception.code, 2)
        self.assertNotIn('test-placeholder', output.getvalue())
        return json.loads(output.getvalue())

    def test_minimal_payload_and_key_priority(self):
        os.environ['JEV_API_KEY'] = 'test-placeholder-secondary'
        self.response({'answers': {'model_tier': {'choice': 'balanced', 'confidence': .9, 'probabilities': {'balanced': .9}}}})
        result = route.call_jev({'task': 'A minimal summary', 'current_model': 'local-only-model',
                                'current_reasoning_effort': 'high', 'private_note': 'local-only-data'}, 3)
        self.assertEqual(result, ('balanced', .9, {'balanced': .9}))
        request = self.opener.open.call_args.args[0]
        self.assertEqual(json.loads(json.loads(request.data)['state']),
                         {'task': 'A minimal summary', 'risk': 'normal', 'expected_scope': 'contained'})
        self.assertEqual(request.full_url, 'https://api.typesafe.ai/v1/systemone')
        self.assertEqual(request.method, 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-placeholder-primary')
        self.assertEqual(self.opener.open.call_args.kwargs['timeout'], 3)
        self.assertIsInstance(route.urllib.request.build_opener.call_args.args[0], route.NoRedirect)

    def test_alias_key_and_pinned_model(self):
        del os.environ['TYPESAFE_API_KEY']
        os.environ['JEV_API_KEY'] = 'test-placeholder-secondary'
        os.environ['JEV_MODEL'] = 'pinned-test-version'
        self.response({'answers': {'model_tier': {'choice': 'strong', 'confidence': .9}}})
        route.call_jev({'task': 'Summary'}, 1)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-placeholder-secondary')
        self.assertEqual(json.loads(request.data)['model'], 'pinned-test-version')

    def test_missing_key_does_not_make_request(self):
        del os.environ['TYPESAFE_API_KEY']
        self.assertEqual(self.emitted()['reason'], 'missing_TYPESAFE_API_KEY_or_JEV_API_KEY')
        self.opener.open.assert_not_called()

    def test_http_error_does_not_echo_server_detail(self):
        self.opener.open.side_effect = urllib.error.HTTPError('https://api.typesafe.ai', 503, 'test-placeholder-private', {}, None)
        self.assertEqual(self.emitted({'task': 'High risk summary', 'risk': 'high'}),
                         {'status': 'error', 'reason': 'jev_http_error', 'http_status': 503})

    def test_network_and_timeout_failures(self):
        cases = ((urllib.error.URLError(socket.gaierror('DNS failure')), 'network_unavailable_or_sandboxed'),
                 (urllib.error.URLError('test-placeholder-private'), 'jev_network_error'),
                 (TimeoutError('test-placeholder-private'), 'jev_timeout_or_io_error'),
                 (OSError('test-placeholder-private'), 'jev_timeout_or_io_error'))
        for error, reason in cases:
            with self.subTest(reason=reason):
                self.opener.open.side_effect = error
                self.assertEqual(self.emitted()['reason'], reason)

    def test_invalid_json(self):
        self.opener.open.return_value.__enter__.return_value = io.StringIO('not-json')
        self.assertEqual(self.emitted()['reason'], 'invalid_jev_json')

    def test_malformed_schema(self):
        answers = ([], None, {}, {'choice': [], 'confidence': .9}, {'choice': 'fast', 'confidence': True},
                   {'choice': 'fast', 'confidence': '0.9'}, {'choice': 'fast', 'confidence': .9, 'probabilities': ['bad']},
                   {'choice': 'fast', 'confidence': .9, 'probabilities': {'fast': float('nan')}},
                   {'choice': 'fast', 'confidence': .9, 'probabilities': {'unexpected': .9}})
        for answer in answers:
            with self.subTest(answer=answer):
                self.response({'answers': {'model_tier': answer}})
                self.assertEqual(self.emitted()['reason'], 'invalid_jev_response')
        for response in ([], None, {}, {'answers': []}):
            self.response(response)
            self.assertEqual(self.emitted()['reason'], 'invalid_jev_response')

    def test_redirect_is_refused(self):
        request = route.urllib.request.Request('https://api.typesafe.ai/v1/systemone', headers={'Authorization': 'Bearer test-placeholder-primary'})
        with self.assertRaises(urllib.error.HTTPError):
            route.NoRedirect().redirect_request(request, None, 302, 'Found', {}, 'https://other.invalid')


class CLITests(CleanEnvironment):
    def run_cli(self, payload, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], input=payload,
                              text=True, capture_output=True, timeout=5)

    def test_offline_mode_requires_no_key(self):
        result = self.run_cli('{"task":"Summary"}', '--offline-choice', 'balanced', '--offline-confidence', '.92')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'routed')

    def test_low_confidence_is_successful_keep_current(self):
        result = self.run_cli('{"task":"Summary"}', '--offline-choice', 'fast', '--offline-confidence', '.4')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['status'], 'keep_current')

    def test_invalid_inputs_fail_without_traceback(self):
        for payload in ('not-json', '[]', '{}', '{"task":"  "}', '{"task":"Summary","risk":"HIGH"}',
                        '{"task":"Summary","expected_scope":[]}', '{"task":"Summary","current_model":3}',
                        '{"task":"Summary","current_reasoning_effort":null}'):
            with self.subTest(payload=payload):
                result = self.run_cli(payload, '--offline-choice', 'fast', '--offline-confidence', '.9')
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)['status'], 'error')
                self.assertNotIn('Traceback', result.stderr)

    def test_offline_confidence_required(self):
        result = self.run_cli('{"task":"Summary"}', '--offline-choice', 'fast')
        self.assertEqual(json.loads(result.stdout)['reason'], 'offline_confidence_required')
        self.assertEqual(result.returncode, 2)

    def test_timeout_validation(self):
        for value in ('0', '-1', 'nan', 'inf'):
            result = self.run_cli('{"task":"Summary"}', '--timeout', value)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['reason'], 'timeout_must_be_positive_and_finite')

    def test_cli_missing_key_is_structured_error(self):
        result = self.run_cli('{"task":"Summary"}')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['status'], 'error')


if __name__ == '__main__':
    unittest.main()
