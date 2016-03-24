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

from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.mysql import INTEGER, SMALLINT
from sqlalchemy.orm import relationship

from platformio_api.database import Base


class PendingLibs(Base):
    __tablename__ = "pendinglibs"

    id = Column(INTEGER(unsigned=True), primary_key=True)
    conf_url = Column(String(255), nullable=False, unique=True)
    added = Column(DateTime, nullable=False, default=datetime.utcnow)
    approved = Column(Boolean, nullable=False, default=False)
    processed = Column(Boolean, nullable=False, default=False)


class Attributes(Base):
    __tablename__ = "attributes"

    id = Column(INTEGER(unsigned=True), primary_key=True)
    name = Column(String(20), unique=True, nullable=False)


class Authors(Base):
    __tablename__ = "authors"

    id = Column(INTEGER(unsigned=True), primary_key=True)
    name = Column(String(30), nullable=False, unique=True)
    email = Column(String(50))
    url = Column(String(100))


class Frameworks(Base):
    __tablename__ = "frameworks"

    id = Column(INTEGER(unsigned=True), primary_key=True)
    name = Column(String(20), unique=True, nullable=False)
    title = Column(String(20), nullable=False)


class Keywords(Base):
    __tablename__ = "keywords"

    id = Column(INTEGER(unsigned=True), primary_key=True)
    name = Column(String(20), unique=True, nullable=False)


class Libs(Base):
    __tablename__ = "libs"

    id = Column(INTEGER(unsigned=True), primary_key=True, autoincrement=False)
    latest_version_id = Column(INTEGER(unsigned=True))
    conf_url = Column(String(200), nullable=False)
    conf_sha1 = Column(String(40))
    example_nums = Column(SMALLINT(unsigned=True))
    added = Column(DateTime, nullable=False, default=datetime.utcnow,
                   index=True)
    updated = Column(DateTime, nullable=False, default=datetime.utcnow,
                     index=True)
    synced = Column(DateTime, nullable=False, default=datetime.utcnow)
    active = Column(Boolean, nullable=False, default=True)

    # relationships
    attributes = relationship("LibsAttributes", cascade="all,delete-orphan")
    authors = relationship("LibsAuthors", cascade="all,delete-orphan")
    examples = relationship("LibExamples", cascade="all,delete-orphan")
    frameworks = relationship("Frameworks", secondary="libs_frameworks",
                              cascade="save-update, merge, refresh-expire, "
                                      "expunge")
    fts = relationship("LibFTS", uselist=False, lazy="joined", innerjoin=True,
                       cascade="all")
    dllog = relationship("LibDLLog", cascade="all")
    dlstats = relationship("LibDLStats", uselist=False, lazy="joined",
                           innerjoin=True, cascade="all")
    keywords = relationship("Keywords", secondary="libs_keywords",
                            cascade="save-update, merge, refresh-expire, "
                                    "expunge")
    platforms = relationship("Platforms", secondary="libs_platforms",
                             cascade="save-update, merge, refresh-expire, "
                                     "expunge")
    versions = relationship("LibVersions", cascade="all")


class LibsAttributes(Base):
    __tablename__ = "libs_attributes"

    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    primary_key=True)
    attribute_id = Column(INTEGER(unsigned=True), ForeignKey("attributes.id"),
                          primary_key=True)
    value = Column(String(255), nullable=False)
    attribute = relationship("Attributes", lazy="joined", innerjoin=True)


class LibsAuthors(Base):
    __tablename__ = "libs_authors"

    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    primary_key=True)
    author_id = Column(INTEGER(unsigned=True), ForeignKey("authors.id"),
                       primary_key=True)
    maintainer = Column(Boolean, nullable=False, default=False)
    author = relationship("Authors", lazy="joined")


class LibsFrameworks(Base):
    __tablename__ = "libs_frameworks"

    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    primary_key=True)
    framework_id = Column(INTEGER(unsigned=True), ForeignKey("frameworks.id"),
                          primary_key=True)


class LibDLLog(Base):
    __tablename__ = "lib_dllog"

    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    primary_key=True)
    ip = Column(INTEGER(unsigned=True), primary_key=True, autoincrement=False)
    date = Column(DateTime, nullable=False, default=datetime.utcnow)


class LibDLStats(Base):
    __tablename__ = "lib_dlstats"

    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    primary_key=True)
    day = Column(INTEGER(unsigned=True), nullable=False, index=True)
    week = Column(INTEGER(unsigned=True), nullable=False, index=True)
    month = Column(INTEGER(unsigned=True), nullable=False, index=True)


class LibExamples(Base):
    __tablename__ = "lib_examples"

    id = Column(INTEGER(unsigned=True), primary_key=True)
    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    nullable=False)
    name = Column(String(100))


class LibFTS(Base):
    __tablename__ = "lib_fts"

    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    primary_key=True)
    name = Column(String(50), nullable=False)
    description = Column(String(255), nullable=False)
    keywords = Column(String(255), nullable=False)
    examplefiles = Column(Text(), nullable=False)
    authornames = Column(String(255), nullable=False)
    frameworkslist = Column(String(255), nullable=False)
    platformslist = Column(Text(), nullable=False)


class LibsKeywords(Base):
    __tablename__ = "libs_keywords"

    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    primary_key=True)
    keyword_id = Column(INTEGER(unsigned=True), ForeignKey("keywords.id"),
                        primary_key=True)


class LibsPlatforms(Base):
    __tablename__ = "libs_platforms"

    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    primary_key=True)
    platform_id = Column(INTEGER(unsigned=True), ForeignKey("platforms.id"),
                         primary_key=True)


class LibVersions(Base):
    __tablename__ = "lib_versions"
    __table_args__ = (UniqueConstraint("lib_id", "name"),)

    id = Column(INTEGER(unsigned=True), primary_key=True)
    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    nullable=False)
    name = Column(String(20))
    released = Column(DateTime, nullable=False, default=datetime.utcnow)


class Platforms(Base):
    __tablename__ = "platforms"

    id = Column(INTEGER(unsigned=True), primary_key=True)
    name = Column(String(20), unique=True, nullable=False)
    title = Column(String(20), nullable=False)
