from eventlet.green import httplib

from swift_zipkin import api


__org_endheaders__ = httplib.HTTPConnection.endheaders
__org_begin__ = httplib.HTTPResponse.begin


def _patched_endheaders(self):
    if api.is_tracing():
        span_ctx = api.ezipkin_client_span(
            api.default_service_name(), span_name=self._method,
            binary_annotations={'http.uri': self.path},
        )
        span_ctx.start()
        span_ctx.add_remote_endpoint(host=self.host, port=self.port)
        for h, v in api.create_http_headers_for_new_span().items():
            self.putheader(h, v)

    __org_endheaders__(self)


def _patched_begin(self):
    __org_begin__(self)

    if api.is_tracing():
        api.get_trace_data().get_span_ctx().stop()


def patch():
    httplib.HTTPConnection.endheaders = _patched_endheaders
    httplib.HTTPResponse.begin = _patched_begin
