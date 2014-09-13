# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

from math import ceil
from os.path import join
from socket import inet_aton, inet_ntoa
from struct import pack, unpack
from subprocess import check_call

from requests import get

from platformio_api import config
from platformio_api.exception import (DLFileError, DLFileSizeError,
                                      InvalidLibConf)


def ip2int(ip_string):
    return unpack("!I", inet_aton(ip_string))[0]


def int2ip(ip_int):
    return inet_ntoa(pack("!I", ip_int))


def download_file(source_url, destination_path):
    CHUNK_SIZE = 1024
    downloaded = 0

    try:
        r = get(source_url, stream=True)
        if r.status_code != 200:
            raise DLFileError("status=%d, url=%s" % (
                r.status_code, source_url))
        if int(r.headers.get("content-length", 0)) > config['MAX_DLFILE_SIZE']:
            raise DLFileSizeError(config['MAX_DLFILE_SIZE'],
                                  r.headers['content-length'])

        f = open(destination_path, "wb")
        for data in r.iter_content(chunk_size=CHUNK_SIZE):
            if downloaded > config['MAX_DLFILE_SIZE']:
                raise DLFileSizeError(config['MAX_DLFILE_SIZE'], downloaded)
            f.write(data)
            downloaded += CHUNK_SIZE
    finally:
        f.close()
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


def get_libarch_relpath(id_, name, version):
    id_ = int(id_)
    assert id_ > 0
    return join("libraries", "archives", str(int(ceil(id_/100))),
                "%s_%s.tar.gz" % (name, version))


def get_libarch_path(id_, name, version):
    return join(config['DL_PIO_DIR'], get_libarch_relpath(id_, name, version))


def get_libarch_url(id_, name, version):
    return "%s/%s" % (config['DL_PIO_URL'], get_libarch_relpath(id_, name,
                                                                version))


def get_libexample_relpath(id_):
    id_ = int(id_)
    assert id_ > 0
    return join("libraries", "examples", str(int(ceil(id_/100))), str(id_))


def get_libexample_dir(id_):
    return join(config['DL_PIO_DIR'], get_libexample_relpath(id_))


def get_libexample_url(id_, name):
    return "%s/%s/%s" % (config['DL_PIO_URL'], get_libexample_relpath(id_),
                         name)


def validate_libconf(data):
    fields = set(data.keys())
    if not fields.issuperset(set(["name", "keywords", "description"])):
        raise InvalidLibConf(
            "The 'name, keywords and description' fields are required")

    # if github-based project
    if "repository" in data:
        repo = data['repository']
        if (repo.get("type", None) == "git" and
                "github.com" in repo.get("url", None)):
            return data

    # if CVS-based
    if "author" not in data or "name" not in data['author']:
        raise InvalidLibConf("The 'author' field is required")
    elif ("repository" in data and
          data['repository'].get("type", None) in ("git", "svn")):
        return data

    # if self-hosted
    if "version" not in data:
        raise InvalidLibConf("The 'version' field is required")
    elif "downloadUrl" not in data:
        raise InvalidLibConf("The 'downloadUrl' field is required")

    return data
