import py_zipkin.storage
import py_zipkin.transport

from swift_zipkin import api, wsgi, http, greenthread


def patch_eventlet_and_swift(zipkin_host='127.0.0.1', zipkin_port=9411,
                             sample_rate=1.0):
    """
    Monkey patch eventlet and swift for Zipkin distributed tracing.

    The "Zipkin server" can be anything that accepts Zipkin V2 JSON protocol
    HTTP POSTs to /api/v2/spans

    :param host: Zipkin server IP address (default: '127.0.0.1')
    :param port: Zipkin server port (default: 9411)
    :param sample_rate: A Float value (0.0~1.0) that indicates
        the tracing proportion. If you specify 1.0, all request
        are traced (and sent to Zipkin collecotr).
        If you specify 0.1, each root trace (client WSGI request) has only a
        10% chance of actually getting traced. (default: 1.0)
    """
    # Overwrite py_zipkin.storage get/set_default_tracer functions with our
    # greenthread-aware functions.
    py_zipkin.storage.set_default_tracer = api._set_greenthread_local_tracer
    py_zipkin.storage.get_default_tracer = api._get_greenthread_local_tracer
    py_zipkin.zipkin.get_default_tracer = api._get_greenthread_local_tracer
    py_zipkin.get_default_tracer = api._get_greenthread_local_tracer

    # py_zipkin uses 0-100% for sample-rate, so convert here
    api.sample_rate_pct = sample_rate * 100.0
    api.global_green_http_transport = api.GreenHttpTransport(zipkin_host,
                                                             zipkin_port)

    wsgi.patch()
    http.patch()
    greenthread.patch()
