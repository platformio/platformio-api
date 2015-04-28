# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

import logging
from os import remove
from shutil import rmtree

from sqlalchemy import func

from platformio_api.database import db_session
from platformio_api.models import Libs, LibVersions
from platformio_api.util import get_libarch_path, get_libexample_dir

logger = logging.getLogger(__name__)


def remove_library_version_archive(lib_id, version_id):
    try:
        remove(get_libarch_path(lib_id, version_id))
    except OSError:
        logger.warning("Unable to remove lib #%s version #%s archive. Probably"
                       " it was removed earlier." % (lib_id, version_id))


def delete_library(lib_id):
    try:
        lib = db_session.query(Libs).get(lib_id)

        # remove whole examples dir (including all examples files)
        try:
            rmtree(get_libexample_dir(lib_id))
        except OSError:
            logger.warning("Unable to remove lib #%s examples directory. "
                           "Probably it was removed earlier." % lib_id)

        # remove all versions archives
        for version in lib.versions:
            remove_library_version_archive(lib_id, version.id)

        # remove information about library from database
        db_session.delete(lib)

        db_session.commit()
    except Exception as e:
        db_session.rollback()
        logger.exception(e)


def cleanup_lib_versions(keep_versions):
    try:
        libs_query = db_session\
            .query(Libs, func.count(Libs.versions))\
            .join(Libs.versions)\
            .group_by(Libs)
        for lib, versions_count in libs_query.all():
            if versions_count <= keep_versions:
                continue
            versions_query = db_session.query(LibVersions)\
                .with_parent(lib)\
                .order_by(LibVersions.released.desc())
            for version in versions_query.all()[keep_versions:]:
                remove_library_version_archive(lib.id, version.id)
                db_session.delete(version)
        db_session.commit()
    except Exception as e:
        db_session.rollback()
        logger.exception(e)
