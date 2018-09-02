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

import logging
import re
from datetime import datetime, timedelta
from os.path import basename, join

from sqlalchemy import and_, desc, distinct, func
from sqlalchemy.sql import label
from sqlalchemy.orm.exc import NoResultFound

from platformio_api import config, crawler, models, util
from platformio_api.database import Match, db_session
from platformio_api.exception import APIBadRequest, APINotFound, InvalidLibConf

logger = logging.getLogger(__name__)


class APIBase(object):

    def get_result(self):
        raise NotImplementedError()


class BoardsAPI(APIBase):

    @staticmethod
    def get_result():
        return util.load_json(
            join(config['DL_PIO_DIR'], "api-data", "boards.json"))


class FrameworksAPI(APIBase):

    @staticmethod
    def get_result():
        return util.load_json(
            join(config['DL_PIO_DIR'], "api-data", "frameworks.json"))


class PackagesAPI(APIBase):

    @staticmethod
    def get_result():
        return util.load_json(
            join(config['DL_PIO_DIR'], "api-data", "packages.json"))


class PlatformsAPI(APIBase):

    @staticmethod
    def get_result():
        return util.load_json(
            join(config['DL_PIO_DIR'], "api-data", "platforms.json"))


class PioStatsAPI(APIBase):

    def get_result(self):
        boards = BoardsAPI.get_result()
        result = dict(
            libs=db_session.query(func.count(models.Libs.id)).scalar(),
            libexamples=db_session.query(func.count(models.LibExamples.id))
            .scalar(),
            boards=len(boards),
            mcus=len(set([b['mcu'] for b in boards])),
            frameworks=len(FrameworksAPI.get_result()),
            platforms=len(PlatformsAPI.get_result()))
        return result


class LibSearchAPI(APIBase):

    ITEMS_PER_PAGE = 10

    def __init__(self, query=None, page=1, perpage=None, api_version=1):
        # if not query:
        #     raise APIBadRequest("Please specify '?query' parameter")
        self.search_query = self.parse_search_query(query)
        self.page = page
        self.perpage = perpage or self.ITEMS_PER_PAGE
        self.api_version = api_version

        self.total = self.get_total()

        if self.perpage < 1 or self.perpage > self.ITEMS_PER_PAGE:
            self.perpage = self.ITEMS_PER_PAGE

        if self.page < 1 or ((self.page - 1) * self.perpage) > self.total:
            self.page = 1

    def get_total(self):
        return self._prepare_sql_query(is_count=True).scalar()

    def get_result(self):
        items = []
        query = self._prepare_sql_query().limit(self.perpage).offset(
            (self.page - 1) * self.perpage)

        for data in query.all():
            (lib_id, lib_name, lib_description, lib_keywords, authornames,
             dllifetime, example_nums, updated, frameworkslist,
             platformslist) = data
            items.append(
                dict(
                    id=lib_id,
                    name=lib_name,
                    description=lib_description,
                    keywords=lib_keywords.split(","),
                    authornames=authornames.split(","),
                    frameworks=util.parse_namedtitled_list(
                        frameworkslist, self.api_version == 1),
                    platforms=util.parse_namedtitled_list(
                        platformslist, self.api_version == 1),
                    dllifetime=dllifetime,
                    dlmonth=dllifetime,  # FIXME: Remove later
                    examplenums=example_nums,
                    updated=updated.strftime("%Y-%m-%dT%H:%M:%SZ")))
        return dict(
            total=self.total,
            page=self.page,
            perpage=self.perpage,
            items=items)

    def parse_search_query(self, query):
        quote = "\""
        words = []
        params = {
            "ids": [],
            "authors": [],
            "keywords": [],
            "frameworks": [],
            "platforms": [],
            "names": [],
            "headers": []
        }
        state = {key: None for key in params.keys()}

        if query == "*":
            query = ""

        for token in query.split(" "):
            token_used = False
            token = token.strip()
            if not len(token):
                continue

            # if parameter's value consists from multiple words
            for s in state.keys():
                if state[s] is not None:
                    state[s] += " %s" % token
                    token_used = True
                    break

            # if new parameter
            for s in state.keys():
                if token.startswith("%s:" % s[:-1]):
                    state[s] = token[len(s):]
                    token_used = True
                    break

            if not token_used:
                words.append(token)

            # check if value is completed
            for s in state.keys():
                if state[s] is None:
                    continue
                if not state[s].startswith(quote):
                    params[s].append(state[s])
                    state[s] = None
                elif state[s].startswith(quote) and state[s].endswith(quote):
                    params[s].append(state[s][1:-1])
                    state[s] = None

        # if invalid query
        if all([v is None for v in state.values()]):
            return {"params": params, "words": words}
        else:
            return {
                "params": {},
                "words": [i.strip() for i in query.split(" ") if len(i)]
            }

    def make_fts_words_strict(self, words):
        items = []
        stop = False
        for word in words:
            if "(" in word:
                stop = True

            if word[0] not in "+-<>()~" and word[-1] != "*":
                if "-" in word:
                    word = '"%s"' % word
                if not stop:
                    word = "+" + word

            if ")" in word:
                stop = False

            items.append(word)
        return items

    def escape_fts_query(self, query):
        return re.sub(r"(([\+\-\~\<\>]([^\w\(\"]|$))|(\*{2,}))", r'"\1"',
                      query)

    def _prepare_sql_query(self, is_count=False):
        if is_count:
            query = db_session.query(
                func.count(distinct(models.LibFTS.lib_id)))
        else:
            query = db_session.query(
                models.LibFTS.lib_id, models.LibFTS.name,
                models.LibFTS.description, models.LibFTS.keywords,
                models.LibFTS.authornames, models.LibDLStats.lifetime,
                models.Libs.example_nums, models.Libs.updated,
                models.LibFTS.frameworkslist, models.LibFTS.platformslist)

        query = query.join(models.Libs, models.LibDLStats)
        query = self._apply_filters_to_query(query, is_count)

        if not is_count:
            query = query.order_by(models.LibDLStats.lifetime.desc())
        return query

    def _apply_filters_to_query(self, query, is_count=False):
        # Relationship Way
        _params = self.search_query['params']

        # TMP: fix renamed platforms
        if "espressif" in _params.get("platforms", []):
            for i, item in enumerate(_params.get("platforms")):
                _params['platforms'][i] = "espressif8266"

        if _params.get("ids"):
            query = query.filter(models.LibFTS.lib_id.in_(_params['ids']))
        if _params.get("names"):
            query = query.filter(models.LibFTS.name.in_(_params['names']))
        if _params.get("headers"):
            query = query.join(
                models.LibHeaders,
                and_(
                    models.LibHeaders.name.in_(_params['headers']),
                    models.LibHeaders.lib_id == models.LibFTS.lib_id))

        need_grouping = False
        for key in ("authors", "keywords", "frameworks", "platforms"):
            if not _params.get(key):
                continue
            need_grouping = True
            model_item = getattr(models, key.title())
            model_lib_item = getattr(models, "Libs" + key.title())
            query = query.join(model_item, model_item.name.in_(_params[key]))
            query = query.join(
                model_lib_item,
                and_(model_lib_item.lib_id == models.LibFTS.lib_id,
                     getattr(model_lib_item, key[:-1] + "_id") ==
                     model_item.id))

        if not is_count and need_grouping:
            query = query.group_by(models.LibFTS.lib_id)

        _words = self.make_fts_words_strict(self.search_query['words'])
        if _words:
            fts_query = self.escape_fts_query(" ".join(_words))
            query = query.filter(
                Match([
                    models.LibFTS.name, models.LibFTS.description,
                    models.LibFTS.keywords, models.LibFTS.headerslist,
                    models.LibFTS.authornames, models.LibFTS.frameworkslist,
                    models.LibFTS.platformslist
                ], fts_query))
        return query


class LibExamplesAPI(LibSearchAPI):

    ITEMS_PER_PAGE = 5

    def get_result(self):
        items = []
        query = self._prepare_sql_query().limit(self.perpage).offset(
            (self.page - 1) * self.perpage)

        for data in query.all():
            (example, lib_name, lib_description, lib_keywords, authornames,
             frameworkslist, platformslist) = data
            lib_id = example.lib_id
            items.append(
                dict(
                    id=example.id,
                    name=example.name,
                    url=util.get_libexample_url(lib_id, example.name),
                    lib=dict(
                        id=lib_id,
                        name=lib_name,
                        description=lib_description,
                        keywords=lib_keywords.split(","),
                        authornames=authornames.split(","),
                        frameworks=util.parse_namedtitled_list(frameworkslist),
                        platforms=util.parse_namedtitled_list(platformslist))))
        return dict(
            total=self.total,
            page=self.page,
            perpage=self.perpage,
            items=items)

    def _prepare_sql_query(self, is_count=False):
        _params, _words = self.search_query

        if is_count:
            query = db_session.query(func.count(models.LibExamples.id))
        else:
            query = db_session.query(
                models.LibExamples, models.LibFTS.name,
                models.LibFTS.description, models.LibFTS.keywords,
                models.LibFTS.authornames, models.LibFTS.frameworkslist,
                models.LibFTS.platformslist)

        query = query.join(models.Libs, models.LibFTS)
        query = self._apply_filters_to_query(query, is_count)

        if not self.search_query['words'] and not is_count:
            query = query.order_by(models.LibExamples.id.desc())

        return query


class LibInfoAPI(APIBase):

    def __init__(self, id_):
        self.id_ = id_

    def get_result(self):
        result = dict(
            authors=[],
            dlstats=dict(),
            version=dict(),
            examples=[],
            frameworks={},
            platforms={})

        query = db_session.query(models.Libs, models.LibVersions).join(
            models.LibVersions,
            models.LibVersions.id == models.Libs.latest_version_id).filter(
                models.Libs.id == self.id_)
        try:
            lib, libversion = query.one()
        except NoResultFound:
            raise APINotFound("Unknown library with ID '%s'" % str(self.id_))

        result['id'] = lib.id
        result['confurl'] = lib.conf_url

        for k in ("name", "description"):
            result[k] = getattr(lib.fts, k)
        result['keywords'] = lib.fts.keywords.split(",")

        for k in ("lifetime", "day", "week", "month"):
            result['dlstats'][k] = getattr(lib.dlstats, k)

        # examples
        if lib.example_nums:
            for item in lib.examples:
                result['examples'].append(
                    util.get_libexample_url(lib.id, item.name))

        # latest version
        result['version'] = dict(
            name=libversion.name,
            released=libversion.released.strftime("%Y-%m-%dT%H:%M:%SZ"))

        # previous versions
        result['versions'] = [
            dict(
                name=l.name,
                released=l.released.strftime("%Y-%m-%dT%H:%M:%SZ"))
            for l in lib.versions
        ]

        # authors
        for item in lib.authors:
            _author = {"maintainer": item.maintainer}
            for k in ("name", "email", "url"):
                _author[k] = getattr(item.author, k)
            result['authors'].append(_author)

        # frameworks & platforms
        for what in ("frameworks", "platforms"):
            result[what] = util.parse_namedtitled_list(
                getattr(lib.fts, what + "list"))

        # headers
        result['headers'] = lib.fts.headerslist.split(
            ",") if lib.fts.headerslist else []

        # attributes
        attributes = {}
        for item in lib.attributes:
            attributes[item.attribute.name] = item.value

        result['homepage'] = attributes.get("homepage")
        result['repository'] = attributes.get("repository.url")

        return result


class LibDownloadAPI(APIBase):

    def __init__(self, id_, ip=None, version=None, ci=False):
        self.id_ = id_
        self.ip = ip
        self.version = version.strip() if version else None
        self.ci = ci

    def get_result(self):
        if self.version:
            query = db_session.query(
                models.Libs.id, models.LibVersions.id,
                models.LibVersions.name).outerjoin(
                    models.LibVersions,
                    and_(models.LibVersions.lib_id == models.Libs.id,
                         models.LibVersions.name == self.version)).filter(
                             models.Libs.id == self.id_)
        else:
            query = db_session.query(models.Libs.id, models.LibVersions.id,
                                     models.LibVersions.name).join(
                                         models.LibVersions,
                                         models.LibVersions.id ==
                                         models.Libs.latest_version_id).filter(
                                             models.Libs.id == self.id_)
        try:
            data = query.one()
        except NoResultFound:
            raise APINotFound("Unknown library with ID '%d'" % self.id_)

        lib_id = data[0]
        version_id = data[1]
        version_name = data[2]

        if not version_id:
            raise APINotFound("Unknown version '%s'" % self.version)

        self._logdlinfo(lib_id)

        result = dict(
            url=util.get_libarch_url(lib_id, version_id), version=version_name)
        return result

    def _logdlinfo(self, lib_id):
        if not self.ip or self.ci:
            return

        ip_int = util.ip2int(self.ip)
        try:
            query = db_session.query(models.LibDLLog).filter(
                models.LibDLLog.lib_id == lib_id,
                models.LibDLLog.date > datetime.utcnow() - timedelta(hours=1),
                models.LibDLLog.ip == ip_int)
            item = query.one()
            item.date = datetime.utcnow()
        except NoResultFound:
            db_session.query(models.LibDLStats).filter(
                models.LibDLStats.lib_id == lib_id).update({
                    models.LibDLStats.lifetime: models.LibDLStats.lifetime + 1,
                    models.LibDLStats.day: models.LibDLStats.day + 1,
                    models.LibDLStats.week: models.LibDLStats.week + 1,
                    models.LibDLStats.month: models.LibDLStats.month + 1
                })
            db_session.add(models.LibDLLog(lib_id=lib_id, ip=ip_int))

        db_session.commit()


class LibVersionsAPI(APIBase):

    def __init__(self, id_):
        self.id_ = id_

    def get_result(self):
        result = []
        query = db_session.query(models.LibVersions).filter(
            models.LibVersions.lib_id == self.id_).order_by(
                models.LibVersions.released.asc(), models.LibVersions.id.asc())
        for version in query.all():
            result.append(
                dict(
                    version=version.name,
                    date=version.released.strftime("%Y-%m-%dT%H:%M:%SZ")))
        if not result:
            raise APINotFound("Unknown library with ID '%s'" % self.id_)
        return result


class LibVersionAPI(APIBase):

    def __init__(self, ids):
        self.ids = ids
        assert isinstance(ids, list)

    def get_result(self):
        result = dict()
        query = db_session.query(models.Libs.id, models.LibVersions.name).join(
            models.LibVersions,
            models.LibVersions.id == models.Libs.latest_version_id).filter(
                models.Libs.id.in_(self.ids))
        result = {i[0]: i[1] for i in query.all()}
        for id_ in self.ids:
            if id_ not in result:
                result[id_] = None
        return result


class LibRegisterAPI(APIBase):

    def __init__(self, conf_url):
        self.conf_url = conf_url.strip() if conf_url else None
        if not self.conf_url:
            raise APIBadRequest("Please specify the library configuration URL")

    def get_result(self):
        result = dict(successed=False, message=None)

        try:
            manifest_name = basename(self.conf_url)
            if manifest_name.endswith(".properties"):
                cls = crawler.ArduinoLibSyncer
            elif manifest_name == "module.json":
                cls = crawler.YottaLibSyncer
            else:
                cls = crawler.PlatformIOLibSyncer

            config = cls.load_config(self.conf_url)
            assert cls.validate_config(config)

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
            result['message'] = (
                "Invalid URL or broken manifest. "
                "Please validate it with http://jsonlint.com")
        return result


class LibStatsAPI(APIBase):

    def get_result(self):
        result = dict(
            updated=self._get_last_updated(),
            added=self._get_last_added(),
            lastkeywords=self._get_last_keywords(),
            topkeywords=self._get_top_keywords(),
            dlday=self._get_most_downloaded(models.LibDLStats.day),
            dlweek=self._get_most_downloaded(models.LibDLStats.week),
            dlmonth=self._get_most_downloaded(models.LibDLStats.month))
        return result

    def _get_last_updated(self, limit=5):
        items = []
        query = db_session.query(
            models.Libs.id, models.Libs.updated,
            models.LibFTS.name).join(models.LibFTS).order_by(
                models.Libs.updated.desc()).limit(limit)
        for item in query.all():
            items.append(
                dict(
                    id=item[0],
                    name=item[2],
                    date=item[1].strftime("%Y-%m-%dT%H:%M:%SZ")))
        return items

    def _get_last_added(self, limit=5):
        items = []
        query = db_session.query(
            models.Libs.id, models.Libs.added, models.LibFTS.name).join(
                models.LibFTS).order_by(models.Libs.added.desc()).limit(limit)
        for item in query.all():
            items.append(
                dict(
                    id=item[0],
                    name=item[2],
                    date=item[1].strftime("%Y-%m-%dT%H:%M:%SZ")))
        return items

    def _get_last_keywords(self, limit=5):
        items = []
        query = db_session.query(models.Keywords.name).order_by(
            models.Keywords.id.desc()).limit(limit)
        for item in query.all():
            items.append(item[0])
        return items

    def _get_top_keywords(self, limit=50):
        items = []
        query = db_session.query(
            models.Keywords.name, func.count(models.Keywords.id).label(
                "total")).join(models.LibsKeywords).group_by(
                    models.Keywords.id).order_by(desc("total")).limit(limit)
        for item in query.all():
            items.append(item[0])
        return items

    def _get_most_downloaded(self, period, limit=10):
        period_prev = getattr(models.LibDLStats, "%s_prev" % period.key)
        items = []
        query = db_session.query(
                period,
                label("diff", period - period_prev),
                models.LibFTS.lib_id, models.LibFTS.name)\
            .join(models.LibFTS,
                  models.LibDLStats.lib_id == models.LibFTS.lib_id)\
            .filter(period >= period_prev)\
            .order_by(desc("diff"))\
            .limit(limit)
        for item in query.all():
            items.append(
                dict(
                    id=item[2], name=item[3], total=item[0], diff=item[1]))
        return items
