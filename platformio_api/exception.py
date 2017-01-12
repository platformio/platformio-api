# Copyright 2014-present Ivan Kravets <me@ikravets.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


class PlatformioAPIException(Exception):

    MESSAGE = None

    def __str__(self):  # pragma: no cover
        if self.MESSAGE:
            return self.MESSAGE % self.args
        else:
            return Exception.__str__(self)


class InvalidLibConf(PlatformioAPIException):

    MESSAGE = "Invalid library config: %s"


class InvalidLibVersion(PlatformioAPIException):

    MESSAGE = "Invalid library version: %s"


class DLFileError(PlatformioAPIException):

    MESSAGE = "Can not download a file: %s"


class DLFileSizeError(PlatformioAPIException):

    MESSAGE = ("A maximum size of download file is %s bytes "
               "(you tried to download %d bytes")


class APIBadRequest(PlatformioAPIException):
    pass


class APINotFound(PlatformioAPIException):
    pass
