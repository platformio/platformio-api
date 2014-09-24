# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

import json
import logging
import re
from datetime import datetime, timedelta
from glob import glob
from hashlib import sha1
from os import listdir, makedirs, remove
from os.path import basename, dirname, isdir, isfile, join
from shutil import copy, copytree, rmtree
from tempfile import mkdtemp, mkstemp

from requests import get
from sqlalchemy import and_, func, select
from sqlalchemy.orm.exc import NoResultFound

from platformio_api import models, util
from platformio_api.cvsclient import CVSClientFactory
from platformio_api.database import db_session
from platformio_api.exception import (InvalidLibConf, InvalidLibVersion,
                                      LibArchiveError)
from platformio_api.util import validate_libconf

logger = logging.getLogger(__name__)


class LibSyncer(object):

    def __init__(self, lib):
        assert isinstance(lib, models.Libs)
        self.lib = lib

        try:
            self.config = self.clean_dict(
                validate_libconf(get(lib.conf_url).json()))
            logger.debug("LibConf: %s" % self.config)
        except ValueError:
            raise InvalidLibConf(lib.conf_url)

        self.cvsclient = None
        if "repository" in self.config:
            _type = self.config['repository'].get("type", "").lower()
            _url = self.config['repository'].get("url", "").lower()
            if _type and _url:
                self.cvsclient = CVSClientFactory.newClient(_type, _url)

    def clean_dict(self, data):
        for key in data.keys():
            if isinstance(data[key], dict):
                self.clean_dict(data[key])
            elif isinstance(data[key], list):
                data[key] = [i.strip() for i in data[key]]
            else:
                data[key] = data[key].strip()
        return data

    def get_version(self):
        version = dict(
            name=self.config.get("version", None),
            released=datetime.utcnow()
        )

        if not version['name'] and self.cvsclient:
            path = None
            inclist = self.config.get("include", None)
            if isinstance(inclist, basestring):
                path = inclist
            commit = self.cvsclient.get_last_commit(path=path)
            version['name'] = commit['sha'][:10]
            version['released'] = commit['date']

        if (version['name'] and
                re.match(r"^[a-z0-9\.\-]+$", version['name'], re.I)):
            return version
        else:
            raise InvalidLibVersion(version['name'])

    def calc_config_sha1(self):
        return sha1(str(sorted(self.config.items()))).hexdigest()

    def sync(self):
        # fetch version info
        version = self.get_version()
        self.config['version'] = version['name']

        config_sha1 = self.calc_config_sha1()
        if self.lib.conf_sha1 == config_sha1:
            return True
        else:
            logger.info("Library #%d is out-of-date", self.lib.id)
            self.lib.conf_sha1 = config_sha1

        try:
            version = (db_session.query(models.LibVersions).filter(
                models.LibVersions.lib_id == self.lib.id,
                models.LibVersions.name == version['name']).one())
        except NoResultFound:
            version = models.LibVersions(**version)
            self.lib.versions.append(version)
            db_session.flush()

        self.lib.latest_version_id = version.id
        self.lib.updated = datetime.utcnow()

        # update author info
        self.sync_author()

        # FTS & keywords
        if self.lib.fts is None:
            self.lib.fts = models.LibFTS(name=self.config['name'])
        self.lib.fts.name = self.config['name']
        self.lib.fts.description = self.config.get("description", None)
        self.sync_keywords(self.config.get("keywords", []))

        # archive current version of library
        self.archive()

        return True

    def sync_author(self):
        data = dict(
            name=None,
            email=None,
            url=None
        )
        if "author" in self.config and "name" in self.config['author']:
            data['name'] = self.config['author']['name']
            data['email'] = self.config['author'].get("email", None)
            data['url'] = self.config['author'].get("url", None)
        elif self.cvsclient and self.cvsclient.get_type() == "github":
            data = self.cvsclient.get_owner()
            self.config['author'] = data
        else:
            return

        try:
            author = (db_session.query(models.Authors).filter(
                models.Authors.name == data['name']).one())
            author.email = data['email']
            author.url = data['url']
        except NoResultFound:
            author = models.Authors(**data)
        self.lib.author = author

    def sync_keywords(self, keywords):
        keywords = self._clean_keywords(keywords)

        # delete obsolete keywords
        self.lib.keywords = []

        existing = db_session.query(models.Keywords).filter(
            models.Keywords.name.in_(keywords)).all()
        existingnames = set()
        for item in existing:
            existingnames.add(item.name)
            self.lib.keywords.append(item)

        for item in (set(keywords) - existingnames):
                self.lib.keywords.append(models.Keywords(name=item))

        # save in string format for FTS
        self.lib.fts.keywords = ", ".join(keywords)

    def _clean_keywords(self, keywords):
        result = []
        keywords = (",".join(keywords) if isinstance(keywords, list) else
                    keywords)
        for item in keywords.split(","):
            item = item.strip().lower()
            if len(item) and item not in result:
                result.append(item)
        return result

    def archive(self):
        archdir = mkdtemp()
        srcdir = mkdtemp()

        try:
            if self.cvsclient:
                self.cvsclient.clone(srcdir)
            elif "downloadUrl" in self.config:
                try:
                    tmparh_path = mkstemp(basename(
                        self.config['downloadUrl']))[1]
                    util.download_file(self.config['downloadUrl'], tmparh_path)
                    util.extract_archive(tmparh_path, srcdir)
                finally:
                    remove(tmparh_path)
            else:
                raise LibArchiveError()

            # delete excluded items
            exclist = self.config.get("exclude", [])
            if isinstance(exclist, basestring):
                    exclist = [exclist]
            for pathname in exclist:
                for item in glob(join(srcdir, pathname)):
                    if isfile(item):
                        remove(item)
                    else:
                        rmtree(item)

            inclist = self.config.get("include", None)
            if inclist is None:
                archdir, srcdir = srcdir, archdir
            elif isinstance(inclist, list):
                for pathname in inclist:
                    for item in glob(join(srcdir, pathname)):
                        dstpath = join(archdir, item[len(srcdir)+1:])
                        if isfile(item):
                            copy(item, dstpath)
                        else:
                            copytree(item, dstpath)
            # if "include" is a string then use it like a "mount" point
            elif isinstance(inclist, basestring):
                srcpath = join(srcdir, inclist)
                if isfile(srcpath):
                    copy(srcpath, join(archdir, inclist))
                else:
                    for item in listdir(srcpath):
                        itempath = join(srcpath, item)
                        dstpath = join(archdir, item)
                        if isfile(itempath):
                            copy(itempath, dstpath)
                        else:
                            copytree(itempath, dstpath)

            # put library.json
            with open(join(archdir, "library.json"), "w") as f:
                json.dump(self.config, f, indent=4)

            # pack lib's files
            archive_path = util.get_libarch_path(
                self.lib.id,
                self.config['name'],
                self.config['version']
            )
            if not isdir(dirname(archive_path)):
                makedirs(dirname(archive_path))
            util.create_archive(archive_path, archdir)
            assert isfile(archive_path)

            # fetch examples
            exmglobs = self.config.get("examples", None)
            exmfiles = []
            if exmglobs is None:
                for ext in ("*.ino", "*.pde"):
                    exmfiles += glob(join(archdir, "[Ee]xamples", "*", ext))
            else:
                if not isinstance(exmglobs, list):
                    exmglobs = [exmglobs]
                for fmask in exmglobs:
                    exmfiles += glob(join(srcdir, fmask))

            self.sync_examples(exmfiles)
        finally:
            rmtree(archdir)
            rmtree(srcdir)

    def sync_examples(self, files):
        # clean previous examples
        self.lib.examples = []
        usednames = []

        exmdir = util.get_libexample_dir(self.lib.id)
        if isdir(exmdir):
            rmtree(exmdir)

        if files:
            makedirs(exmdir)

        for f in files:
            name = basename(f)
            if name in usednames:
                continue
            copy(f, join(exmdir, name))
            self.lib.examples.append(models.LibExamples(name=name))
            usednames.append(name)

        self.lib.example_nums = len(usednames)


def process_pending_libs():
    query = db_session.query(models.PendingLibs, models.Libs.id).filter(
        ~models.PendingLibs.processed, models.PendingLibs.approved).outerjoin(
            models.Libs, models.PendingLibs.conf_url == models.Libs.conf_url)
    for (item, lib_id) in query.all():
        if lib_id:
            continue
        try:
            lib = models.Libs(conf_url=item.conf_url)
            lib.dlstats = models.LibDLStats(day=0, week=0, month=0)
            db_session.add(lib)

            ls = LibSyncer(lib)
            ls.sync()

            item.processed = True
            db_session.commit()
        except Exception as e:
            db_session.rollback()
            logger.exception(e)


def sync_libs():
    query = db_session.query(models.Libs).filter(
        models.Libs.synced < datetime.utcnow() - timedelta(days=1))
    for item in query.all():
        try:
            ls = LibSyncer(item)
            if ls.sync():
                item.synced = datetime.utcnow()

            db_session.commit()
        except Exception as e:
            db_session.rollback()
            logger.exception(e)


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
