# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

import json
import logging.config
import os
from time import tzset


VERSION = (0, 4, 0)
__version__ = ".".join([str(s) for s in VERSION])

__title__ = "platformio-api"
__description__ = ("An API for PlatformIO")
__url__ = "https://github.com/ivankravets/platformio-api"

__author__ = "Ivan Kravets"
__email__ = "me@ikravets.com"

__license__ = "MIT License"
__copyright__ = "Copyright (C) 2014-2015 Ivan Kravets"

config = dict(
    SQLALCHEMY_DATABASE_URI=None,
    GITHUB_LOGIN=None,
    GITHUB_PASSWORD=None,
    DL_PIO_DIR=None,
    DL_PIO_URL=None,
    MAX_DLFILE_SIZE=1024*1024*50,  # 50 Mb
    LOGGING=dict(version=1)
)

assert "PIOAPI_CONFIG_PATH" in os.environ
with open(os.environ.get("PIOAPI_CONFIG_PATH")) as f:
    config.update(json.load(f))

# configure logging for packages
logging.basicConfig()
logging.config.dictConfig(config['LOGGING'])

# setup time zone to UTC globally
os.environ['TZ'] = "+00:00"
tzset()
