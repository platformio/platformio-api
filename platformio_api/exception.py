# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.


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


class LibArchiveError(PlatformioAPIException):

    MESSAGE = "Can not archive a library"


class DLFileError(PlatformioAPIException):

    MESSAGE = "Can not download a file: %s"


class DLFileSizeError(PlatformioAPIException):

    MESSAGE = ("A maximum size of download file is %s bytes "
               "(you tried to download %d bytes")


class APIBadRequest(PlatformioAPIException):
    pass


class APINotFound(PlatformioAPIException):
    pass
