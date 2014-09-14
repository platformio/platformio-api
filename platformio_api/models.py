# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, String,
                        UniqueConstraint)
from sqlalchemy.dialects.mysql import INTEGER, SMALLINT
from sqlalchemy.orm import relationship

from platformio_api.database import Base


class PendingLibs(Base):
    __tablename__ = "pendinglibs"

    id = Column(INTEGER(unsigned=True), primary_key=True)
    conf_url = Column(String(255), nullable=False, unique=True)
    added = Column(DateTime, nullable=False, default=datetime.utcnow())
    approved = Column(Boolean, nullable=False, default=False)
    processed = Column(Boolean, nullable=False, default=False)


class Authors(Base):
    __tablename__ = "authors"

    id = Column(INTEGER(unsigned=True), primary_key=True)
    name = Column(String(20), nullable=False, unique=True)
    email = Column(String(50))
    url = Column(String(100))


class Keywords(Base):
    __tablename__ = "keywords"

    id = Column(INTEGER(unsigned=True), primary_key=True)
    name = Column(String(20), unique=True, nullable=False)


class Attributes(Base):
    __tablename__ = "attributes"

    id = Column(INTEGER(unsigned=True), primary_key=True)
    name = Column(String(20), unique=True, nullable=False)


class Libs(Base):
    __tablename__ = "libs"

    id = Column(INTEGER(unsigned=True), primary_key=True)
    author_id = Column(INTEGER(unsigned=True), ForeignKey("authors.id"))
    latest_version_id = Column(INTEGER(unsigned=True))
    conf_url = Column(String(200), nullable=False)
    conf_sha1 = Column(String(40))
    example_nums = Column(SMALLINT(unsigned=True))
    updated = Column(DateTime, nullable=False, default=datetime.utcnow(),
                     index=True)
    synced = Column(DateTime, nullable=False, default=datetime.utcnow())

    author = relationship("Authors", uselist=False, lazy="joined",
                          backref="libs")
    fts = relationship("LibFTS", uselist=False, lazy="joined", cascade="all")
    versions = relationship("LibVersions", cascade="all")
    examples = relationship("LibExamples", cascade="all,delete-orphan")
    dllog = relationship("LibDLLog", cascade="all")
    dlstats = relationship("LibDLStats", uselist=False, cascade="all")
    attributes = relationship("Attributes", secondary="libs_attributes",
                              cascade="all")
    keywords = relationship("Keywords", secondary="libs_keywords",
                            cascade="all")


class LibFTS(Base):
    __tablename__ = "lib_fts"

    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(String(255))
    keywords = Column(String(255))


class LibVersions(Base):
    __tablename__ = "lib_versions"
    __table_args__ = (UniqueConstraint("lib_id", "name"),)

    id = Column(INTEGER(unsigned=True), primary_key=True)
    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    nullable=False)
    name = Column(String(20))
    released = Column(DateTime, nullable=False, default=datetime.utcnow())


class LibExamples(Base):
    __tablename__ = "lib_examples"

    id = Column(INTEGER(unsigned=True), primary_key=True)
    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    nullable=False)
    name = Column(String(30))


class LibDLLog(Base):
    __tablename__ = "lib_dllog"

    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    primary_key=True)
    ip = Column(INTEGER(unsigned=True), primary_key=True, autoincrement=False)
    date = Column(DateTime, nullable=False, default=datetime.utcnow())


class LibDLStats(Base):
    __tablename__ = "lib_dlstats"

    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    primary_key=True)
    day = Column(INTEGER(unsigned=True), nullable=False, index=True)
    week = Column(INTEGER(unsigned=True), nullable=False, index=True)
    month = Column(INTEGER(unsigned=True), nullable=False, index=True)


class LibsAttributes(Base):
    __tablename__ = "libs_attributes"

    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    primary_key=True)
    attribute_id = Column(INTEGER(unsigned=True), ForeignKey("attributes.id"),
                          primary_key=True)
    value = Column(String(100), nullable=False)


class LibsKeywords(Base):
    __tablename__ = "libs_keywords"

    lib_id = Column(INTEGER(unsigned=True), ForeignKey("libs.id"),
                    primary_key=True)
    keyword_id = Column(INTEGER(unsigned=True), ForeignKey("keywords.id"),
                        primary_key=True)
