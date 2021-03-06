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
from eventlet import greenthread

from swift_zipkin import api


__original_init__ = greenthread.GreenThread.__init__
__original_main__ = greenthread.GreenThread.main


def _patched__init(self, parent):
    # parent thread saves current TraceData from tls to self
    if api.has_default_tracer():
        self.zipkin_tracer = api.get_default_tracer().copy()

    __original_init__(self, parent)


def _patched_main(self, function, args, kwargs):
    # child thread inherits TraceData
    if hasattr(self, 'zipkin_tracer'):
        api.set_default_tracer(self.zipkin_tracer)

    try:
        __original_main__(self, function, args, kwargs)
    finally:
        if hasattr(self, 'zipkin_tracer'):
            del self.zipkin_tracer


def patch():
    greenthread.GreenThread.__init__ = _patched__init
    greenthread.GreenThread.main = _patched_main
