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
import py_zipkin.storage
import py_zipkin.thread_local

from swift_zipkin import api, wsgi, http, greenthread, memcached, transport


def patch_eventlet_and_swift(logger, zipkin_host='127.0.0.1', zipkin_port=9411,
                             sample_rate=1.0, flush_size=2**20, flush_sec=2.0):
    """
    Monkey patch eventlet and swift for Zipkin distributed tracing.

    The "Zipkin server" can be anything that accepts Zipkin V2 JSON protocol
    HTTP POSTs to /api/v2/spans

    :param logger: logger for logging things; passed to the transport
    :param host: Zipkin server IP address (default: '127.0.0.1')
    :param port: Zipkin server port (default: 9411)
    :param sample_rate: A Float value (0.0~1.0) that indicates
        the tracing proportion. If you specify 1.0, all request
        are traced (and sent to Zipkin collecotr).
        If you specify 0.1, each root trace (client WSGI request) has only a
        10% chance of actually getting traced. (default: 1.0)
    :param flush_size: flush when buffer is greater than this number of bytes
    :param flush_sec: flush every X seconds, regardless of buffer size
    """
    # Overwrite py_zipkin.storage get/set_default_tracer functions with our
    # greenthread-aware functions.
    py_zipkin.storage.set_default_tracer = api.set_default_tracer
    py_zipkin.storage.get_default_tracer = api.get_default_tracer
    py_zipkin.storage.has_default_tracer = api.has_default_tracer
    py_zipkin.zipkin.get_default_tracer = api.get_default_tracer
    py_zipkin.thread_local.get_default_tracer = api.get_default_tracer
    py_zipkin.get_default_tracer = api.get_default_tracer

    # py_zipkin uses 0-100% for sample-rate, so convert here
    api.sample_rate_pct = sample_rate * 100.0
    transport.GreenHttpTransport.init_singleton(
        logger, zipkin_host, zipkin_port, flush_size, flush_sec)

    wsgi.patch()
    http.patch()
    greenthread.patch()
    memcached.patch()
