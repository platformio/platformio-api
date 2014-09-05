# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

from datetime import datetime

from sqlalchemy import and_, distinct, func
from sqlalchemy.orm.exc import NoResultFound

from platformio_api.database import db_session, Match
from platformio_api.exception import APIBadRequest, APINotFound
from platformio_api.models import (Authors, Keywords, LibDLLog, LibDLStats,
                                   LibExamples, LibFTS, Libs, LibsKeywords,
                                   LibVersions)
from platformio_api.util import get_libarch_url, ip2int


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
            query = db_session.query(func.count(distinct(LibFTS.lib_id)))
        else:
            query = db_session.query(LibFTS, Authors.name, LibDLStats.month,
                                     Libs.example_nums)

        query = query.join(Libs, Authors, LibDLStats)

        if _authors:
            query = query.filter(Authors.name.in_(_authors))
        # else:
        #     query = query.with_hint(Authors, "FORCE INDEX(PRIMARY)")

        if _keywords:
            query = query.join(LibsKeywords).join(
                Keywords, and_(Keywords.name.in_(_keywords), Keywords.id ==
                               LibsKeywords.keyword_id))
            if not count:
                query = query.group_by(LibFTS.lib_id)

        if _words:
            query = query.filter(
                Match([LibFTS.name, LibFTS.description, LibFTS.keywords],
                      " ".join(_words)))
        elif not count:
            query = query.order_by(LibDLStats.month.desc())

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
            LibFTS, LibVersions, Authors, LibDLStats,
            func.group_concat(LibExamples.name)).join(
                Libs, Authors, LibDLStats).join(
                LibVersions, LibVersions.id == Libs.latest_version_id
                ).outerjoin(LibExamples).filter(
            LibFTS.name == self.name).group_by(Libs.id)
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
                LibFTS.lib_id, LibVersions.id, LibVersions.name).outerjoin(
                    LibVersions, and_(LibVersions.lib_id == LibFTS.lib_id,
                                      LibVersions.name == self.version)
                ).filter(LibFTS.name == self.name)
        else:
            query = db_session.query(
                LibFTS.lib_id, LibVersions.id, LibVersions.name).join(
                Libs).join(LibVersions,
                           LibVersions.id == Libs.latest_version_id).filter(
                LibFTS.name == self.name)
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
            query = db_session.query(LibDLLog).filter(
                LibDLLog.lib_id == lib_id, LibDLLog.ip == ip_int)
            item = query.one()
            item.date = datetime.utcnow()
        except NoResultFound:
            db_session.query(LibDLStats).filter(
                LibDLStats.lib_id == lib_id).update({
                    LibDLStats.day: LibDLStats.day + 1,
                    LibDLStats.week: LibDLStats.week + 1,
                    LibDLStats.month: LibDLStats.month + 1
                })
            db_session.add(LibDLLog(lib_id=lib_id, ip=ip_int))

        db_session.commit()


class LibVersionAPI(APIBase):

    def __init__(self, names):
        self.names = names
        assert isinstance(names, list)

    def get_result(self):
        result = dict()
        query = db_session.query(
            LibFTS.name, LibVersions.name).join(Libs).join(
                LibVersions, LibVersions.id == Libs.latest_version_id
                ).filter(LibFTS.name.in_(self.names))
        result = {i[0]: i[1] for i in query.all()}
        for name in self.names:
            if name not in result:
                result[name] = None
        return result
