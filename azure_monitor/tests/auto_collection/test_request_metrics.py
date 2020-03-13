# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

import unittest
from http.server import HTTPServer
from unittest import mock

import requests
from opentelemetry import metrics
from opentelemetry.sdk.metrics import Gauge, Meter

from azure_monitor.auto_collection import RequestMetrics, request_metrics

ORIGINAL_FUNCTION = requests.Session.request
ORIGINAL_CONS = HTTPServer.__init__


# pylint: disable=protected-access
class TestRequestMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._meter_defaults = (metrics._METER, metrics._METER_FACTORY)
        metrics.set_preferred_meter_implementation(lambda _: Meter())
        cls._meter = metrics.meter()
        kvp = {"environment": "staging"}
        cls._test_label_set = cls._meter.get_label_set(kvp)

    @classmethod
    def tearDown(cls):
        metrics._METER, metrics._METER_FACTORY = cls._meter_defaults

    def setUp(self):
        request_metrics.requests_map.clear()
        requests.Session.request = ORIGINAL_FUNCTION
        request_metrics.ORIGINAL_CONSTRUCTOR = ORIGINAL_CONS

    def test_constructor(self):
        mock_meter = mock.Mock()
        request_metrics_collector = RequestMetrics(
            meter=mock_meter, label_set=self._test_label_set
        )
        self.assertEqual(request_metrics_collector._meter, mock_meter)
        self.assertEqual(
            request_metrics_collector._label_set, self._test_label_set
        )

        self.assertEqual(mock_meter.create_metric.call_count, 2)

        create_metric_calls = mock_meter.create_metric.call_args_list

        self.assertEqual(
            create_metric_calls[0][0],
            (
                "\\ASP.NET Applications(??APP_W3SVC_PROC??)\\Request Execution Time",
                "Incoming Requests Average Execution Time",
                "milliseconds",
                int,
                Gauge,
            ),
        )

        self.assertEqual(
            create_metric_calls[1][0],
            (
                "\\ASP.NET Applications(??APP_W3SVC_PROC??)\\Requests/Sec",
                "Incoming Requests Average Execution Rate",
                "rps",
                int,
                Gauge,
            ),
        )

    def test_track(self):
        mock_meter = mock.Mock()
        request_metrics_collector = RequestMetrics(
            meter=mock_meter, label_set=self._test_label_set
        )
        duration_mock = mock.Mock()
        rate_mock = mock.Mock()
        request_metrics_collector._track_request_duration = duration_mock
        request_metrics_collector._track_request_rate = rate_mock
        request_metrics_collector.track()
        self.assertEqual(duration_mock.call_count, 1)
        self.assertEqual(rate_mock.call_count, 1)

    def test_track_request_duration(self):
        request_metrics_collector = RequestMetrics(
            meter=self._meter, label_set=self._test_label_set
        )
        request_metrics.requests_map["duration"] = 0.1
        request_metrics.requests_map["count"] = 10
        request_metrics.requests_map["last_count"] = 5
        request_metrics_collector._track_request_duration()
        self.assertEqual(
            request_metrics_collector._request_duration_handle.aggregator.current,
            20,
        )

    def test_track_request_duration_error(self):
        request_metrics_collector = RequestMetrics(
            meter=self._meter, label_set=self._test_label_set
        )
        request_metrics.requests_map["duration"] = 0.1
        request_metrics.requests_map["count"] = 10
        request_metrics.requests_map["last_count"] = 10
        request_metrics_collector._track_request_duration()
        self.assertEqual(
            request_metrics_collector._request_duration_handle.aggregator.current,
            0,
        )

    @mock.patch("azure_monitor.auto_collection.request_metrics.time")
    def test_track_request_rate(self, time_mock):
        request_metrics_collector = RequestMetrics(
            meter=self._meter, label_set=self._test_label_set
        )
        time_mock.time.return_value = 100
        request_metrics.requests_map["last_time"] = 98
        request_metrics.requests_map["count"] = 4
        request_metrics_collector._track_request_rate()
        self.assertEqual(
            request_metrics_collector._request_rate_handle.aggregator.current,
            2,
        )

    @mock.patch("azure_monitor.auto_collection.request_metrics.time")
    def test_track_request_rate_time_none(self, time_mock):
        time_mock.time.return_value = 100
        request_metrics_collector = RequestMetrics(
            meter=self._meter, label_set=self._test_label_set
        )
        request_metrics.requests_map["last_time"] = None
        request_metrics_collector._track_request_rate()
        self.assertEqual(
            request_metrics_collector._request_rate_handle.aggregator.current,
            0,
        )

    @mock.patch("azure_monitor.auto_collection.request_metrics.time")
    def test_track_request_rate_error(self, time_mock):
        request_metrics_collector = RequestMetrics(
            meter=self._meter, label_set=self._test_label_set
        )
        time_mock.time.return_value = 100
        request_metrics.requests_map["last_rate"] = 5
        request_metrics.requests_map["last_time"] = 100
        request_metrics_collector._track_request_rate()
        self.assertEqual(
            request_metrics_collector._request_rate_handle.aggregator.current,
            5,
        )

    def test_request_patch(self):
        map = request_metrics.requests_map  # pylint: disable=redefined-builtin
        func = mock.Mock()
        new_func = request_metrics.request_patch(func)
        new_func()

        self.assertEqual(map["count"], 1)
        self.assertIsNotNone(map["duration"])
        self.assertEqual(len(func.call_args_list), 1)

    def test_server_patch(self):
        request_metrics.ORIGINAL_CONSTRUCTOR = lambda x, y, z: None
        with mock.patch(
            "azure_monitor.auto_collection.request_metrics.request_patch"
        ) as request_mock:
            handler = mock.Mock()
            handler.do_DELETE.return_value = None
            handler.do_GET.return_value = None
            handler.do_HEAD.return_value = None
            handler.do_OPTIONS.return_value = None
            handler.do_POST.return_value = None
            handler.do_PUT.return_value = None
            result = request_metrics.server_patch(None, None, handler)
            handler.do_DELETE()
            handler.do_GET()
            handler.do_HEAD()
            handler.do_OPTIONS()
            handler.do_POST()
            handler.do_PUT()

            self.assertEqual(result, None)
            self.assertEqual(len(request_mock.call_args_list), 6)

    def test_server_patch_no_methods(self):
        request_metrics.ORIGINAL_CONSTRUCTOR = lambda x, y, z: None
        with mock.patch(
            "azure_monitor.auto_collection.request_metrics.request_patch"
        ) as request_mock:
            handler = mock.Mock()
            result = request_metrics.server_patch(None, None, handler)
            handler.do_DELETE()
            handler.do_GET()
            handler.do_HEAD()
            handler.do_OPTIONS()
            handler.do_POST()
            handler.do_PUT()

            self.assertEqual(result, None)
            self.assertEqual(len(request_mock.call_args_list), 0)

    def test_server_patch_no_args(self):
        request_metrics.ORIGINAL_CONSTRUCTOR = lambda x, y: None
        req = request_metrics.server_patch(None, None)

        self.assertEqual(req, None)

    def test_server_patch_no_handler(self):
        request_metrics.ORIGINAL_CONSTRUCTOR = lambda x, y, z: None
        req = request_metrics.server_patch(None, None, None)
        self.assertEqual(req, None)