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
import inspect
import os

from eventlet import wsgi

from swift_zipkin import api


__original_handle_one_response__ = wsgi.HttpProtocol.handle_one_response


def _extract_status_code(environ, zipkin_span):
    # When we're called, the prior stack frame has a local var named
    # "status_code" and we want to extract "status_code[0]" as http.status_code
    # binary annotation.
    frame = inspect.currentframe().f_back
    try:
        status_code = frame.f_locals['status_code'][0]
        zipkin_span.update_binary_annotations({
            'http.status_code': status_code,
        })
    finally:
        del frame


def _patched_handle_one_response(self):
    zipkin_attrs = api.extract_zipkin_attrs_from_headers(self.headers)
    sample_rate = api.sample_rate_pct
    # A client can send just `X-B3-Sampled: 0` with no trace identifiers to
    # just say "don't trace".  In that case, we let new trace IDs get created
    # but we clamp the sample rate to 0% to honor the client's desire.
    if (zipkin_attrs and not (zipkin_attrs.trace_id and zipkin_attrs.span_id)
            and zipkin_attrs.is_sampled is False):
        zipkin_attrs = None
        sample_rate = 0.0

    binary_annotations = {
        "http.uri": self.path,
        "worker.pid": str(os.getpid()),
    }

    local_ip, local_port = self.request.getsockname()[:2]

    # PROXY proto proxy_address will be better to use for "this server" than
    # the raw values from the socket.
    proxy_proto_proxy_ip, proxy_proto_proxy_port = getattr(
        self, 'proxy_address', (None, None))
    if proxy_proto_proxy_ip:
        local_ip = proxy_proto_proxy_ip
        local_port = int(proxy_proto_proxy_port)

    # Get the best client IP/port we can
    proxy_proto_client_ip, proxy_proto_client_port = getattr(
        self, 'client_address', (None, None))
    raw_peer_ip, raw_peer_port = self.request.getpeername()[:2]
    forwarded_for = self.headers.get('X-Forwarded-For')
    if proxy_proto_client_ip:
        # Try results of PROXY protocol first
        client_ip = proxy_proto_client_ip
        client_port = int(proxy_proto_client_port)
    elif forwarded_for:
        # Fallback on standard X-Forwarded-For
        client_ip = forwarded_for.split(',')[0].strip()
        client_port = raw_peer_port
    else:
        # Failing all that, just use the other end of the raw socket.
        client_ip = raw_peer_ip
        client_port = raw_peer_port

    with api.ezipkin_server_span(
        service_name=api.default_service_name(),
        span_name=self.command,
        zipkin_attrs=zipkin_attrs,
        sample_rate=sample_rate,
        host=local_ip,
        port=local_port,
        binary_annotations=binary_annotations,
    ) as zipkin_span:
        zipkin_span.add_remote_endpoint(
            client_port, self.headers.get('User-Agent'), client_ip)
        # Add in a hook to snarf out the response status
        self.environ['eventlet.posthooks'].append(
            (_extract_status_code, (zipkin_span,), {}),
        )

        __original_handle_one_response__(self)

        # If we're a root span, see if we can extract a Swift transaction ID to
        # associate with this (one-per-trace) root span.  We don't track it on
        # every span because Zipkin's trace_id/span_id/parent_id values already
        # link everything together and more copies of the swift txid would just
        # waste space.
        SWIFT_TRANS_ID_KEY = 'swift.trans_id'
        if not zipkin_span.zipkin_attrs.parent_span_id:
            if SWIFT_TRANS_ID_KEY in self.environ:
                zipkin_span.update_binary_annotations({
                    SWIFT_TRANS_ID_KEY: self.environ[SWIFT_TRANS_ID_KEY],
                })


def patch():
    wsgi.HttpProtocol.handle_one_response = _patched_handle_one_response
