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

import logging
from datetime import datetime, timedelta
from math import ceil
from os import remove
from os.path import isfile, join
from shutil import rmtree
from urlparse import urlparse

import requests
from pkg_resources import parse_version
from sqlalchemy import and_, func, select
from sqlalchemy.orm import lazyload

from platformio_api import config, models, util
from platformio_api.crawler import LibSyncerFactory
from platformio_api.database import db_session
from platformio_api.vcsclient import VCSClientFactory

logger = logging.getLogger(__name__)


def process_pending_libs():

    def get_free_lib_id():
        lib_id = 0
        free_id = 0
        query = db_session.query(models.Libs.id).order_by(models.Libs.id.asc())
        for (lib_id, ) in query.all():
            free_id += 1
            if lib_id > free_id:
                break
        if lib_id == free_id:
            free_id += 1
        return free_id

    query = db_session.query(models.PendingLibs, models.Libs.id).filter(
        ~models.PendingLibs.processed, models.PendingLibs.approved).outerjoin(
            models.Libs, models.PendingLibs.conf_url == models.Libs.conf_url)

    were_synced = False
    for (item, lib_id) in query.all():
        if lib_id:
            continue
        logger.info("Processing pending library: %s", item.conf_url)
        with util.rollback_on_exception(db_session, logger):
            lib = models.Libs(id=get_free_lib_id(), conf_url=item.conf_url)
            lib.dlstats = models.LibDLStats()
            db_session.add(lib)

            ls = LibSyncerFactory.new(lib)
            ls.sync()

            item.processed = True
            db_session.commit()

            were_synced = True
            purge_cache()

    if were_synced:
        optimise_sync_period()


def sync_libs():
    query = db_session.query(models.Libs)\
        .filter(models.Libs.synced < datetime.utcnow() - timedelta(days=1),
                models.Libs.active)
    for item in query.all():
        before = item.updated
        sync_lib(item)
        if before != item.updated:
            purge_cache()


def sync_lib(item):
    sync_succeeded = False
    with util.rollback_on_exception(db_session, logger):
        ls = LibSyncerFactory.new(item)
        sync_succeeded = ls.sync()

    item.sync_failures = 0 if sync_succeeded else item.sync_failures + 1
    item.synced = datetime.utcnow() + timedelta(days=item.sync_failures)

    db_session.commit()


def sync_lib_by_id(lib_id):
    item = db_session.query(models.Libs).get(lib_id)
    if not item:
        print "Library with id={} not found.".format(lib_id)
        return

    sync_lib(item)


def rotate_libs_dlstats():
    today = datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0)

    # delete obsolete logs
    db_session.query(models.LibDLLog.lib_id).filter(
        models.LibDLLog.date < today - timedelta(days=60)).delete()

    db_session.query(models.LibDLStats).update(
        dict(

            day=select([func.count(1)]).where(
                and_(
                    models.LibDLLog.lib_id == models.LibDLStats.lib_id,
                    models.LibDLLog.date >= today)
                ).as_scalar(),

            week=select([func.count(1)]).where(
                and_(
                    models.LibDLLog.lib_id == models.LibDLStats.lib_id,
                    models.LibDLLog.date >= today - timedelta(days=7))
                ).as_scalar(),

            month=select([func.count(1)]).where(
                and_(
                    models.LibDLLog.lib_id == models.LibDLStats.lib_id,
                    models.LibDLLog.date >= today - timedelta(days=30))
                ).as_scalar(),

            day_prev=select([func.count(1)]).where(
                and_(
                    models.LibDLLog.lib_id == models.LibDLStats.lib_id,
                    models.LibDLLog.date < today,
                    models.LibDLLog.date >= today - timedelta(days=1))
                ).as_scalar(),

            week_prev=select([func.count(1)]).where(
                and_(
                    models.LibDLLog.lib_id == models.LibDLStats.lib_id,
                    models.LibDLLog.date < today - timedelta(days=7),
                    models.LibDLLog.date >= today - timedelta(days=14))
                ).as_scalar(),

            month_prev=select([func.count(1)]).where(
                and_(
                    models.LibDLLog.lib_id == models.LibDLStats.lib_id,
                    models.LibDLLog.date < today - timedelta(days=30))
                ).as_scalar()

        ), synchronize_session=False)

    db_session.commit()
    purge_cache()


def remove_library_version_archive(lib_id, version_id):
    try:
        remove(util.get_libarch_path(lib_id, version_id))
    except OSError:
        logger.warning("Unable to remove lib #%s version #%s archive. Probably"
                       " it was removed earlier." % (lib_id, version_id))


@util.rollback_on_exception_decorator(db_session, logger)
def delete_library(lib_id):
    lib = db_session.query(models.Libs).get(lib_id)

    # remove whole examples dir (including all examples files)
    try:
        rmtree(util.get_libexample_dir(lib_id))
    except OSError:
        logger.warning("Unable to remove lib #%s examples directory. "
                       "Probably it was removed earlier." % lib_id)

    # remove all versions archives
    for version in lib.versions:
        remove_library_version_archive(lib_id, version.id)

    # remove information about library from database
    db_session.delete(lib)
    db_session.commit()
    purge_cache()


@util.rollback_on_exception_decorator(db_session, logger)
def cleanup_lib_versions(keep_versions):
    libs_query = db_session\
        .query(models.Libs, func.count(models.Libs.versions))\
        .join(models.Libs.versions)\
        .group_by(models.Libs)
    for lib, versions_count in libs_query.all():
        if versions_count <= keep_versions:
            continue
        versions_query = db_session.query(models.LibVersions)\
            .with_parent(lib)\
            .order_by(models.LibVersions.released.desc())
        for version in versions_query.all()[keep_versions:]:
            remove_library_version_archive(lib.id, version.id)
            db_session.delete(version)
    db_session.commit()
    purge_cache()


@util.rollback_on_exception_decorator(db_session, logger)
def delete_lib_version(version_id):
    query = db_session\
        .query(models.LibVersions, models.Libs)\
        .options(lazyload('*'))\
        .join(models.Libs)\
        .filter(models.LibVersions.id == version_id)
    version, lib = query.one()
    lib_id = lib.id
    lib.latest_version_id = db_session\
       .query(models.LibVersions.id)\
       .with_parent(lib)\
       .filter(models.LibVersions.id != version.id)\
       .order_by(models.LibVersions.released.desc())\
       .limit(1)\
       .scalar()
    db_session.delete(version)
    db_session.commit()
    remove_library_version_archive(lib_id, version_id)
    purge_cache()


@util.rollback_on_exception_decorator(db_session, logger)
def optimise_sync_period():
    libs = db_session.query(models.Libs)
    libs_count = libs.count()
    dt = timedelta(seconds=ceil(86400 / libs_count))  # 24h == 86400s
    new_sync_datetime = datetime.utcnow() - timedelta(hours=24)
    for lib in libs.all():
        lib.synced = new_sync_datetime
        new_sync_datetime += dt
    db_session.commit()


def sync_arduino_libs():

    def _cleanup_url(url):
        for text in (".git", "/"):
            if url.endswith(text):
                url = url[:-len(text)]
        return url

    used_urls = set()
    for item in db_session.query(models.PendingLibs).all():
        used_urls.add(item.conf_url.lower())

    query = db_session\
        .query(models.LibsAttributes.value)\
        .join(models.Attributes)\
        .filter(models.Attributes.name.in_(["homepage", "repository.url"]))
    for (url, ) in query.all():
        url = _cleanup_url(url)
        used_urls.add(url.lower())

    libs_index = requests.get(
        "http://downloads.arduino.cc/libraries/library_index.json").json()
    libs = {}
    for lib in libs_index['libraries']:
        if lib['name'] not in libs or \
           parse_version(lib['version']) > parse_version(
               libs[lib['name']]['version']):
            libs[lib['name']] = lib
    del libs_index

    for lib in libs.values():
        github_url = "https://github.com/{}/{}"
        if "github.com" in lib['website'] and lib['website'].count("/") >= 4:
            github_url = github_url.format(
                *urlparse(lib['website']).path[1:].split("/")[:2])
        else:
            _username, _filename = lib['url'].rsplit("/", 2)[1:]
            github_url = github_url.format(_username,
                                           _filename.rsplit("-", 1)[0])
        github_url = _cleanup_url(github_url)
        if github_url.lower() in used_urls:
            continue

        logger.debug("SyncArduinoLibs: Processing {name}, {website}".format(
            **lib))

        approved = False
        try:
            vcs = VCSClientFactory.newClient("git", github_url)
            default_branch = vcs.default_branch
            assert default_branch
            conf_url = ("https://raw.githubusercontent.com{user_and_repo}/"
                        "{branch}/library.properties".format(
                            user_and_repo=urlparse(github_url).path,
                            branch=default_branch))
            if conf_url.lower() in used_urls:
                continue
            r = requests.get(conf_url)
            r.raise_for_status()
            approved = True
        except Exception:
            conf_url = github_url

        if conf_url.lower() in used_urls:
            continue
        else:
            used_urls.add(conf_url)

        # leave for moderation library with existing name
        if approved:
            query = db_session.query(func.count(1)).filter(
                models.LibFTS.name == lib['name'])
            approved = not query.scalar()

        db_session.add(
            models.PendingLibs(
                conf_url=conf_url, approved=approved))
        db_session.commit()
        logger.info(
            "SyncArduinoLibs: Registered new library {name}, {website}".format(
                **lib))


def purge_cache():
    flag_path = join(config['DL_PIO_DIR'], ".apicacheobsolete.flag")
    if isfile(flag_path):
        return
    with open(flag_path, "w") as fp:
        fp.write("")
