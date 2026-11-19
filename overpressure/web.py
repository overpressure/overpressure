# SPDX-License-Identifier: Unlicense
"""HTTP(S) for every download: one TLS setup, a short outage tried again, and a failure that says why.

Python's ssl on Windows trusts only the roots already in Windows' certificate store, while Windows itself
fetches a root it lacks the first time it needs one. GitHub's hosts end at roots a computer may not have
fetched yet (ISRG Root YR under ISRG Root X1, Sectigo's E46 under USERTrust ECC): a download then failed with
"unable to get issuer certificate" although the browser loaded the same page. truststore verifies with the
system's own verifier, as pip does: CryptoAPI on Windows, which fetches the missing root; the Security framework
on macOS; OpenSSL with the system's roots on Linux. Without it (Python 3.9, or a Python without the package)
the standard library's default context verifies. Every certificate is verified either way.
"""

from __future__ import annotations

import functools
import http.client
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import IO, Callable, TypeVar, cast

T = TypeVar("T")
USER_AGENT = "overpressure"
#: How long a short outage is tried again before a download fails, and the waits between the tries.
RETRY_SECONDS = 120
FIRST_WAIT = 2
LONGEST_WAIT = 30
#: Answers that a later try may not get: a timeout, too many requests, the server's own trouble.
TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})

Opener = Callable[..., IO[bytes]]
#: The request type, so that a caller needs no urllib of its own.
Request = urllib.request.Request


class DownloadError(OSError):
    """A request that failed for good: the message names its URL and why; status is the HTTP status, if any."""

    def __init__(self, message: str, status: int | None = None) -> None:
        """Keep @p message and the HTTP @p status (None when no answer came)."""
        super().__init__(message)
        self.status = status


@functools.cache
def context() -> ssl.SSLContext:
    """Return the TLS context of every request: the system's verifier through truststore, else Python's."""
    try:
        import truststore  # noqa: PLC0415  # pinned for Python 3.10+; Python 3.9 verifies with the standard library
    except ImportError:
        return ssl.create_default_context()
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def system_verifier() -> bool:
    """Tell whether TLS is verified by the system's own verifier (truststore), for the dependency check."""
    return type(context()) is not ssl.SSLContext  # truststore's context is a subclass


def _request(request: str | urllib.request.Request) -> urllib.request.Request:
    if isinstance(request, str):
        request = urllib.request.Request(request)  # noqa: S310  # its scheme is checked below
    if request.type not in ("http", "https"):
        msg = f"not an http(s) URL: {request.full_url}"
        raise ValueError(msg)
    if not request.has_header("User-agent"):
        request.add_header("User-Agent", USER_AGENT)
    return request


def open_once(request: str | urllib.request.Request, timeout: float = 60) -> http.client.HTTPResponse:
    """Open @p request (a URL or a Request, http or https) once, TLS verified by context(); urllib's errors."""
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context()))
    return cast("http.client.HTTPResponse", opener.open(_request(request), timeout=timeout))


def describe(exc: BaseException) -> str:  # noqa: PLR0911  # one answer per kind of failure
    """Return in a few words why a request failed: the HTTP status, TLS, the name, a timeout, the connection."""
    if isinstance(exc, DownloadError):
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} {exc.reason}".rstrip()
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLCertVerificationError):
        return f"its TLS certificate is not trusted ({reason.verify_message})"
    if isinstance(reason, ssl.SSLError):
        return f"TLS failed ({reason.reason or reason})"
    if isinstance(reason, socket.gaierror):
        return f"the name does not resolve ({reason.strerror or reason})"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "no answer in time"
    if isinstance(reason, ConnectionRefusedError):
        return "the connection was refused"
    if isinstance(reason, (ConnectionError, http.client.HTTPException)):
        return f"the connection dropped ({reason})"
    return str(reason)


def transient(exc: BaseException) -> bool:
    """Tell whether a later try may succeed: any failure of the connection, or an answer of TRANSIENT_STATUS."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in TRANSIENT_STATUS
    return isinstance(exc, (OSError, http.client.HTTPException))


@dataclass(frozen=True)
class Retry:
    """How a request is tried again: for @p seconds, each try again logged; the clock is the tests'."""

    seconds: float = RETRY_SECONDS
    log: Callable[[str], None] = print
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


#: The default: two minutes, logged to the output.
RETRY = Retry()


def _retrying(url: str, action: Callable[[], T], retry: Retry) -> T:
    """Run @p action, trying a transient failure again (2 s apart at first, up to 30 s) until retry.seconds."""
    end = retry.clock() + retry.seconds
    wait = FIRST_WAIT
    while True:
        try:
            return action()
        except (OSError, http.client.HTTPException) as exc:  # noqa: PERF203  # a retry loop; urllib's errors are OSErrors
            why = describe(exc)
            if not transient(exc) or retry.clock() + wait > end:
                status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
                msg = f"{url}: {why}"
                raise DownloadError(msg, status) from exc
            retry.log(f"   {url}: {why}, again in {wait} s")
            retry.sleep(wait)
            wait = min(wait * 2, LONGEST_WAIT)


def _url(request: str | urllib.request.Request) -> str:
    return request if isinstance(request, str) else request.full_url


def urlopen(
    request: str | urllib.request.Request, timeout: float = 60, retry: Retry = RETRY, opener: Opener = open_once
) -> IO[bytes]:
    """Open @p request, a transient failure tried again (Retry); a failure for good raises DownloadError.

    For a response that is read in parts; read() also tries a body that breaks off again.
    """
    return _retrying(_url(request), lambda: opener(request, timeout=timeout), retry)


def read(
    request: str | urllib.request.Request, timeout: float = 60, retry: Retry = RETRY, opener: Opener = open_once
) -> bytes:
    """Return the body of @p request, a transient failure (also of the body) tried again; else DownloadError."""

    def once() -> bytes:
        with opener(request, timeout=timeout) as response:
            return response.read()

    return _retrying(_url(request), once, retry)


def answers(url: str, timeout: float = 15) -> str:
    """Return "" when this computer gets an answer from @p url (any HTTP status is one), else why not."""
    request = urllib.request.Request(url, method="HEAD")  # noqa: S310  # http(s) only, checked in open_once
    try:
        open_once(request, timeout).close()
    except urllib.error.HTTPError:
        return ""
    except (OSError, http.client.HTTPException) as exc:
        return describe(exc)
    return ""
