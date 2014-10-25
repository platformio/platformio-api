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
            self.config_origin = get(lib.conf_url).text
            self.config = self.clean_dict(
                validate_libconf(json.loads(self.config_origin)))
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
        for (key, _) in (data.iteritems() if isinstance(data, dict) else
                         enumerate(data)):
            if isinstance(data[key], dict) or isinstance(data[key], list):
                data[key] = self.clean_dict(data[key])
            elif isinstance(data[key], basestring):
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

        logger.info("Library is out-of-date: %s", self.lib.conf_url)
        self.lib.conf_sha1 = config_sha1
        self.lib.updated = datetime.utcnow()

        # update versions data
        self.lib.latest_version_id = self.sync_version(version)

        # FTS defaults
        if self.lib.fts is None:
            self.lib.fts = models.LibFTS(name=self.config['name'])
        self.lib.fts.name = self.config['name']
        self.lib.fts.description = self.config.get("description", None)

        # update authors data wit new from conf or CVS
        self.config['authors'] = self.sync_authors(
            self.config.get("authors", None))
        self.config['keywords'] = self.sync_keywords(
            self.config.get("keywords", []))
        self.config['frameworks'] = self.sync_frameworks_or_platforms(
            "frameworks", self.config.get("frameworks", []))
        self.config['platforms'] = self.sync_frameworks_or_platforms(
            "platforms", self.config.get("platforms", []))

        # store library registry ID
        self.config['id'] = self.lib.id

        # archive current library version
        self.archive()

        return self.config['id']

    def sync_version(self, version):
        try:
            version = (db_session.query(models.LibVersions).filter(
                models.LibVersions.lib_id == self.lib.id,
                models.LibVersions.name == version['name']).one())
        except NoResultFound:
            version = models.LibVersions(**version)
            self.lib.versions.append(version)
            db_session.flush()
        return version.id

    def sync_authors(self, confauthors):
        authors = []
        itemtpl = dict(
            email=None,
            url=None,
            maintainer=False
        )
        if confauthors:
            if not isinstance(confauthors, list):
                confauthors = [confauthors]
            for item in confauthors:
                tmp = itemtpl.copy()
                tmp.update(item)
                authors.append(tmp)
        elif self.cvsclient and self.cvsclient.get_type() == "github":
            tmp = itemtpl.copy()
            tmp.update(self.cvsclient.get_owner())
            authors.append(tmp)
        else:
            raise NotImplementedError()

        authornames = [item['name'] for item in authors]

        # delete obsolete authors
        self.lib.authors = []

        query = db_session.query(models.Authors).filter(
            models.Authors.name.in_(authornames))
        existing = set()
        for _author in query.all():
            for item in authors:
                if item['name'] != _author.name:
                    continue
                existing.add(_author.name)
                _la = models.LibsAuthors(maintainer=item['maintainer'])
                _la.author = _author
                self.lib.authors.append(_la)

        for name in (set(authornames) - existing):
            for item in authors:
                if item['name'] != name:
                    continue
                _la = models.LibsAuthors(maintainer=item['maintainer'])
                _la.author = models.Authors(
                    name=item['name'],
                    email=item['email'],
                    url=item['url']
                )
                self.lib.authors.append(_la)

        # save in string format for FTS
        self.lib.fts.authornames = ",".join(authornames)
        return authors

    def sync_keywords(self, keywords):
        keywords = self._clean_keywords(keywords)

        # delete obsolete keywords
        self.lib.keywords = []

        query = db_session.query(models.Keywords).filter(
            models.Keywords.name.in_(keywords))
        existing = set()
        for item in query.all():
            existing.add(item.name)
            self.lib.keywords.append(item)

        for item in (set(keywords) - existing):
                self.lib.keywords.append(models.Keywords(name=item))

        # save in string format for FTS
        self.lib.fts.keywords = ",".join(keywords)
        return keywords

    def _clean_keywords(self, keywords):
        result = []
        keywords = (",".join(keywords) if isinstance(keywords, list) else
                    keywords)
        for item in keywords.split(","):
            item = item.strip().lower()
            if len(item) and item not in result:
                result.append(item)
        return result

    def sync_frameworks_or_platforms(self, what, items):
        assert what in ("frameworks", "platforms")
        if not isinstance(items, list):
            items = [i.strip().lower() for i in items.split(",")]

        dbitems = []
        if items:
            _model = getattr(models, what.title())
            dbitems = db_session.query(_model).filter(
                _model.name.in_(items)).all()

        # assert if invalid items
        assert len(items) == len(dbitems)
        # update items in DB
        setattr(self.lib, what, dbitems)
        # save in string format for FTS
        setattr(self.lib.fts, what + "list", ",".join([
            "%s:%s" % (item.name, item.title) for item in dbitems
        ]))

        return items

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

            # put original library.json & modified .library.json
            with open(join(archdir, ".library.json"), "w") as f:
                json.dump(self.config, f, indent=4)
            with open(join(archdir, "library.json"), "w") as f:
                f.write(self.config_origin)

            # pack lib's files
            archive_path = util.get_libarch_path(
                self.lib.id,
                self.lib.latest_version_id
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
                    exmfiles += glob(
                        join(archdir if inclist is None else srcdir, fmask)
                    )
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
        usednames.sort(key=lambda v: v.upper())
        self.lib.fts.examplefiles = ",".join(usednames)


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
