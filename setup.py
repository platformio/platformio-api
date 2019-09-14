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

from setuptools import find_packages, setup

from platformio_api import (__author__, __description__, __email__,
                            __license__, __title__, __url__, __version__)

setup(
    name=__title__,
    version=__version__,
    description=__description__,
    long_description=open("README.rst").read(),
    author=__author__,
    author_email=__email__,
    url=__url__,
    license=__license__,
    install_requires=[
        "click<6",
        "bottle",
        "pymysql",
        "SQLAlchemy<1.2",
        "requests",
        "PyGithub>=1.26,<2",
        "GitPython",
        "beautifulsoup4",
        "platformio"
    ],
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "platformio-api = platformio_api.__main__:main"
        ]
    }
)
