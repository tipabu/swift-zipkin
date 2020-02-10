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
from eventlet import Timeout
import json
import pickle

from swift.common import memcached

from swift_zipkin import api


__org_set__ = memcached.MemcacheRing.set
__org_get__ = memcached.MemcacheRing.get
__org_incr__ = memcached.MemcacheRing.incr
__org_decr__ = memcached.MemcacheRing.decr
__org_delete__ = memcached.MemcacheRing.delete
__org_set_multi__ = memcached.MemcacheRing.set_multi
__org_get_multi__ = memcached.MemcacheRing.get_multi


def add_remote_endpoint(zipkin_span, server):
    server_host, server_port = memcached.utils.parse_socket_string(
        server, memcached.DEFAULT_MEMCACHED_PORT)
    zipkin_span.add_remote_endpoint(port=int(server_port),
                                    host=server_host,
                                    service_name='memcached')


# Okay, so fine.  This is pretty gross, inlining basically all of the memcached
# file here and just adding tracing.
# BUT, in my defense, I really want to be able to record the span to every
# memcached server with accurate remote_endpoint information.  And that's not
# possible, given how the upstream code is structured.
#
# Luckily, the last change in here was May 29, 2019, so it's not a super
# high-change module.
#
# Future work would be to reorganize upstream code so it was easy to
# monkey-patch by us, here.

def _set(self, key, value, serialize=True, time=0,
         min_compress_len=0):
    """
    Set a key/value pair in memcache

    :param key: key
    :param value: value
    :param serialize: if True, value is serialized with JSON before sending
                        to memcache, or with pickle if configured to use
                        pickle instead of JSON (to avoid cache poisoning)
    :param time: the time to live
    :param min_compress_len: minimum compress length, this parameter was
                                added to keep the signature compatible with
                                python-memcached interface. This
                                implementation ignores it.
    """
    orig_key = key
    key = memcached.md5hash(key)
    timeout = memcached.sanitize_timeout(time)
    flags = 0
    if serialize and self._allow_pickle:
        value = pickle.dumps(value, memcached.PICKLE_PROTOCOL)
        flags |= memcached.PICKLE_FLAG
    elif serialize:
        if isinstance(value, bytes):
            value = value.decode('utf8')
        value = json.dumps(value).encode('ascii')
        flags |= memcached.JSON_FLAG
    elif not isinstance(value, bytes):
        value = str(value).encode('utf-8')

    for (server, fp, sock) in self._get_conns(key):
        with api.ezipkin_client_span(
            api.default_service_name(),
            span_name='memcached.set',
            binary_annotations={
                "memcached.key": orig_key,
            },
        ) as zipkin_span:
            add_remote_endpoint(zipkin_span, server)
            try:
                with Timeout(self._io_timeout):
                    sock.sendall(memcached.set_msg(key, flags, timeout, value))
                    # Wait for the set to complete
                    fp.readline()
                    self._return_conn(server, fp, sock)
                    return
            except (Exception, Timeout) as e:
                self._exception_occurred(server, e, sock=sock, fp=fp)


def _get(self, key):
    """
    Gets the object specified by key.  It will also unserialize the object
    before returning if it is serialized in memcache with JSON, or if it
    is pickled and unpickling is allowed.

    :param key: key
    :returns: value of the key in memcache
    """
    orig_key = key
    key = memcached.md5hash(key)
    value = None
    for (server, fp, sock) in self._get_conns(key):
        with api.ezipkin_client_span(
            api.default_service_name(),
            span_name='memcached.get',
            binary_annotations={
                "memcached.key": orig_key,
            },
        ) as zipkin_span:
            add_remote_endpoint(zipkin_span, server)
            try:
                with Timeout(self._io_timeout):
                    sock.sendall(b'get ' + key + b'\r\n')
                    line = fp.readline().strip().split()
                    while True:
                        if not line:
                            raise memcached.MemcacheConnectionError(
                                'incomplete read')
                        if line[0].upper() == b'END':
                            break
                        if line[0].upper() == b'VALUE' and line[1] == key:
                            size = int(line[3])
                            value = fp.read(size)
                            if int(line[2]) & memcached.PICKLE_FLAG:
                                if self._allow_unpickle:
                                    value = pickle.loads(value)
                                else:
                                    value = None
                            elif int(line[2]) & memcached.JSON_FLAG:
                                value = json.loads(value)
                            fp.readline()
                        line = fp.readline().strip().split()
                    self._return_conn(server, fp, sock)
                    return value
            except (Exception, Timeout) as e:
                self._exception_occurred(server, e, sock=sock, fp=fp)


def _incr(self, key, delta=1, time=0):
    """
    Increments a key which has a numeric value by delta.
    If the key can't be found, it's added as delta or 0 if delta < 0.
    If passed a negative number, will use memcached's decr. Returns
    the int stored in memcached
    Note: The data memcached stores as the result of incr/decr is
    an unsigned int.  decr's that result in a number below 0 are
    stored as 0.

    :param key: key
    :param delta: amount to add to the value of key (or set as the value
                    if the key is not found) will be cast to an int
    :param time: the time to live
    :returns: result of incrementing
    :raises MemcacheConnectionError:
    """
    orig_key = key
    key = memcached.md5hash(key)
    command = b'incr'
    if delta < 0:
        command = b'decr'
    delta = str(abs(int(delta))).encode('ascii')
    timeout = memcached.sanitize_timeout(time)
    if delta >= 0:
        span_name = 'memcached.incr'
    else:
        span_name = 'memcached.decr'
    for (server, fp, sock) in self._get_conns(key):
        with api.ezipkin_client_span(
            api.default_service_name(),
            span_name=span_name,
            binary_annotations={
                "memcached.key": orig_key,
            },
        ) as zipkin_span:
            add_remote_endpoint(zipkin_span, server)
            try:
                with Timeout(self._io_timeout):
                    sock.sendall(b' '.join([
                        command, key, delta]) + b'\r\n')
                    line = fp.readline().strip().split()
                    if not line:
                        raise memcached.MemcacheConnectionError('incomplete read')
                    if line[0].upper() == b'NOT_FOUND':
                        add_val = delta
                        if command == b'decr':
                            add_val = b'0'
                        sock.sendall(b' '.join([
                            b'add', key, b'0',
                            str(timeout).encode('ascii'),
                            str(len(add_val)).encode('ascii')
                        ]) + b'\r\n' + add_val + b'\r\n')
                        line = fp.readline().strip().split()
                        if line[0].upper() == b'NOT_STORED':
                            sock.sendall(b' '.join([
                                command, key, delta]) + b'\r\n')
                            line = fp.readline().strip().split()
                            ret = int(line[0].strip())
                        else:
                            ret = int(add_val)
                    else:
                        ret = int(line[0].strip())
                    self._return_conn(server, fp, sock)
                    return ret
            except (Exception, Timeout) as e:
                self._exception_occurred(server, e, sock=sock, fp=fp)
    raise memcached.MemcacheConnectionError("No Memcached connections succeeded.")


def _decr(self, key, delta=1, time=0):
    """
    Decrements a key which has a numeric value by delta. Calls incr with
    -delta.

    :param key: key
    :param delta: amount to subtract to the value of key (or set the
                    value to 0 if the key is not found) will be cast to
                    an int
    :param time: the time to live
    :returns: result of decrementing
    :raises MemcacheConnectionError:
    """
    return self.incr(key, delta=-delta, time=time)


def _delete(self, key):
    """
    Deletes a key/value pair from memcache.

    :param key: key to be deleted
    """
    orig_key = key
    key = memcached.md5hash(key)
    for (server, fp, sock) in self._get_conns(key):
        with api.ezipkin_client_span(
            api.default_service_name(),
            span_name='memcached.delete',
            binary_annotations={
                "memcached.key": orig_key,
            },
        ) as zipkin_span:
            add_remote_endpoint(zipkin_span, server)
            try:
                with Timeout(self._io_timeout):
                    sock.sendall(b'delete ' + key + b'\r\n')
                    # Wait for the delete to complete
                    fp.readline()
                    self._return_conn(server, fp, sock)
                    return
            except (Exception, Timeout) as e:
                self._exception_occurred(server, e, sock=sock, fp=fp)


def _set_multi(self, mapping, server_key, serialize=True, time=0,
               min_compress_len=0):
    """
    Sets multiple key/value pairs in memcache.

    :param mapping: dictionary of keys and values to be set in memcache
    :param server_key: key to use in determining which server in the ring
                        is used
    :param serialize: if True, value is serialized with JSON before sending
                        to memcache, or with pickle if configured to use
                        pickle instead of JSON (to avoid cache poisoning)
    :param time: the time to live
    :min_compress_len: minimum compress length, this parameter was added
                        to keep the signature compatible with
                        python-memcached interface. This implementation
                        ignores it
    """
    orig_key = server_key
    server_key = memcached.md5hash(server_key)
    timeout = memcached.sanitize_timeout(time)
    msg = []
    for key, value in mapping.items():
        key = memcached.md5hash(key)
        flags = 0
        if serialize and self._allow_pickle:
            value = pickle.dumps(value, memcached.PICKLE_PROTOCOL)
            flags |= memcached.PICKLE_FLAG
        elif serialize:
            if isinstance(value, bytes):
                value = value.decode('utf8')
            value = json.dumps(value).encode('ascii')
            flags |= memcached.JSON_FLAG
        msg.append(memcached.set_msg(key, flags, timeout, value))
    for (server, fp, sock) in self._get_conns(server_key):
        with api.ezipkin_client_span(
            api.default_service_name(),
            span_name='memcached.set_multi',
            binary_annotations={
                "memcached.key": orig_key,
            },
        ) as zipkin_span:
            add_remote_endpoint(zipkin_span, server)
            try:
                with Timeout(self._io_timeout):
                    sock.sendall(b''.join(msg))
                    # Wait for the set to complete
                    for line in range(len(mapping)):
                        fp.readline()
                    self._return_conn(server, fp, sock)
                    return
            except (Exception, Timeout) as e:
                self._exception_occurred(server, e, sock=sock, fp=fp)


def _get_multi(self, keys, server_key):
    """
    Gets multiple values from memcache for the given keys.

    :param keys: keys for values to be retrieved from memcache
    :param server_key: key to use in determining which server in the ring
                        is used
    :returns: list of values
    """
    orig_key = server_key
    server_key = memcached.md5hash(server_key)
    keys = [memcached.md5hash(key) for key in keys]
    for (server, fp, sock) in self._get_conns(server_key):
        with api.ezipkin_client_span(
            api.default_service_name(),
            span_name='memcached.get_multi',
            binary_annotations={
                "memcached.key": orig_key,
            },
        ) as zipkin_span:
            add_remote_endpoint(zipkin_span, server)
            try:
                with Timeout(self._io_timeout):
                    sock.sendall(b'get ' + b' '.join(keys) + b'\r\n')
                    line = fp.readline().strip().split()
                    responses = {}
                    while True:
                        if not line:
                            raise memcached.MemcacheConnectionError(
                                'incomplete read')
                        if line[0].upper() == b'END':
                            break
                        if line[0].upper() == b'VALUE':
                            size = int(line[3])
                            value = fp.read(size)
                            if int(line[2]) & memcached.PICKLE_FLAG:
                                if self._allow_unpickle:
                                    value = pickle.loads(value)
                                else:
                                    value = None
                            elif int(line[2]) & memcached.JSON_FLAG:
                                value = json.loads(value)
                            responses[line[1]] = value
                            fp.readline()
                        line = fp.readline().strip().split()
                    values = []
                    for key in keys:
                        if key in responses:
                            values.append(responses[key])
                        else:
                            values.append(None)
                    self._return_conn(server, fp, sock)
                    return values
            except (Exception, Timeout) as e:
                self._exception_occurred(server, e, sock=sock, fp=fp)


def patch():
    memcached.MemcacheRing.set = _set
    memcached.MemcacheRing.get = _get
    memcached.MemcacheRing.incr = _incr
    memcached.MemcacheRing.decr = _decr
    memcached.MemcacheRing.delete = _delete
    memcached.MemcacheRing.set_multi = _set_multi
    memcached.MemcacheRing.get_multi = _get_multi
