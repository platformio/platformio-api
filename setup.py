# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

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
        "click",
        "bottle",
        "MySQL-python",
        "SQLAlchemy",
        "requests"
    ],
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "platformio-api = platformio_api.__main__:main"
        ]
    }
)
