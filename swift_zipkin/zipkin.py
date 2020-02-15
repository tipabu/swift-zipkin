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


"""
-----------------------------------------------
How to Enable Zipkin Tracing in a Swift Cluster
-----------------------------------------------

XXX Rewrite this

This middleware was written as an effort to refactor parts of the proxy server,
so this functionality was already available in previous releases and every
attempt was made to maintain backwards compatibility. To allow operators to
perform a seamless upgrade, it is not required to add the middleware to the
proxy pipeline and the flag ``allow_versions`` in the container server
configuration files are still valid, but only when using
``X-Versions-Location``. In future releases, ``allow_versions`` will be
deprecated in favor of adding this middleware to the pipeline to enable or
disable the feature.

In case the middleware is added to the proxy pipeline, you must also
set ``allow_versioned_writes`` to ``True`` in the middleware options
to enable the information about this middleware to be returned in a /info
request.

 .. note::
     You need to add the middleware to the proxy pipeline and set
     ``allow_versioned_writes = True`` to use ``X-History-Location``. Setting
     ``allow_versions = True`` in the container server is not sufficient to
     enable the use of ``X-History-Location``.


------------------------------------------------
How to Disable Zipkin Tracing in a Swift Cluster
------------------------------------------------

XXX Rewrite this

If you want to disable all functionality, set ``allow_versioned_writes`` to
``False`` in the middleware options.

Disable versioning from a container (x is any value except empty)::

    curl -i -XPOST -H "X-Auth-Token: <token>" \
-H "X-Remove-Versions-Location: x" http://<storage_url>/container
"""

import os

from swift.common.utils import (
    get_logger, register_swift_info, config_true_value,
    config_positive_int_value, config_float_value)

from swift_zipkin.patcher import patch_eventlet_and_swift


class ZipkinMiddleware(object):

    def __init__(self, app, conf):
        self.app = app
        self.conf = conf
        self.logger = get_logger(conf, log_route='swift_zipkin')
        self.enabled = config_true_value(conf.get('zipkin_enable'))
        self.zipkin_v2_host = self.conf.get('zipkin_v2_host') or '127.0.0.1'
        self.zipkin_v2_port = config_positive_int_value(
            self.conf.get('zipkin_v2_port', 9411))
        raw_sample_rate = self.conf.get('zipkin_sample_rate')
        if raw_sample_rate:
            self.zipkin_sample_rate = config_float_value(raw_sample_rate,
                                                         minimum=0.0,
                                                         maximum=1.0)
        else:
            self.zipkin_sample_rate = 1.0
        self.zipkin_flush_threshold_size = config_positive_int_value(
            self.conf.get('zipkin_flush_threshold_size', 2**20))
        self.zipkin_flush_threshold_sec = config_float_value(
            self.conf.get('zipkin_flush_threshold_size', 2.0))

        if not self.enabled:
            # It's not like we're going to get enabled between the first and
            # second invocations of this constructor...
            return

        # Use our class to store a count of instantiations; We'll get
        # one time before the forking off of workers, and again inside each
        # worker.  We're interested in only doing our business post-fork,
        # during the SECOND instantiation.
        setattr(self.__class__, '_instantiation_count',
                1 + getattr(self.__class__, '_instantiation_count', 0))

        if self.__class__._instantiation_count < 2:
            self.logger.debug('ZipkinMiddleware() count=%d PID=%d; '
                              'deferring work to 2nd instantiation.',
                              self.__class__._instantiation_count, os.getpid())
            return

        self.logger.debug('ZipkinMiddleware() count=%d PID=%d; '
                          'tracing %.0f%% of reqs to Zipkin at '
                          '%s:%s',
                          self.__class__._instantiation_count, os.getpid(),
                          100.0 * self.zipkin_sample_rate,
                          self.zipkin_v2_host, self.zipkin_v2_port)
        patch_eventlet_and_swift(
            self.logger,
            self.zipkin_v2_host,
            self.zipkin_v2_port,
            self.zipkin_sample_rate,
            self.zipkin_flush_threshold_size,
            self.zipkin_flush_threshold_sec,
        )

    def __call__(self, env, start_response):
        # This middleware doesn't actually do anything _in_ the pipeline.  It
        # just exists to monkey-patch things at import-time prior to the
        # creation and execution of the eventlet WSGI server.
        return self.app(env, start_response)


def filter_factory(global_conf, **local_conf):
    conf = global_conf.copy()
    conf.update(local_conf)
    register_swift_info('zipkin')

    def zipkin_filter(app):
        return ZipkinMiddleware(app, conf)

    return zipkin_filter
