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
from eventlet.green import httplib

from swift_zipkin import api


__org_endheaders__ = httplib.HTTPConnection.endheaders
__org_conn_close__ = httplib.HTTPConnection.close
__org_begin__ = httplib.HTTPResponse.begin
__org_resp_close__ = httplib.HTTPResponse.close


# stores [span_ctx, should_close_span_in_HTTPConnection.close_flag] pairs
# if the 2nd element, the flag, is True, then we didn't exit
# HTTPResponse.begin(), so we timed out, and a call to HTTPConnection.close()
# should close our span stored by fd.  When begin() exits, it'll clear that
# flag so the span doesn't close early..
_span_contexts_by_fd = {}


def _patched_endheaders(self):
    if api.has_default_tracer():
        span_ctx = api.ezipkin_client_span(
            api.default_service_name(), span_name=self._method,
            binary_annotations={'http.uri': self.path},
        )
        span_ctx.start()

        remote_service_name = 'unknown'
        try:
            path_bits = self.path.split('/', 5)[1:]
            if path_bits[0].startswith('d') and path_bits[0][1:].isdigit():
                aco_bits = path_bits[2:]
                if len(aco_bits) == 1:
                    remote_service_name = 'swift-account-server'
                elif len(aco_bits) == 2:
                    remote_service_name = 'swift-container-server'
                elif len(aco_bits) == 3:
                    remote_service_name = 'swift-object-server'
        except Exception:
            pass
        span_ctx.add_remote_endpoint(host=self.host, port=self.port,
                                     service_name=remote_service_name)
        b3_headers = span_ctx.create_http_headers_for_my_span()
        for h, v in b3_headers.items():
            self.putheader(h, v)

    __org_endheaders__(self)

    if api.has_default_tracer():
        span_ctx._fd_key = self.sock.fileno()
        # stores [span_ctx, should_close_span_in_HTTPConnection.close_flag]
        # pairs:
        _span_contexts_by_fd[span_ctx._fd_key] = [span_ctx, True]


def _patched_begin(self):
    __org_begin__(self)

    if api.has_default_tracer():
        self._zipkin_span = span_ctx = \
            _span_contexts_by_fd[self.fp.fileno()][0]
        span_ctx.update_binary_annotations({"http.status_code": self.status})
        span_ctx.add_annotation('Response headers received')

        # If we were a HEAD, go ahead and do the close here; should be safe if
        # the client does it too since it's idempotent, I think.  There were
        # definitely some cases where _no one_ called our self.close() and the
        # span was left dangling.
        if self._method == "HEAD":
            self.close()
        else:
            # We're not timing out, so make sure the call to
            # HTTPConnection.close() doesn't close the span.
            _span_contexts_by_fd[self.fp.fileno()][1] = False


def _patched_resp_close(self):
    __org_resp_close__(self)

    span_ctx = getattr(self, '_zipkin_span', None)
    if api.has_default_tracer() and span_ctx:
        span_ctx.stop()
        del _span_contexts_by_fd[span_ctx._fd_key]
        del self._zipkin_span


def _patched_conn_close(self):
    sock = self.sock
    span_ctx = None
    if sock:
        span_ctx, conn_close_should_close_span = _span_contexts_by_fd.get(sock.fileno(),
                                                                          (None, None))
    __org_conn_close__(self)

    if api.has_default_tracer() and span_ctx and conn_close_should_close_span:
        span_ctx.stop()
        del _span_contexts_by_fd[span_ctx._fd_key]


def patch():
    httplib.HTTPConnection.endheaders = _patched_endheaders
    httplib.HTTPConnection.close = _patched_conn_close
    httplib.HTTPResponse.begin = _patched_begin
    httplib.HTTPResponse.close = _patched_resp_close
