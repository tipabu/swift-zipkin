# Copyright (c) 2020 SwiftStack, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Contains concepts originally found in Eventlet, covered by the MIT software
# license.  The Eventlet license:
#  Copyright (c) 2005-2006, Bob Ippolito
#  Copyright (c) 2007-2010, Linden Research, Inc.
#  Copyright (c) 2008-2010, Eventlet Contributors (see AUTHORS)
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#
#  The above copyright notice and this permission notice shall be included in
#  all copies or substantial portions of the Software.
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#  THE SOFTWARE.
import sys
import os

import eventlet
requests = eventlet.import_patched('requests.__init__')

from eventlet.green import threading

import py_zipkin.storage
from py_zipkin.encoding import Encoding
from py_zipkin.zipkin import (
    zipkin_span, zipkin_client_span, zipkin_server_span, create_attrs_for_span,
    create_endpoint)

from swift_zipkin import transport

# Convenience imports so other places don't have to import py_zipkin stuff
from py_zipkin.zipkin import (
    create_http_headers_for_new_span,
    create_http_headers_for_this_span,
    ZipkinAttrs,
)
# shut up linter
create_http_headers_for_new_span = create_http_headers_for_new_span
create_http_headers_for_this_span = create_http_headers_for_this_span
ZipkinAttrs = ZipkinAttrs


sample_rate_pct = 100
_tls = threading.local()  # thread local storage for a SpanSavingTracer


class SpanSavingTracer(py_zipkin.storage.Tracer):
    """
    Like py-zipkin's Tracer, but supports accessing the "current" zipkin span
    context object.
    """
    def __init__(self):
        super(SpanSavingTracer, self).__init__()
        self._span_ctx_stack = py_zipkin.storage.Stack()

    def get_span_ctx(self):
        return self._span_ctx_stack.get()

    def push_span_ctx(self, ctx):
        self._span_ctx_stack.push(ctx)

    def pop_span_ctx(self):
        return self._span_ctx_stack.pop()


def _get_greenthread_local_tracer():
    """
    This is used to monkey-patch py_zipkins's get_default_tracer() with this
    eventlet-thread-local-storage-aware version.
    """
    if not hasattr(_tls, 'tracer'):
        _tls.tracer = SpanSavingTracer()
    return _tls.tracer


get_tracer = _get_greenthread_local_tracer


def _set_greenthread_local_tracer(tracer):
    """
    This is used to monkey-patch py_zipkins's set_default_tracer() with this
    eventlet-thread-local-storage-aware version.
    """
    _tls.tracer = tracer


set_tracer = _set_greenthread_local_tracer


def is_tracing():
    """
    Is a span currently "open"?
    """
    return hasattr(_tls, 'tracer')


class ezipkin_span(zipkin_span):
    """
    Subclass of zipkin_span that defaults some parameters and also allows
    access to the "current" span context object via a Stack on the
    SpanSavingTracer instance.

    It also allows adding a remote_endpoint for SERVER kinds.
    """
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('transport_handler', transport.global_green_http_transport)
        kwargs.setdefault('use_128bit_trace_id', True)
        kwargs.setdefault('encoding', Encoding.V2_JSON)
        super(ezipkin_span, self).__init__(*args, **kwargs)

    def start(self):
        # retval will be same as "self" but this feels a little cleaner
        retval = super(ezipkin_span, self).start()
        if retval.do_pop_attrs:
            self.get_tracer().push_span_ctx(retval)
        return retval

    def stop(self, _exc_type=None, _exc_value=None, _exc_traceback=None):
        if self.do_pop_attrs:
            self.get_tracer().pop_span_ctx()
        return super(ezipkin_span, self).stop(_exc_type=_exc_type,
                                              _exc_value=_exc_value,
                                              _exc_traceback=_exc_traceback)

    # The V2 protobuf API, at least, says remote_endpoint is valid and useful
    # for both CLIENT and SERVER kinds, so we provide a better, more general
    # method that replaces the base class's `add_sa_binary_annotation`
    def add_remote_endpoint(
        self,
        port=0,
        service_name='unknown',
        host='127.0.0.1',
    ):
        remote_endpoint = create_endpoint(
            port=int(port),
            service_name=service_name,
            host=host,
        )
        if not self.logging_context:
            if self.remote_endpoint is not None:
                raise ValueError('remote_endpoint already set!')
            self.remote_endpoint = remote_endpoint
        else:
            if self.logging_context.remote_endpoint is not None:
                raise ValueError('remote_endpoint already set!')
            self.logging_context.remote_endpoint = remote_endpoint


class ezipkin_client_span(ezipkin_span, zipkin_client_span):
    pass


class ezipkin_server_span(ezipkin_span, zipkin_server_span):
    pass


# Convenience function to find the current span context instance and call this
# method on it.
def update_binary_annotations(extra_annotations):
    tracer = _get_greenthread_local_tracer()
    span_ctx = tracer.get_span_ctx()
    if span_ctx:
        return span_ctx.update_binary_annotations(extra_annotations)


# Convenience function to find the current span context instance and call this
# method on it.
def add_remote_endpoint(port=0, service_name='unknown', host='127.0.0.1'):
    tracer = _get_greenthread_local_tracer()
    span_ctx = tracer.get_span_ctx()
    if span_ctx:
        return span_ctx.add_remote_endpoint(port=int(port),
                                            service_name=service_name,
                                            host=host)


def extract_zipkin_attrs_from_headers(headers):
    """
    Implements extraction of B3 headers per:
        https://github.com/openzipkin/b3-propagation

    Returns a ZipkinAttrs instance or None
    """
    # Check our non-standard header first
    is_shared = headers.get('X-B3-Shared', False) == '1'
    if 'b3' in headers:
        # b3={TraceId}-{SpanId}-{SamplingState}-{ParentSpanId}
        # where the last two fields are optional.
        bits = headers['b3'].split('-')
        if len(bits) == 1 and int(bits[0]) == 0:
            return create_attrs_for_span(sample_rate=0.0,
                                         use_128bit_trace_id=True)
        return ZipkinAttrs(bits[0], bits[1], bits[3:4] or None,
                           '0', bits[2:3] and bits[2] == '1',
                           is_shared)
    trace_id = headers.get('X-B3-TraceId', None)
    span_id = headers.get('X-B3-SpanId', None)
    sampled = headers.get('X-B3-Sampled', None)
    # Must have either both trace_id & span_id OR sampled
    if ((trace_id and span_id) or sampled):
        # ['trace_id', 'span_id', 'parent_span_id', 'flags', 'is_sampled',
        #  'is_shared']
        return ZipkinAttrs(
            trace_id,
            span_id,
            headers.get('X-B3-ParentSpanId', None),
            headers.get('X-B3-Flags', '0'),
            sampled == '1',
            is_shared)


def default_service_name():
    return os.path.basename(sys.argv[0])
