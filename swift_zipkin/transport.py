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
import eventlet
requests = eventlet.import_patched('requests.__init__')

from eventlet.green import threading

from py_zipkin import transport


_tls = threading.local()  # thread local storage for GreenHttpTransport
global_green_http_transport = None


class GreenHttpTransport(transport.BaseTransportHandler):
    """
    We'll keep one global instance of this class to send the v2 API JSON
    payloads to the server with a greened `requests` module connection pool.
    """
    def __init__(self, logger, address, port, flush_threshold_size=2**20,
                 flush_threshold_sec=2.0):
        self.logger = logger
        self.address = address
        self.port = port
        self.url = 'http://%s:%s/api/v2/spans' % (self.address, self.port)
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': "application/json",
        })
        self.flush_threshold_size = flush_threshold_size
        self.flush_threshold_sec = flush_threshold_sec
        self.payload_buffer = []
        self.total_buffer_size = 0
        self._timed_flush_gt = None

    @classmethod
    def init_singleton(cls, logger, address, port, flush_threshold_size=2**20,
                       flush_threshold_sec=2.0):
        global global_green_http_transport
        global_green_http_transport = cls(logger, address, port,
                                          flush_threshold_size=flush_threshold_size,
                                          flush_threshold_sec=flush_threshold_sec)

    def get_max_payload_bytes(self):
        return None

    def send(self, payload):
        if not self._timed_flush_gt:
            self.reschedule_flush_timer()

        self.payload_buffer.append(payload)
        self.total_buffer_size += len(payload)
        if self.total_buffer_size > self.flush_threshold_size:
            self.do_flush()

    def reschedule_flush_timer(self, _gt=None):
        if self._timed_flush_gt:
            self._timed_flush_gt.wait()
        self._timed_flush_gt = eventlet.spawn_after(
            self.flush_threshold_sec,
            self.do_flush)
        self._timed_flush_gt.link(self.reschedule_flush_timer)

    def do_flush(self):
        if not self.payload_buffer:
            return

        buffer_switch_event = eventlet.Event()
        eventlet.spawn_n(self._gt_flush, buffer_switch_event)
        buffer_switch_event.wait()
        self.payload_buffer = []
        self.total_buffer_size = 0

    def _gt_flush(self, buffer_switch_event):
        # This was the fastest way I could think of to concatenate JSON lists
        _tls.flush_buffer = '[' + ','.join(
            p[p.index('[') + 1:p.rindex(']')]
            for p in self.payload_buffer
        ) + ']'
        buffer_switch_event.send()
        flush_size = len(_tls.flush_buffer)
        resp = self.session.post(self.url, data=_tls.flush_buffer)
        del _tls.flush_buffer
        try:
            resp.raise_for_status()
        except Exception as e:
            self.logger.debug(
                'Error flushing %d bytes to %s: %r',
                flush_size, self.url, e)
