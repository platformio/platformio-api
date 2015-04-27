# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

import logging
from os import remove
from shutil import rmtree

from platformio_api.database import db_session
from platformio_api.models import Libs
from platformio_api.util import get_libarch_path, get_libexample_dir

logger = logging.getLogger(__name__)


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
            try:
                remove(get_libarch_path(lib_id, version.id))
            except OSError:
                logger.warning("Unable to remove lib #%s version #%s archive. "
                               "Probably it was removed earlier."
                               % (lib_id, version.id))

        # remove information about library from database
        db_session.delete(lib)

        db_session.commit()
    except Exception as e:
        db_session.rollback()
        logger.exception(e)
