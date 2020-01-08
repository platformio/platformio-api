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
import logging.config
import os


VERSION = (1, 21, 1)
__version__ = ".".join([str(s) for s in VERSION])

__title__ = "platformio-api"
__description__ = ("An API for PlatformIO")
__url__ = "https://github.com/ivankravets/platformio-api"

__author__ = "Ivan Kravets"
__email__ = "me@ikravets.com"

__license__ = "MIT License"
__copyright__ = "Copyright (C) 2014-2017 Ivan Kravets"

config = dict(
    SQLALCHEMY_DATABASE_URI=None,
    GITHUB_LOGIN=None,
    GITHUB_PASSWORD=None,
    DL_PIO_DIR=None,
    DL_PIO_URL=None,
    MAX_DLFILE_SIZE=1024 * 1024 * 150,  # 150 Mb

    # Fuzzy search will not be applied to words shorter than the value below
    SOLR_FUZZY_MIN_WORD_LENGTH=3,
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
try:
    from time import tzset
    tzset()
except ImportError:
    pass
