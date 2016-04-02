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

import json
import logging
import re
from datetime import datetime
from os.path import basename, join
from itertools import chain

import requests
from platformio.platforms.base import PlatformFactory, get_packages
from platformio.util import get_boards, get_frameworks
from sqlalchemy import and_, distinct, func
from sqlalchemy.orm.exc import NoResultFound

from platformio_api import __version__, config, models, util
from platformio_api.database import Match, db_session
from platformio_api.exception import APIBadRequest, APINotFound, InvalidLibConf
from platformio_api.solr import solr_libs
from platformio_api.util import parse_namedtitled_list


logger = logging.getLogger(__name__)


class APIBase(object):

    def get_result(self):
        raise NotImplementedError()


class BoardsAPI(APIBase):

    def get_result(self):
        items = []
        for type_, data in get_boards().iteritems():
            items.append({
                "type": type_,
                "name": data['name'],
                "mcu": data.get("build", {}).get("mcu", "").upper(),
                "fcpu": int(data.get("build", {}).get("f_cpu", "")[:-1]),
                "ram": data.get("upload", {}).get("maximum_ram_size", 0),
                "rom": data.get("upload", {}).get("maximum_size", 0),
                "frameworks": data['frameworks'],
                "platform": data['platform'],
                "vendor": data['vendor'],
                "url": data['url']
            })
        return items


class FrameworksAPI(APIBase):

    def get_result(self):
        items = []
        for type_, data in get_frameworks().iteritems():
            items.append({
                'type': type_,
                'name': data['name'],
                'description': data['description'],
                'url': data['url']
            })
        return items


class PackagesAPI(APIBase):

    def get_result(self):
        result = {}
        for name, contents in get_packages().iteritems():
            result[name] = []
            for c in contents:
                result[name].append({
                    'name': c[0],
                    'url': c[1]
                })
        return result


class PackagesManifestAPI(APIBase):

    def get_result(self):
        result = None
        r = None

        try:
            headers = {"User-Agent": "PlatformIO/%s %s" % (
                __version__, requests.utils.default_user_agent())}
            r = requests.get(
                "https://dl.bintray.com/platformio/dl-packages/"
                "manifest_old.json",
                headers=headers, timeout=3)
            result = r.json()
            r.raise_for_status()
        except:
            with open(join(util.get_packages_dir(), "manifest.json")) as f:
                result = json.load(f)
            for name, versions in result.iteritems():
                for item in versions:
                    item['url'] = util.get_package_url(basename(item['url']))
        finally:
            if r:
                r.close()
        return result


class PlatformsAPI(APIBase):

    def get_result(self):
        result = []
        for type_ in PlatformFactory.get_platforms().keys():
            p = PlatformFactory.newPlatform(type_)
            result.append({
                'type': type_,
                'name': p.get_name(),
                'description': p.get_description(),
                'url': p.get_vendor_url(),
                'packages': p.get_packages().keys(),
                'forDesktop': any([
                    type_.startswith(n)
                    for n in ("native", "linux", "windows")])
            })
        return sorted(result, key=lambda item: item['type'])


class LibSearchAPI(APIBase):

    ITEMS_PER_PAGE = 10

    def __init__(self, query=None, page=1, perpage=None):
        # if not query:
        #     raise APIBadRequest("Please specify '?query' parameter")
        self.search_query = self.parse_search_query(query)
        self.page = page
        self.perpage = perpage or self.ITEMS_PER_PAGE

        self.total = self.get_total()

        if self.perpage < 1 or self.perpage > self.ITEMS_PER_PAGE:
            self.perpage = self.ITEMS_PER_PAGE

        if self.page < 1 or ((self.page - 1) * self.perpage) > self.total:
            self.page = 1

    def get_total(self):
        return self._prepare_sql_query(count=True).scalar()

    def get_result(self):
        items = []
        query = self._prepare_sql_query().limit(self.perpage).offset(
            (self.page - 1) * self.perpage)

        for data in query.all():
            (lib_id, lib_name, lib_description, lib_keywords,
             authornames, dlmonth, example_nums, updated,
             frameworkslist, platformslist) = data
            items.append(dict(
                id=lib_id,
                name=lib_name,
                description=lib_description,
                keywords=lib_keywords.split(","),
                authornames=authornames.split(","),
                frameworks=parse_namedtitled_list(frameworkslist),
                platforms=parse_namedtitled_list(platformslist),
                dlmonth=dlmonth,
                examplenums=example_nums,
                updated=updated.strftime("%Y-%m-%dT%H:%M:%SZ")
            ))
        return dict(
            total=self.total,
            page=self.page,
            perpage=self.perpage,
            items=items
        )

    def parse_search_query(self, query):
        quote = "\""
        words = []
        params = {
            "authors": [],
            "keywords": [],
            "frameworks": [],
            "platforms": [],
            "names": [],
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
            return {"params": {},
                    "words": [i.strip() for i in query.split(" ") if len(i)]}

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

    def _prepare_sql_query(self, count=False):
        if count:
            query = db_session.query(
                func.count(distinct(models.LibFTS.lib_id))
            )
        else:
            query = db_session.query(
                models.LibFTS.lib_id, models.LibFTS.name,
                models.LibFTS.description, models.LibFTS.keywords,
                models.LibFTS.authornames, models.LibDLStats.month,
                models.Libs.example_nums, models.Libs.updated,
                models.LibFTS.frameworkslist, models.LibFTS.platformslist
            )

        query = query.join(models.Libs, models.LibDLStats)

        # Relationship Way
        _params = self.search_query['params']
        if _params.get("authors"):
            query = query.join(models.LibsAuthors).join(
                models.Authors,
                and_(models.Authors.name.in_(_params['authors']),
                     models.Authors.id == models.LibsAuthors.author_id)
            )

        if _params.get("keywords"):
            query = query.join(models.LibsKeywords).join(
                models.Keywords,
                and_(models.Keywords.name.in_(_params['keywords']),
                     models.Keywords.id == models.LibsKeywords.keyword_id)
            )

        if not count and (_params.get("authors") or _params.get("keywords")):
            query = query.group_by(models.LibFTS.lib_id)

        # Cached FTS Way
        _words = self.make_fts_words_strict(self.search_query['words'])
        for key, items in (_params or {}).iteritems():
            if not items or key in ("authors", "keywords"):
                continue
            _words.append('+("%s")' % '" "'.join(items))

        if _words:
            fts_query = self.escape_fts_query(" ".join(_words))
            query = query.filter(
                Match([models.LibFTS.name, models.LibFTS.description,
                       models.LibFTS.keywords, models.LibFTS.examplefiles,
                       models.LibFTS.authornames, models.LibFTS.frameworkslist,
                       models.LibFTS.platformslist],
                      fts_query))
        elif not count:
            query = query.order_by(models.LibDLStats.month.desc(),
                                   models.LibFTS.name)

        return query


class LibSearchSolrAPI(LibSearchAPI):

    ITEMS_PER_PAGE = 10

    def __init__(self, query=None, strict=False, page=1, perpage=None):
        self.search_query = self.parse_search_query(query)
        self.solr_query = self.prepare_solr_query(self.search_query, strict)
        self.page = page
        self.perpage = perpage or self.ITEMS_PER_PAGE

        if self.perpage < 1 or self.perpage > self.ITEMS_PER_PAGE:
            self.perpage = self.ITEMS_PER_PAGE

        if self.page < 1:
            self.page = 1

    def get_result(self):
        result = solr_libs.search(
            self.solr_query,
            params={
                'start': (self.page - 1) * self.perpage,
                'rows': self.perpage,
            },
        ).json()
        _docs = result.get('response', {}).get('docs', [])

        ids = []
        for doc in _docs:
            try:
                ids.append(int(doc['id']))
            except ValueError as e:
                logger.exception(e)

        query = db_session.query(models.Libs)\
            .join(models.Libs.fts)\
            .join(models.Libs.dlstats)\
            .filter(models.Libs.id.in_(ids))
        db_entries = {lib.id: lib for lib in query.all()}

        items = []
        # Iterating over the original Solr response to preserve the order
        for doc in _docs:
            try:
                lib_id = int(doc['id'])
            except ValueError:
                # This error should have been logged above, when list of IDs
                # is created. No reason to duplicate the exception.
                pass

            lib = db_entries.get(lib_id)
            if not lib:
                logger.warn(
                    "Lib #{} was in Solr response, but is not found in DB. "
                    "Consider running `platformio-api synchronize_libs_on_solr"
                    "` to remove any outdated libraries from index."
                    .format(lib_id)
                )
                continue
            items.append(dict(
                id=lib.id,
                name=lib.fts.name,
                description=lib.fts.description,
                keywords=lib.fts.keywords.split(","),
                authornames=lib.fts.authornames.split(","),
                frameworks=parse_namedtitled_list(lib.fts.frameworkslist),
                platforms=parse_namedtitled_list(lib.fts.platformslist),
                dlmonth=lib.dlstats.month,
                examplenums=lib.example_nums,
                updated=lib.updated.strftime("%Y-%m-%dT%H:%M:%SZ")
            ))

        return dict(
            total=result.get('response', {}).get('numFound', 0),
            page=self.page,
            perpage=self.perpage,
            items=items,
        )

    @staticmethod
    def prepare_solr_query(search_query, strict):
        """Prepares a user query for Solr.

        Currently this method does the following:
            - adds "~" everywhere in order to turn a fuzzy search on (when
              `strict` is False);
            - renames fields to match its actual names.

        :param strict: flag indicating whether fuzzy search should be disabled
            or not.
        :type strict: bool
        :return: a processed query
        :rtype: unicode
        """
        words = search_query.get('words', [])
        if "*" in words:
            words.remove("*")
        params = search_query.get('params', {})
        params = {k: v for k, v in params.items() if v}  # skip empty filters

        query_param_to_solr_field_map = {
            "authors": "authornames",
            "keywords": "keywords",
            "frameworks": "frameworkslist",
            "platforms": "platformslist",
            "names": "name",
        }

        threshold = config['SOLR_FUZZY_MIN_WORD_LENGTH']
        if not strict:
            words = [word + "~" if len(word) > threshold else word
                     for word in words]
        if not words and not params:
            words = ["*:*"]

        filters = []
        for param, values in params.items():
            if param in query_param_to_solr_field_map:
                param = query_param_to_solr_field_map[param]
            for value in values:
                if " " in value:
                    value = '"%s"' % value
                filters.append("%s:%s" % (param, value))

        return " ".join(chain(words, filters))


class LibExamplesAPI(LibSearchAPI):

    ITEMS_PER_PAGE = 5

    def get_result(self):
        items = []
        query = self._prepare_sql_query().limit(self.perpage).offset(
            (self.page - 1) * self.perpage)

        for data in query.all():
            (example, lib_name, lib_description, lib_keywords,
             authornames, frameworkslist, platformslist) = data
            lib_id = example.lib_id
            items.append(dict(
                id=example.id,
                name=example.name,
                url=util.get_libexample_url(lib_id, example.name),
                lib=dict(
                    id=lib_id,
                    name=lib_name,
                    description=lib_description,
                    keywords=lib_keywords.split(","),
                    authornames=authornames.split(","),
                    frameworks=parse_namedtitled_list(frameworkslist),
                    platforms=parse_namedtitled_list(platformslist)
                )
            ))
        return dict(
            total=self.total,
            page=self.page,
            perpage=self.perpage,
            items=items
        )

    def _prepare_sql_query(self, count=False):
        _params, _words = self.search_query

        if count:
            query = db_session.query(func.count(models.LibExamples.id))
        else:
            query = db_session.query(
                models.LibExamples, models.LibFTS.name,
                models.LibFTS.description, models.LibFTS.keywords,
                models.LibFTS.authornames, models.LibFTS.frameworkslist,
                models.LibFTS.platformslist
            )

        query = query.join(models.Libs, models.LibFTS)

        # Relationship Way
        _params = self.search_query['params']
        if _params.get("authors"):
            query = query.join(models.LibsAuthors).join(
                models.Authors,
                and_(models.Authors.name.in_(_params['authors']),
                     models.Authors.id == models.LibsAuthors.author_id)
            )

        if _params.get("keywords"):
            query = query.join(models.LibsKeywords).join(
                models.Keywords,
                and_(models.Keywords.name.in_(_params['keywords']),
                     models.Keywords.id == models.LibsKeywords.keyword_id)
            )

        # Cached FTS Way
        _words = self.make_fts_words_strict(self.search_query['words'])
        for key, items in (_params or {}).iteritems():
            if not items or key in ("authors", "keywords"):
                continue
            _words.append('+("%s")' % '" "'.join(items))

        if _words:
            fts_query = self.escape_fts_query(" ".join(_words))
            query = query.filter(
                Match([models.LibFTS.name, models.LibFTS.description,
                       models.LibFTS.keywords, models.LibFTS.examplefiles,
                       models.LibFTS.authornames, models.LibFTS.frameworkslist,
                       models.LibFTS.platformslist],
                      fts_query))
        elif not count:
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
            platforms={}
        )

        query = db_session.query(
            models.Libs, models.LibVersions
        ).join(
            models.LibVersions,
            models.LibVersions.id == models.Libs.latest_version_id
        ).filter(models.Libs.id == self.id_)
        try:
            lib, libversion = query.one()
        except NoResultFound:
            raise APINotFound("Unknown library with ID '%s'" % str(self.id_))

        result['id'] = lib.id
        result['confurl'] = lib.conf_url

        for k in ("name", "description"):
            result[k] = getattr(lib.fts, k)
        result['keywords'] = lib.fts.keywords.split(",")

        for k in ("day", "week", "month"):
            result['dlstats'][k] = getattr(lib.dlstats, k)

        # examples
        for name in lib.fts.examplefiles.split(","):
            if name:
                result['examples'].append(
                    util.get_libexample_url(lib.id, name))

        # latest version
        result['version'] = dict(
            name=libversion.name,
            released=libversion.released.strftime("%Y-%m-%dT%H:%M:%SZ")
        )

        # authors
        for item in lib.authors:
            _author = {"maintainer": item.maintainer}
            for k in ("name", "email", "url"):
                _author[k] = getattr(item.author, k)
            result['authors'].append(_author)

        # frameworks & platforms
        for what in ("frameworks", "platforms"):
            result[what] = []
            _list = getattr(lib.fts, what + "list").split(",")
            for l in _list:
                if ":" in l:
                    result[what].append(l.split(":")[0])

        # attributes
        attributes = {}
        for item in lib.attributes:
            attributes[item.attribute.name] = item.value

        # home url
        if set(["url", "repository.url"]) & set(attributes.keys()):
            result['url'] = attributes.get(
                "url", attributes.get("repository.url"))

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
                models.Libs.id,
                models.LibVersions.id, models.LibVersions.name
            ).outerjoin(
                models.LibVersions,
                and_(models.LibVersions.lib_id == models.Libs.id,
                     models.LibVersions.name == self.version)
            ).filter(models.Libs.id == self.id_)
        else:
            query = db_session.query(
                models.Libs.id, models.LibVersions.id, models.LibVersions.name
            ).join(
                models.LibVersions,
                models.LibVersions.id == models.Libs.latest_version_id
            ).filter(models.Libs.id == self.id_)
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
            url=util.get_libarch_url(lib_id, version_id),
            version=version_name
        )
        return result

    def _logdlinfo(self, lib_id):
        if not self.ip or self.ci:
            return

        ip_int = util.ip2int(self.ip)
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

    def __init__(self, ids):
        self.ids = ids
        assert isinstance(ids, list)

    def get_result(self):
        result = dict()
        query = db_session.query(
            models.Libs.id, models.LibVersions.name
        ).join(
            models.LibVersions,
            models.LibVersions.id == models.Libs.latest_version_id
        ).filter(models.Libs.id.in_(self.ids))
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
        result = dict(
            successed=False,
            message=None
        )

        config = dict()
        try:
            r = requests.get(self.conf_url)
            try:
                config = r.json()
            except ValueError:
                raise InvalidLibConf(self.conf_url)

            # validate fields
            config = util.validate_libconf(config)

            # check for name duplicates
            # query = db_session.query(func.count(1)).filter(
            #     models.LibFTS.name == config['name'])
            # if query.scalar():
            #     raise InvalidLibConf("The library with name '%s' is already "
            #                          "registered" % config['name'])

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


class LibStatsAPI(APIBase):

    def get_result(self):
        result = dict(
            updated=self._get_last_updated(),
            added=self._get_last_added(),
            lastkeywords=self._get_last_keywords(),
            topkeywords=self._get_top_keywords(),
            dlday=self._get_most_downloaded(models.LibDLStats.day),
            dlweek=self._get_most_downloaded(models.LibDLStats.week),
            dlmonth=self._get_most_downloaded(models.LibDLStats.month)
        )
        return result

    def _get_last_updated(self, limit=5):
        items = []
        query = db_session.query(
            models.Libs.id, models.Libs.updated, models.LibFTS.name
        ).join(models.LibFTS).order_by(models.Libs.updated.desc()).limit(limit)
        for item in query.all():
            items.append(dict(
                id=item[0],
                name=item[2],
                date=item[1].strftime("%Y-%m-%dT%H:%M:%SZ")
            ))
        return items

    def _get_last_added(self, limit=5):
        items = []
        query = db_session.query(
            models.Libs.id, models.Libs.added, models.LibFTS.name
        ).join(models.LibFTS).order_by(models.Libs.added.desc()).limit(limit)
        for item in query.all():
            items.append(dict(
                id=item[0],
                name=item[2],
                date=item[1].strftime("%Y-%m-%dT%H:%M:%SZ")
            ))
        return items

    def _get_last_keywords(self, limit=5):
        items = []
        query = db_session.query(
            models.Keywords.name
        ).order_by(models.Keywords.id.desc()).limit(limit)
        for item in query.all():
            items.append(item[0])
        return items

    def _get_top_keywords(self, limit=50):
        items = []
        query = db_session.query(
            models.Keywords.name, func.count(models.Keywords.id).label("total")
        ).join(models.LibsKeywords).group_by(
            models.Keywords.id
        ).order_by("total DESC").limit(limit)
        for item in query.all():
            items.append(item[0])
        return items

    def _get_most_downloaded(self, period, limit=5):
        items = []
        query = db_session.query(
            period, models.LibFTS.lib_id, models.LibFTS.name
        ).join(
            models.LibFTS, models.LibDLStats.lib_id == models.LibFTS.lib_id
        ).order_by(period.desc()).limit(limit)
        for item in query.all():
            items.append(dict(
                id=item[1],
                name=item[2],
                total=item[0]
            ))
        return items
