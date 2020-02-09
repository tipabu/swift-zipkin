#!/usr/bin/python
# Copyright 2020 SwiftStack, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import setuptools


setuptools.setup(
    name="swift-zipkin",
    version="__VERSION__",
    description="Zipkin monkey-patching tracing middleware for OpenStack Swift",
    license='Apache License (2.0)',
    author='SwiftStack',
    author_email='darrell@swiftstack.com',
    url='https://swiftstack.com',
    packages=['swift_zipkin'],
    classifiers=['Development Status :: 4 - Beta',
                 'Operating System :: POSIX :: Linux',
                 'Programming Language :: Python :: 2.7',
                 'Programming Language :: Python :: 3.7',
                 'Environment :: No Input/Output (Daemon)'],
    entry_points={'paste.filter_factory': [
        'zipkin = swift_zipkin.zipkin:filter_factory',
    ]},
)
