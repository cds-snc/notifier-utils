from flask.wrappers import Request
from flask import request, current_app, abort


class NotifyRequest(Request):
    """
    A custom Request class, implementing extraction of zipkin headers used to trace request through cloudfoundry
    as described here: https://docs.cloudfoundry.org/concepts/http-routing.html#zipkin-headers
    """

    @property
    def request_id(self):
        return self.trace_id

    @property
    def trace_id(self):
        """
        The "trace id" (in zipkin terms) assigned to this request, if present (None otherwise)
        """
        if not hasattr(self, "_trace_id"):
            self._trace_id = self._get_header_value(current_app.config["NOTIFY_TRACE_ID_HEADER"])
        return self._trace_id

    @property
    def span_id(self):
        """
        The "span id" (in zipkin terms) set in this request's header, if present (None otherwise)
        """
        if not hasattr(self, "_span_id"):
            # note how we don't generate an id of our own. not being supplied a span id implies that we are running in
            # an environment with no span-id-aware request router, and thus would have no intermediary to prevent the
            # propagation of our span id all the way through all our onwards requests much like trace id. and the point
            # of span id is to assign identifiers to each individual request.
            self._span_id = self._get_header_value(current_app.config["NOTIFY_SPAN_ID_HEADER"])
        return self._span_id

    @property
    def parent_span_id(self):
        """
        The "parent span id" (in zipkin terms) set in this request's header, if present (None otherwise)
        """
        if not hasattr(self, "_parent_span_id"):
            self._parent_span_id = self._get_header_value(current_app.config["NOTIFY_PARENT_SPAN_ID_HEADER"])
        return self._parent_span_id

    def _get_header_value(self, header_name):
        """
        Returns value of the given header
        """
        if header_name in self.headers and self.headers[header_name]:
            return self.headers[header_name]

        return None


class ResponseHeaderMiddleware(object):
    def __init__(self, app, trace_id_header, span_id_header):
        self.app = app
        self.trace_id_header = trace_id_header
        self.span_id_header = span_id_header

    def __call__(self, environ, start_response):
        def rewrite_response_headers(status, headers, exc_info=None):
            lower_existing_header_names = frozenset(name.lower() for name, value in headers)

            if self.trace_id_header not in lower_existing_header_names:
                headers.append((self.trace_id_header, str(request.trace_id)))  # type: ignore

            if self.span_id_header not in lower_existing_header_names:
                headers.append((self.span_id_header, str(request.span_id)))  # type: ignore

            return start_response(status, headers, exc_info)

        return self.app(environ, rewrite_response_headers)


def init_app(app):
    app.config.setdefault("NOTIFY_TRACE_ID_HEADER", "X-B3-TraceId")
    app.config.setdefault("NOTIFY_SPAN_ID_HEADER", "X-B3-SpanId")
    app.config.setdefault("NOTIFY_PARENT_SPAN_ID_HEADER", "X-B3-ParentSpanId")

    app.request_class = NotifyRequest
    app.wsgi_app = ResponseHeaderMiddleware(
        app.wsgi_app,
        app.config["NOTIFY_TRACE_ID_HEADER"],
        app.config["NOTIFY_SPAN_ID_HEADER"],
    )


def check_proxy_header_before_request():
    keys = [
        current_app.config.get("ROUTE_SECRET_KEY_1"),
        current_app.config.get("ROUTE_SECRET_KEY_2"),
    ]
    result, msg = _check_proxy_header_secret(request, keys)

    if not result:
        if current_app.config.get("CHECK_PROXY_HEADER", False):
            current_app.logger.warning(msg)
            abort(403)

    # We need to return None to continue processing the request
    # http://flask.pocoo.org/docs/0.12/api/#flask.Flask.before_request
    return None


def _check_proxy_header_secret(request, secrets, header="X-Custom-Forwarder"):
    if header not in request.headers:
        return False, "Header missing"

    header_secret = request.headers.get(header)
    if not header_secret:
        return False, "Header exists but is empty"

    # if there isn't any non-empty secret configured we fail closed
    if not any(secrets):
        return False, "Secrets are not configured"

    for i, secret in enumerate(secrets):
        if header_secret == secret:
            return True, "Key used: {}".format(i + 1)  # add 1 to make it human-compatible

    return False, "Header didn't match any keys"
