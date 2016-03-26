# Copyright 2014-2015 Ivan Kravets <me@ikravets.com>
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
from shutil import rmtree

from sqlalchemy import and_, func, select

from platformio_api import models, util
from platformio_api.crawler import LibSyncer
from platformio_api.database import db_session

logger = logging.getLogger(__name__)


def process_pending_libs():

    def get_free_lib_id():
        free_id = 0
        query = db_session.query(models.Libs.id).order_by(
            models.Libs.id.asc())
        for item in query.all():
            free_id += 1
            if item[0] > free_id:
                break
        return free_id

    query = db_session.query(models.PendingLibs, models.Libs.id).filter(
        ~models.PendingLibs.processed, models.PendingLibs.approved).outerjoin(
            models.Libs, models.PendingLibs.conf_url == models.Libs.conf_url)

    were_synced = False
    for (item, lib_id) in query.all():
        if lib_id:
            continue
        with util.rollback_on_exception(db_session, logger):
            lib = models.Libs(
                id=get_free_lib_id(),
                conf_url=item.conf_url)
            lib.dlstats = models.LibDLStats(day=0, week=0, month=0)
            db_session.add(lib)

            ls = LibSyncer(lib)
            ls.sync()

            item.processed = True
            db_session.commit()

            were_synced = True

    if were_synced:
        optimise_sync_period()


def sync_libs():
    query = db_session.query(models.Libs)\
        .filter(models.Libs.synced < datetime.utcnow() - timedelta(days=1),
                models.Libs.active)
    for item in query.all():
        sync_lib(item)


def sync_lib(item):
    sync_succeeded = False
    with util.rollback_on_exception(db_session, logger):
        ls = LibSyncer(item)
        sync_succeeded = ls.sync()
        if sync_succeeded:
            item.synced = datetime.utcnow()
    item.active = bool(sync_succeeded)
    db_session.commit()


def sync_lib_by_id(lib_id):
    item = db_session.query(models.Libs).get(lib_id)
    if not item:
        print "Library with id={} not found.".format(lib_id)
        return

    sync_lib(item)


def rotate_libs_dlstats():
    # delete obsolete logs
    db_session.query(models.LibDLLog.lib_id).filter(
        models.LibDLLog.date < datetime.utcnow() - timedelta(days=30)).delete()

    db_session.query(models.LibDLStats).update(dict(
        day=0,
        week=select([func.count(1)]).where(and_(
            models.LibDLLog.lib_id == models.LibDLStats.lib_id,
            models.LibDLLog.date > datetime.utcnow() - timedelta(days=7)
        )).as_scalar(),
        month=select([func.count(1)]).where(
            models.LibDLLog.lib_id == models.LibDLStats.lib_id).as_scalar()
    ), synchronize_session=False)

    db_session.commit()


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
