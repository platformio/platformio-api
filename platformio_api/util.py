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

import json
import logging
import socket
import tarfile
import zipfile
from contextlib import contextmanager
from glob import glob
from math import ceil
from os.path import join
from struct import pack, unpack
from subprocess import check_call

import requests

from platformio_api import __version__, config
from platformio_api.exception import DLFileError, DLFileSizeError

logger = logging.getLogger(__name__)


def load_json(file_path):
    with open(file_path, "r") as f:
        return json.load(f)


def ip2int(ip_string):
    try:
        return unpack("!I", socket.inet_aton(ip_string))[0]
    except socket.error as e:
        logger.error("Illegal IP address string passed to inet_aton: " +
                     ip_string)
        logger.exception(e)
    return 0


def int2ip(ip_int):
    return socket.inet_ntoa(pack("!I", ip_int))


def download_file(source_url, destination_path):
    CHUNK_SIZE = 1024
    downloaded = 0

    f = None
    r = None
    try:
        headers = {"User-Agent": "PlatformIOLibRegistry/%s %s" %
                   (__version__, requests.utils.default_user_agent())}
        r = requests.get(source_url, headers=headers, stream=True)
        if r.status_code != 200:
            raise DLFileError("status=%d, url=%s" % (r.status_code,
                                                     source_url))
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
        with tarfile.open(archive_path) as tar:
            tar.extractall(destination_dir)
    elif archive_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zip_:
            zip_.extractall(destination_dir)
    else:
        raise NotImplementedError()


def get_libarch_relpath(lib_id, version_id):
    lib_id = int(lib_id)
    version_id = int(version_id)
    assert lib_id > 0 and version_id > 0
    return join("libraries", "archives", str(int(ceil(lib_id / 100))),
                "%d.tar.gz" % version_id)


def get_libarch_path(lib_id, version_id):
    return join(config['DL_PIO_DIR'], get_libarch_relpath(lib_id, version_id))


def get_libarch_url(lib_id, version_id):
    return "%s/%s" % (config['DL_PIO_URL'],
                      get_libarch_relpath(lib_id, version_id))


def get_libexample_relpath(lib_id):
    lib_id = int(lib_id)
    assert lib_id > 0
    return join("libraries", "examples", str(int(ceil(lib_id / 100))),
                str(lib_id))


def get_libexample_dir(lib_id):
    return join(config['DL_PIO_DIR'], get_libexample_relpath(lib_id))


def get_libexample_url(lib_id, name):
    return "%s/%s/%s" % (config['DL_PIO_URL'], get_libexample_relpath(lib_id),
                         name)


def get_c_sources(in_dir):
    return glob(join(in_dir, '*.c')) + glob(join(in_dir, '*.cpp')) \
        + glob(join(in_dir, '*.h'))


@contextmanager
def rollback_on_exception(session, logger=None):
    try:
        yield
    except Exception as e:
        session.rollback()
        if logger is not None:
            logger.exception(e)


def rollback_on_exception_decorator(session, logger=None):

    def actual_decorator(f):

        def wrapped(*args, **kwargs):
            with rollback_on_exception(session, logger):
                f(*args, **kwargs)

        return wrapped

    return actual_decorator


def parse_namedtitled_list(ntlist, only_names=False):
    items = []
    for item in ntlist.split(","):
        if ":" not in item:
            continue
        name, title = item.split(":")
        if only_names:
            items.append(name)
        else:
            items.append(dict(name=name, title=title))
    return items


def is_mbed_repository(url):
    return ".mbed.org/" in url or ".mbed.com/" in url
