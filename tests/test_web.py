# SPDX-License-Identifier: Unlicense
"""overpressure.web: the TLS context, a short outage tried again, and failures that say why."""

from __future__ import annotations

import builtins
import http.client
import io
import socket
import ssl
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest import mock

from overpressure import web

URL = "https://github.com/overpressure/linux/releases/download/phoenix%2F7.2-6/Image.gz"


def http_error(code: int, reason: str = "") -> urllib.error.HTTPError:
    """Return the error urllib raises for an answer of @p code."""
    return urllib.error.HTTPError(URL, code, reason, None, io.BytesIO(b""))  # ty: ignore[invalid-argument-type]  # no headers in a test


def untrusted() -> urllib.error.URLError:
    """Return the error of a certificate whose issuer this computer does not know (the reporter's)."""
    error = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    error.verify_message = "unable to get issuer certificate"
    return urllib.error.URLError(error)


class Clock:
    """A clock that moves only when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []
        self.lines: list[str] = []

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds

    def retry(self, seconds: float = web.RETRY_SECONDS) -> web.Retry:
        return web.Retry(seconds, self.lines.append, self.sleep, lambda: self.now)


class Opener:
    """Answers each try with the next of @p outcomes: an exception to raise, or a body."""

    def __init__(self, *outcomes: BaseException | bytes) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, _request: Any, timeout: float) -> io.BytesIO:  # noqa: ANN401, ARG002  # a URL or a Request; urlopen's keyword
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return io.BytesIO(outcome)


class RetryTests(unittest.TestCase):
    def test_a_short_outage_is_tried_again_and_logged(self):
        clock = Clock()
        opener = Opener(http_error(503, "Service Unavailable"), untrusted(), b"the kernel")
        self.assertEqual(web.read(URL, 60, clock.retry(), opener), b"the kernel")
        self.assertEqual((opener.calls, clock.waits), (3, [2, 4]))
        self.assertEqual(
            clock.lines,
            [
                f"   {URL}: HTTP 503 Service Unavailable, again in 2 s",
                f"   {URL}: its TLS certificate is not trusted (unable to get issuer certificate), again in 4 s",
            ],
        )

    def test_a_body_that_breaks_off_is_read_again(self):
        class Broken(io.BytesIO):
            def read(self, *_: object) -> bytes:
                partial = b"the ker"
                raise http.client.IncompleteRead(partial, 3)

        bodies = [Broken(), io.BytesIO(b"the kernel")]
        clock = Clock()
        self.assertEqual(web.read(URL, 60, clock.retry(), lambda *_, **__: bodies.pop(0)), b"the kernel")
        self.assertEqual(len(clock.lines), 1)

    def test_an_opener_shaped_like_urlopen_gets_the_timeout_by_keyword(self):
        # A headset passes urllib.request.urlopen (its second parameter is data): the timeout must not land there.
        asked = []

        def urlopen(url: str, data: bytes | None = None, timeout: float | None = None) -> io.BytesIO:
            asked.append((url, data, timeout))
            return io.BytesIO(b"the kernel")

        self.assertEqual(web.read(URL, 60, Clock().retry(), urlopen), b"the kernel")
        with web.urlopen(URL, 30, Clock().retry(), urlopen) as response:
            self.assertEqual(response.read(), b"the kernel")
        self.assertEqual(asked, [(URL, None, 60), (URL, None, 30)])

    def test_an_answer_a_try_again_cannot_change_fails_at_once(self):
        clock = Clock()
        with self.assertRaises(web.DownloadError) as caught:
            web.read(URL, 60, clock.retry(), Opener(http_error(404, "Not Found")))
        self.assertEqual((str(caught.exception), caught.exception.status), (f"{URL}: HTTP 404 Not Found", 404))
        self.assertEqual(clock.waits, [])
        self.assertIsInstance(caught.exception, OSError)  # callers that catch OSError keep working

    def test_an_outage_that_lasts_is_given_up_with_its_reason(self):
        clock = Clock()
        opener = Opener(*[untrusted()] * 20)
        with self.assertRaisesRegex(web.DownloadError, "not trusted \\(unable to get issuer certificate\\)"):
            web.urlopen(URL, 60, clock.retry(60), opener)
        self.assertEqual(clock.waits, [2, 4, 8, 16, 30])
        self.assertLessEqual(sum(clock.waits), 60)

    def test_the_reasons_say_what_failed(self):
        self.assertEqual(web.describe(urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))),
                         "the name does not resolve (getaddrinfo failed)")  # fmt: skip
        self.assertEqual(web.describe(urllib.error.URLError(socket.timeout("timed out"))), "no answer in time")
        self.assertEqual(web.describe(ConnectionRefusedError(10061, "refused")), "the connection was refused")
        dropped = web.describe(ConnectionResetError(10054, "reset"))
        self.assertEqual(dropped, "the connection dropped ([Errno 10054] reset)")
        self.assertTrue(web.transient(ConnectionResetError()))
        self.assertTrue(web.transient(http_error(429)))
        self.assertFalse(web.transient(http_error(403)))


class ContextTests(unittest.TestCase):
    def setUp(self):
        web.context.cache_clear()
        self.addCleanup(web.context.cache_clear)

    def test_without_truststore_python_verifies(self):
        real = builtins.__import__

        def no_truststore(name: str, *args: object, **kwargs: object) -> object:
            if name == "truststore":
                raise ImportError(name)
            return real(name, *args, **kwargs)  # ty: ignore[invalid-argument-type]  # import's own arguments

        with mock.patch("builtins.__import__", no_truststore):
            context = web.context()
        self.assertIs(type(context), ssl.SSLContext)
        self.assertEqual((context.verify_mode, context.check_hostname), (ssl.CERT_REQUIRED, True))
        self.assertFalse(web.system_verifier())

    def test_with_truststore_the_system_verifies(self):
        try:
            import truststore  # noqa: PLC0415  # only where it is installed (Python 3.10+)
        except ImportError:
            self.skipTest("truststore is not installed (Python 3.9)")
        context = web.context()
        self.assertIsInstance(context, truststore.SSLContext)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(web.system_verifier())

    def test_only_http_and_https(self):
        with self.assertRaisesRegex(ValueError, "not an http"):
            web.open_once("file:///etc/passwd")


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        self.send_response(200 if self.path == "/ok" else 404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002  # http.server's signature
        """Keep the test's output quiet."""


class AnswersTests(unittest.TestCase):
    def test_any_status_is_an_answer_and_no_answer_says_why(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        self.assertEqual(web.answers(base + "/ok", 5), "")
        self.assertEqual(web.answers(base + "/missing", 5), "")
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        self.assertEqual(web.answers(f"http://127.0.0.1:{port}/", 5), "the connection was refused")


if __name__ == "__main__":
    unittest.main()
