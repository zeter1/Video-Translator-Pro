import threading
import unittest
from unittest import mock

import videotranslator.public_api as vt
from videotranslator.pipeline.translation import TranslationMixin
from videotranslator.network.guard import ReusableGoogleTranslator
from videotranslator.network.translation_circuit import SharedTranslationCircuit, TranslationProviderCoolingDownError


class TranslationRateLimitTests(unittest.TestCase):
    def make_translator(self, side_effect):
        translator = TranslationMixin()
        translator._check_cancel = mock.Mock()
        translator._sleep_or_cancel = mock.Mock()
        translator._problem = mock.Mock()
        translator.log = mock.Mock()
        translator.target_info = {"code": "ru"}
        translator.translation_network_guard = mock.Mock()
        translator.translation_network_guard.record_failure.return_value = True
        translator.translation_network_guard.before_request.return_value = None
        translator._close_translation_client = mock.Mock()
        client = mock.Mock()
        client.translate.side_effect = side_effect
        translator._get_translation_client = lambda: client
        return translator, client

    def test_rate_limit_batch_does_not_fan_out_to_individual_requests(self):
        exc = RuntimeError(
            "Server Error: You made too many requests to the server. "
            "According to google, you are allowed to make 5 requests per second"
        )
        translator, client = self.make_translator(exc)
        records = [(i, {"source": f"text {i}"}) for i in range(1, 9)]

        results, failures = translator.translate_records_batch(records, attempts=1)

        self.assertEqual(results, {})
        self.assertEqual(len(failures), 8)
        # One failed batch request only. Old behavior made 1 + 8 individual calls.
        self.assertEqual(client.translate.call_count, 1)
        events = [call.args[0] for call in translator._problem.call_args_list]
        self.assertIn("translation_batch_deferred_provider_unavailable", events)

    def test_rate_limit_resets_http_session_and_uses_longer_retry_delay(self):
        exc = RuntimeError("429 Too Many Requests rate limit")
        translator, client = self.make_translator([exc, "Готово"])

        result = translator.translate_segment("Hello", index=4, attempts=2)

        self.assertEqual(result, "Готово")
        self.assertEqual(client.translate.call_count, 2)
        translator._close_translation_client.assert_called_once()
        waited = translator._sleep_or_cancel.call_args_list[0].args[0]
        self.assertGreaterEqual(waited, 8.0)
        events = [call.args[0] for call in translator._problem.call_args_list]
        self.assertIn("translation_rate_limit_detected", events)

    def test_public_constants_expose_safe_translation_policy(self):
        self.assertGreater(vt.TRANSLATION_MIN_REQUEST_INTERVAL_SEC, 0)
        self.assertGreaterEqual(vt.TRANSLATION_RATE_LIMIT_COOLDOWN_SEC, 30)
        self.assertGreaterEqual(vt.TRANSLATION_PROVIDER_FAILURE_STREAK_LIMIT, 2)

    def test_shared_circuit_rate_limit_skips_impossible_eight_second_retry(self):
        exc = RuntimeError("429 Too Many Requests rate limit")
        translator, client = self.make_translator(exc)
        translator.shared_translation_circuit = SharedTranslationCircuit()

        with self.assertRaises(TranslationProviderCoolingDownError):
            translator._translate_google_segment("Hello", index=7, attempts=2)

        self.assertEqual(1, client.translate.call_count)
        translator._sleep_or_cancel.assert_not_called()
        self.assertGreater(translator.shared_translation_circuit.remaining(), 0)
        rate_events = [
            call for call in translator._problem.call_args_list
            if call.args and call.args[0] == "translation_rate_limit_detected"
        ]
        self.assertEqual(1, len(rate_events))
        self.assertGreaterEqual(rate_events[0].kwargs["cooldown_hint_sec"], vt.TRANSLATION_RATE_LIMIT_COOLDOWN_SEC)

    def test_shared_circuit_increases_repeated_rate_limit_cooldown_and_resets_after_success(self):
        now = [100.0]
        circuit = SharedTranslationCircuit(clock=lambda: now[0])

        first = circuit.mark_failure("rate_limit", 60)
        now[0] = circuit.cooldown_until
        second = circuit.mark_failure("rate_limit", 60)
        now[0] = circuit.cooldown_until
        third = circuit.mark_failure("rate_limit", 60)

        self.assertEqual(60.0, first)
        self.assertEqual(120.0, second)
        self.assertEqual(240.0, third)
        circuit.mark_success()
        self.assertEqual(0, circuit.failures)
        self.assertEqual(0, circuit.failure_streak)
        self.assertEqual(0.0, circuit.remaining())


class TranslationEndpointFailoverTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, payload=None, error=None):
            self._payload = payload
            self._error = error

        def raise_for_status(self):
            if self._error:
                raise self._error

        def json(self):
            return self._payload

    @staticmethod
    def make_client(response, mobile_result="мобильный перевод"):
        client = ReusableGoogleTranslator.__new__(ReusableGoogleTranslator)
        client.target = "ru"
        client._pace_lock = threading.Lock()
        client._last_request_at = 0.0
        client._min_request_interval = 0.0
        client._preferred_backend = ReusableGoogleTranslator.BACKEND_JSON
        client._route_event = None
        client.session = mock.Mock()
        client.session.get.return_value = response
        client.translator = mock.Mock()
        client.translator.translate.return_value = mobile_result
        client.google_module = None
        client.original_requests = None
        client.proxy = None
        return client

    def test_json_endpoint_is_primary_and_parses_all_chunks(self):
        response = self.FakeResponse(payload=[[['Привет ', 'Hello ', None], ['мир', 'world', None]], None, 'en'])
        client = self.make_client(response)

        result = client.translate("Hello world")

        self.assertEqual(result, "Привет мир")
        client.translator.translate.assert_not_called()
        self.assertEqual(client.active_backend, ReusableGoogleTranslator.BACKEND_JSON)
        self.assertIsNone(client.consume_route_event())

    def test_json_rate_limit_switches_to_mobile_without_outer_failure(self):
        response = self.FakeResponse(error=RuntimeError("429 Too Many Requests"))
        client = self.make_client(response, mobile_result="Резервный перевод")

        result = client.translate("Hello")

        self.assertEqual(result, "Резервный перевод")
        self.assertEqual(client.active_backend, ReusableGoogleTranslator.BACKEND_MOBILE)
        route = client.consume_route_event()
        self.assertEqual(route["from"], ReusableGoogleTranslator.BACKEND_JSON)
        self.assertEqual(route["to"], ReusableGoogleTranslator.BACKEND_MOBILE)
        self.assertEqual(route["reason"], "rate_limit")

    def test_both_endpoints_failed_preserves_rate_limit_category(self):
        response = self.FakeResponse(error=RuntimeError("429 Too Many Requests"))
        client = self.make_client(response)
        client.translator.translate.side_effect = RuntimeError("Server Error: too many requests")

        with self.assertRaises(RuntimeError) as ctx:
            client.translate("Hello")

        self.assertIn("Все маршруты Google Translate недоступны", str(ctx.exception))
        self.assertIn("429", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
