# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

from glob import glob
from math import ceil
from os.path import join
from socket import inet_aton, inet_ntoa
from struct import pack, unpack
from subprocess import check_call

import requests

from platformio_api import __version__, config
from platformio_api.exception import (DLFileError, DLFileSizeError,
                                      InvalidLibConf)


def ip2int(ip_string):
    return unpack("!I", inet_aton(ip_string))[0]


def int2ip(ip_int):
    return inet_ntoa(pack("!I", ip_int))


def download_file(source_url, destination_path):
    CHUNK_SIZE = 1024
    downloaded = 0

    f = None
    r = None
    try:
        headers = {"User-Agent": "PlatformIOLibRegistry/%s %s" % (
            __version__, requests.utils.default_user_agent())}
        r = requests.get(source_url, headers=headers, stream=True)
        if r.status_code != 200:
            raise DLFileError("status=%d, url=%s" % (
                r.status_code, source_url))
        if int(r.headers.get("content-length", 0)) > config['MAX_DLFILE_SIZE']:
            raise DLFileSizeError(config['MAX_DLFILE_SIZE'],
                                  int(r.headers['content-length']))

        f = open(destination_path, "wb")
        for data in r.iter_content(chunk_size=CHUNK_SIZE):
            if downloaded > config['MAX_DLFILE_SIZE']:
                raise DLFileSizeError(config['MAX_DLFILE_SIZE'], downloaded)
            f.write(data)
            downloaded += CHUNK_SIZE
    finally:
        if f:
            f.close()
        if r:
            r.close()


def create_archive(archive_path, source_dir):
    if archive_path.endswith(".tar.gz"):
        check_call(["tar", "czf", archive_path, "-C", source_dir, "."])
    else:
        raise NotImplementedError()


def extract_archive(archive_path, destination_dir):
    if archive_path.endswith(".tar.gz"):
        check_call(["tar", "xfz", archive_path, "-C", destination_dir])
    elif archive_path.endswith(".zip"):
        check_call(["unzip", "-q", archive_path, "-d", destination_dir])
    else:
        raise NotImplementedError()


def get_packages_dir():
    return join(config['DL_PIO_DIR'], "packages")


def get_package_url(package_name):
    return "%s/packages/%s" % (config['DL_PIO_URL'], package_name)


def get_libarch_relpath(lib_id, version_id):
    lib_id = int(lib_id)
    version_id = int(version_id)
    assert lib_id > 0 and version_id > 0
    return join("libraries", "archives", str(int(ceil(lib_id/100))),
                "%d.tar.gz" % version_id)


def get_libarch_path(lib_id, version_id):
    return join(config['DL_PIO_DIR'], get_libarch_relpath(lib_id, version_id))


def get_libarch_url(lib_id, version_id):
    return "%s/%s" % (config['DL_PIO_URL'],
                      get_libarch_relpath(lib_id, version_id))


def get_libexample_relpath(lib_id):
    lib_id = int(lib_id)
    assert lib_id > 0
    return join("libraries", "examples",
                str(int(ceil(lib_id/100))), str(lib_id))


def get_libexample_dir(lib_id):
    return join(config['DL_PIO_DIR'], get_libexample_relpath(lib_id))


def get_libexample_url(lib_id, name):
    return "%s/%s/%s" % (config['DL_PIO_URL'], get_libexample_relpath(lib_id),
                         name)


def validate_libconf(data):
    fields = set(data.keys())
    if not fields.issuperset(set(["name", "keywords", "description"])):
        raise InvalidLibConf(
            "The 'name, keywords and description' fields are required")

    if ("dependencies" in data and not
            (isinstance(data['dependencies'], list) or
             isinstance(data['dependencies'], dict))):
        raise InvalidLibConf("The 'dependencies' field is invalid")

    # if github- or mbed-based project
    if "repository" in data:
        type = data['repository'].get("type", None)
        url = data['repository'].get("url", "")
        if ((type == "git" and "github.com" in url)  # github
                or (type == "hg" and "developer.mbed.org" in url)):  # mbed
            return data

    # if CVS-based
    authors = data.get("authors", None)
    if authors and not isinstance(authors, list):
        authors = [authors]

    if not authors:
        raise InvalidLibConf("The 'authors' field is required")
    elif not all(["name" in item for item in authors]):
        raise InvalidLibConf("An each author should have 'name' property")
    elif ("repository" in data and
          data['repository'].get("type", None) in ("git", "svn")):
        return data

    # if self-hosted
    if "version" not in data:
        raise InvalidLibConf("The 'version' field is required")
    elif "downloadUrl" not in data:
        raise InvalidLibConf("The 'downloadUrl' field is required")

    return data


def get_c_sources(in_dir):
    return glob(join(in_dir, '*.c')) + glob(join(in_dir, '*.cpp')) \
        + glob(join(in_dir, '*.h'))
