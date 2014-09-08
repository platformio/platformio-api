# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

import logging
from datetime import datetime

from requests import get
from sqlalchemy import and_, distinct, func
from sqlalchemy.orm.exc import NoResultFound

from platformio_api import models
from platformio_api.database import db_session, Match
from platformio_api.exception import APIBadRequest, APINotFound, InvalidLibConf
from platformio_api.util import get_libarch_url, ip2int, validate_libconf

logger = logging.getLogger(__name__)


class APIBase(object):

    def get_result(self):
        raise NotImplementedError()


class LibSearchAPI(APIBase):

    ITEMS_PER_PAGE = 50

    def __init__(self, query=None, page=1, per_page=50):
        if not query:
            raise APIBadRequest("Please specify '?query' parameter")
        self.query = self._parse_query(query)
        self.page = page
        self.per_page = per_page

        self.total = self.get_total()

        if self.per_page < 1 or self.per_page > self.ITEMS_PER_PAGE:
            self.per_page = self.ITEMS_PER_PAGE

        if self.page < 1 or ((self.page - 1) * self.per_page) > self.total:
            self.page = 1

    def get_total(self):
        return self._prepare_sqlquery(count=True).scalar()

    def get_result(self):
        items = []
        query = self._prepare_sqlquery().limit(self.per_page).offset(
            (self.page - 1) * self.per_page)

        for data in query.all():
            fts, author_name, dlmonth, example_nums = data
            items.append(dict(
                name=fts.name,
                description=fts.description,
                keywords=[k.strip() for k in fts.keywords.split(",")],
                author_name=author_name,
                dlmonth=dlmonth,
                example_nums=example_nums
            ))
        return dict(
            total=self.total,
            page=self.page,
            items=items
        )

    def _parse_query(self, query):
        authors = []
        keywords = []
        words = []

        _quote = "\""
        _author = None
        _keyword = None

        for token in query.split(" "):
            token = token.strip()
            if not len(token):
                continue
            if _author is not None:
                _author += " %s" % token
            elif _keyword is not None:
                _keyword += " %s" % token
            elif token.startswith("author:"):
                _author = token[7:]
            elif token.startswith("keyword:"):
                _keyword = token[8:]
            else:
                words.append(token)

            if _author:
                if not _author.startswith(_quote):
                    authors.append(_author)
                    _author = None
                elif _author.startswith(_quote) and _author.endswith(_quote):
                    authors.append(_author[1:-1])
                    _author = None

            if _keyword:
                if not _keyword.startswith(_quote):
                    keywords.append(_keyword)
                    _keyword = None
                elif _keyword.startswith(_quote) and _keyword.endswith(_quote):
                    keywords.append(_keyword[1:-1])
                    _keyword = None

        # invalid query
        if _author or _keyword:
            return ([], [], [i.strip() for i in query.split(" ") if len(i)])
        else:
            return (authors, keywords, words)

    def _prepare_sqlquery(self, count=False):
        _authors, _keywords, _words = self.query

        if count:
            query = db_session.query(
                func.count(distinct(models.LibFTS.lib_id))
            )
        else:
            query = db_session.query(
                models.LibFTS, models.Authors.name, models.LibDLStats.month,
                models.Libs.example_nums
            )

        query = query.join(models.Libs, models.Authors, models.LibDLStats)

        if _authors:
            query = query.filter(models.Authors.name.in_(_authors))
        # else:
        #     query = query.with_hint(Authors, "FORCE INDEX(PRIMARY)")

        if _keywords:
            query = query.join(models.LibsKeywords).join(
                models.Keywords,
                and_(models.Keywords.name.in_(_keywords),
                     models.Keywords.id == models.LibsKeywords.keyword_id)
            )
            if not count:
                query = query.group_by(models.LibFTS.lib_id)

        if _words:
            query = query.filter(
                Match([models.LibFTS.name, models.LibFTS.description,
                       models.LibFTS.keywords], " ".join(_words)))
        elif not count:
            query = query.order_by(models.LibDLStats.month.desc())

        return query


class LibInfoAPI(APIBase):

    def __init__(self, name):
        self.name = name.strip()

    def get_result(self):
        result = dict(
            author=dict(),
            dlstats=dict(),
            version=dict()
        )
        query = db_session.query(
            models.LibFTS, models.LibVersions, models.Authors,
            models.LibDLStats, func.group_concat(models.LibExamples.name)
        ).join(models.Libs, models.Authors, models.LibDLStats).join(
            models.LibVersions,
            models.LibVersions.id == models.Libs.latest_version_id
        ).outerjoin(models.LibExamples).filter(
            models.LibFTS.name == self.name).group_by(models.Libs.id)
        try:
            data = query.one()
        except NoResultFound:
            raise APINotFound("Unknown library with name '%s'" % self.name)

        lib_id = data[0].lib_id

        result['id'] = lib_id
        for k in ("name", "description"):
            result[k] = getattr(data[0], k)
        result['keywords'] = [k.strip() for k in data[0].keywords.split(",")]

        for k in ("name", "email", "url"):
            result['author'][k] = getattr(data[2], k)
        for k in ("day", "week", "month"):
            result['dlstats'][k] = getattr(data[3], k)

        result['examples'] = data[4].split(",") if data[4] else []

        # latest version
        result['version'] = dict(
            name=data[1].name,
            released=data[1].released.strftime("%Y-%m-%dT%H:%M:%S")
        )

        return result


class LibDownloadAPI(APIBase):

    def __init__(self, name, ip=None, version=None):
        self.name = name.strip()
        self.ip = ip
        self.version = version.strip() if version else None

    def get_result(self):
        if self.version:
            query = db_session.query(
                models.LibFTS.lib_id, models.LibVersions.id,
                models.LibVersions. name
            ).outerjoin(
                models.LibVersions,
                and_(models.LibVersions.lib_id == models.LibFTS.lib_id,
                     models.LibVersions.name == self.version)
            ).filter(models.LibFTS.name == self.name)
        else:
            query = db_session.query(
                models.LibFTS.lib_id, models.LibVersions.id,
                models.LibVersions.name
            ).join(models.Libs).join(
                models.LibVersions,
                models.LibVersions.id == models.Libs.latest_version_id
            ).filter(models.LibFTS.name == self.name)
        try:
            data = query.one()
        except NoResultFound:
            raise APINotFound("Unknown library with name '%s'" % self.name)

        lib_id = data[0]
        version_id = data[1]
        version_name = data[2]

        if not version_id:
            raise APINotFound("Unknown version '%s'" % self.version)

        self._logdlinfo(lib_id)

        result = dict(
            url=get_libarch_url(lib_id, self.name, version_name),
            version=version_name
        )
        return result

    def _logdlinfo(self, lib_id):
        if not self.ip:
            return

        ip_int = ip2int(self.ip)
        try:
            query = db_session.query(models.LibDLLog).filter(
                models.LibDLLog.lib_id == lib_id, models.LibDLLog.ip == ip_int)
            item = query.one()
            item.date = datetime.utcnow()
        except NoResultFound:
            db_session.query(models.LibDLStats).filter(
                models.LibDLStats.lib_id == lib_id
            ).update({
                models.LibDLStats.day: models.LibDLStats.day + 1,
                models.LibDLStats.week: models.LibDLStats.week + 1,
                models.LibDLStats.month: models.LibDLStats.month + 1
            })
            db_session.add(models.LibDLLog(lib_id=lib_id, ip=ip_int))

        db_session.commit()


class LibVersionAPI(APIBase):

    def __init__(self, names):
        self.names = names
        assert isinstance(names, list)

    def get_result(self):
        result = dict()
        query = db_session.query(
            models.LibFTS.name, models.LibVersions.name
        ).join(models.Libs).join(
            models.LibVersions,
            models.LibVersions.id == models.Libs.latest_version_id
        ).filter(models.LibFTS.name.in_(self.names))
        result = {i[0]: i[1] for i in query.all()}
        for name in self.names:
            if name not in result:
                result[name] = None
        return result


class LibRegisterAPI(APIBase):

    def __init__(self, conf_url):
        self.conf_url = conf_url.strip() if conf_url else None
        if not self.conf_url:
            raise APIBadRequest("Please specify the library configuration URL")

    def get_result(self):
        result = dict(
            successed=False,
            message=None
        )

        config = dict()
        try:
            r = get(self.conf_url)
            try:
                config = r.json()
            except ValueError:
                raise InvalidLibConf(self.conf_url)

            # validate fields
            config = validate_libconf(config)

            # check for name duplicates
            query = db_session.query(func.count(1)).filter(
                models.LibFTS.name == config['name'])
            if query.scalar():
                raise InvalidLibConf("The library with name '%s' is already "
                                     "registered" % config['name'])
            # check for pending duplicates
            query = db_session.query(func.count(1)).filter(
                models.PendingLibs.conf_url == self.conf_url)
            if query.scalar():
                raise InvalidLibConf("The library is already registered")

            db_session.add(models.PendingLibs(conf_url=self.conf_url))
            db_session.commit()
            result['successed'] = True
            result['message'] = ("The library has been successfully "
                                 "registered and is waiting for moderation")
        except InvalidLibConf as e:
            result['message'] = str(e)
        except Exception as e:
            logger.exception(e)
            result['message'] = ("Could not retrieve a library JSON data by "
                                 "this URL -> " + self.conf_url)
        return result
