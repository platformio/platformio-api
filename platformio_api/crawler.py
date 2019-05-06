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

import json
import logging
import re
import sys
import textwrap
from datetime import datetime
from glob import glob
from hashlib import sha1
from os import listdir, makedirs, remove, walk
from os.path import basename, dirname, isdir, isfile, join
from shutil import copy, copytree, rmtree
from tempfile import mkdtemp, mkstemp
from urlparse import urlparse

import requests
from sqlalchemy import and_
from sqlalchemy.orm.exc import NoResultFound

from platformio_api import models, util
from platformio_api.database import db_session
from platformio_api.exception import (InvalidLibConf, InvalidLibVersion,
                                      PlatformioAPIException)
from platformio_api.vcsclient import VCSClientFactory

logger = logging.getLogger(__name__)


class LibSyncerFactory(object):

    @staticmethod
    def new(lib):
        assert isinstance(lib, models.Libs)
        clsname = "PlatformIOLibSyncer"
        manifest_name = basename(lib.conf_url)
        if manifest_name.endswith(".properties"):
            clsname = "ArduinoLibSyncer"
        elif manifest_name == "module.json":
            clsname = "YottaLibSyncer"
        obj = getattr(sys.modules[__name__], clsname)(lib)
        assert isinstance(obj, LibSyncerBase)
        return obj


class LibSyncerBase(object):

    def __init__(self, lib):
        assert isinstance(lib, models.Libs)
        self.lib = lib

        try:
            self.config = self.load_config(lib.conf_url)
            self.config = self.validate_config(self.config)
            self.config = self.clean_dict(self.config)
            logger.debug("LibConf: %s" % self.config)
        except Exception as e:
            logger.error(e)
            raise InvalidLibConf(lib.conf_url)

        self.vcsclient = None
        if "repository" in self.config:
            _type = self.config['repository'].get("type", "").lower()
            url = self.config['repository'].get("url", "")
            if _type and url:
                self.vcsclient = VCSClientFactory.newClient(
                    _type,
                    url,
                    branch=self.config['repository'].get("branch", None),
                    tag=self.config.get("version", None))

    @staticmethod
    def clean_dict(data):
        for (key, _) in (data.iteritems()
                         if isinstance(data, dict) else enumerate(data)):
            if isinstance(data[key], dict) or isinstance(data[key], list):
                data[key] = LibSyncerBase.clean_dict(data[key])
            elif isinstance(data[key], basestring):
                data[key] = data[key].strip()
                if not isinstance(data[key], unicode):
                    data[key] = data[key].decode("utf-8")
        return data

    @staticmethod
    def get_manifest_name():
        raise NotImplementedError

    @staticmethod
    def load_config(manifest_url):
        raise NotImplementedError

    @staticmethod
    def validate_config(config):
        fields = set(config.keys())
        if not fields.issuperset(set(["name", "keywords", "description"])):
            raise InvalidLibConf(
                "The 'name, keywords and description' fields are required")

        if (config.get("dependencies")
                and not (isinstance(config['dependencies'], list)
                         or isinstance(config['dependencies'], dict))):
            raise InvalidLibConf("The 'dependencies' field is invalid")

        # if github- or mbed-based project
        if "repository" in config:
            type = config['repository'].get("type", None)
            url = config['repository'].get("url", "")
            if ((type == "git" and "github.com" in url)
                    or (type == "hg" and util.is_mbed_repository(url))
                    or (type in ["hg", "git"] and "bitbucket.org" in url)):
                return config

        # if CVS-based
        authors = config.get("authors", None)
        if authors and not isinstance(authors, list):
            authors = [authors]

        if not authors:
            raise InvalidLibConf("The 'authors' field is required")
        elif not all(["name" in item for item in authors]):
            raise InvalidLibConf("An each author should have 'name' property")
        elif ("repository" in config
              and config['repository'].get("type", None) in ("git", "svn")):
            return config

        # if self-hosted
        if "version" not in config:
            raise InvalidLibConf("The 'version' field is required")
        elif "downloadUrl" not in config:
            raise InvalidLibConf("The 'downloadUrl' field is required")

        return config

    def get_version(self):
        version = dict(
            name=str(self.config.get("version", "")),
            released=datetime.utcnow())

        if self.vcsclient:
            path = None
            inclist = self.config.get("export", {}).get("include", None)
            if isinstance(inclist, basestring):
                path = inclist
            commit = self.vcsclient.get_last_commit(path=path)
            if not version['name']:
                version['name'] = commit['sha'][:10]
            version['released'] = commit['date']

        if (version['name']
                and re.match(r"^[a-z0-9\.\-\+]+$", version['name'], re.I)):
            return version
        else:
            raise InvalidLibVersion(version['name'])

    def calc_config_sha1(self):
        return sha1(json.dumps(self.config, sort_keys=True)).hexdigest()

    def sync(self):
        if "version" in self.config and \
           self.lib.conf_sha1 == self.calc_config_sha1():
            return True

        # fetch version info
        version = self.get_version()
        self.config['version'] = version['name']

        config_sha1 = self.calc_config_sha1()
        if self.lib.conf_sha1 == config_sha1:
            return True

        logger.info("Library is out-of-date: %s", self.lib.conf_url)
        self.lib.conf_sha1 = config_sha1
        self.lib.updated = datetime.utcnow()

        self.lib.latest_version_id = self.sync_version(version)
        self.lib.attributes = self.sync_attributes()

        # FTS defaults
        if self.lib.fts is None:
            self.lib.fts = models.LibFTS(name=self.config['name'])
        self.lib.fts.name = self.config['name']
        if len(self.config['description']) > 255:
            self.lib.fts.description = textwrap.wrap(
                self.config['description'], 252)[0] + "..."
        else:
            self.lib.fts.description = self.config['description']

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

        src_dir = mkdtemp()
        try:
            assert self.export(src_dir)
            assert self.sync_headers(src_dir)
            assert self.sync_examples(src_dir)
            assert self.archive(src_dir)
        finally:
            if isdir(src_dir):
                rmtree(src_dir)

        return True

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
        itemtpl = dict(email=None, url=None, maintainer=False)
        if confauthors:
            if not isinstance(confauthors, list):
                confauthors = [confauthors]
            for item in confauthors:
                tmp = itemtpl.copy()
                tmp.update(item)
                authors.append(tmp)
        elif self.vcsclient and self.vcsclient.get_type() == "github":
            tmp = itemtpl.copy()
            tmp.update(self.vcsclient.get_owner())
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
                    name=item['name'], email=item['email'], url=item['url'])
                self.lib.authors.append(_la)

        # save in string format for FTS
        self.lib.fts.authornames = ",".join(authornames)
        return authors

    def sync_keywords(self, keywords):
        keywords = self._cleanup_keywords(keywords)

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

    def _cleanup_keywords(self, keywords):
        assert any([isinstance(keywords, t) for t in (list, basestring)])
        if not isinstance(keywords, list):
            keywords = [k for k in keywords.split(",")]
        keywords = list(set([k.lower().strip() for k in keywords]))

        result = []
        for item in keywords:
            if not item or item in result:
                continue
            if len(item) >= 30:
                for _item in item.split():
                    _item = _item.strip()[:30]
                    if _item not in result:
                        result.append(_item)
            else:
                result.append(item)
        return result

    def sync_frameworks_or_platforms(self, what, items):

        def _process_items(items_):
            if not isinstance(items_, list):
                items_ = [i for i in items_.split(",")]
            return list(set([i.lower().strip() for i in items_]))

        assert what in ("frameworks", "platforms")
        assert any([isinstance(items, t) for t in (list, basestring)])
        items = _process_items(items)

        dbitems = []
        if items:
            _model = getattr(models, what.title())
            dbitems = db_session.query(_model).order_by(_model.name.asc())
            if items[0] == "*":
                if what == "platforms" and self.config.get("frameworks"):
                    dbitems = dbitems.join(models.PlatformsFrameworks).join(
                        models.Frameworks,
                        and_(models.Frameworks.id ==
                             models.PlatformsFrameworks.framework_id,
                             models.Frameworks.name.in_(
                                 _process_items(self.config['frameworks']))))
                dbitems = dbitems.all()
                items = [getattr(i, "name") for i in dbitems]
            else:
                dbitems = dbitems.filter(_model.name.in_(items)).all()

        # check for invalid items
        # assert len(items) == len(dbitems)

        # update items in DB
        setattr(self.lib, what, dbitems)
        # save in string format for FTS
        setattr(self.lib.fts, what + "list", ",".join(
            ["%s:%s" % (item.name, item.title) for item in dbitems]))

        return items

    def sync_attributes(self):
        confattrs = {}
        self._fetch_conf_attrs(confattrs, self.config)

        attributes = []
        for attribute in db_session.query(models.Attributes).all():
            if attribute.name not in confattrs:
                continue
            _la = models.LibsAttributes(value=confattrs[attribute.name])
            _la.attribute = attribute
            attributes.append(_la)

        return attributes

    def _fetch_conf_attrs(self, confattrs, node, path=None):
        if path is None:
            path = []

        for k, v in node.iteritems():
            if isinstance(v, dict):
                self._fetch_conf_attrs(confattrs, v, path + [k])
                continue
            elif isinstance(v, list):
                v = json.dumps(v)
            if v:
                confattrs[".".join(path + [k])] = v

    def export(self, src_dir):
        if "downloadUrl" in self.config:
            try:
                tmparh_path = mkstemp(basename(self.config['downloadUrl']))[1]
                util.download_file(self.config['downloadUrl'], tmparh_path)
                util.extract_archive(tmparh_path, src_dir)
            finally:
                remove(tmparh_path)
        elif self.vcsclient:
            self.vcsclient.clone(src_dir)
        else:
            raise PlatformioAPIException()

        return all(
            [self._export_exclude(src_dir),
             self._export_include(src_dir)])

    def _export_exclude(self, src_dir):
        exclist = self.config.get("export", {}).get("exclude", [])
        if isinstance(exclist, basestring):
            exclist = [exclist]
        for pathname in exclist:
            for item in glob(join(src_dir, pathname)):
                if isfile(item):
                    remove(item)
                else:
                    rmtree(item)
        return True

    def _export_include(self, src_dir):
        inclist = self.config.get("export", {}).get("include", None)
        if not inclist:
            return True

        tmp_dir = mkdtemp()
        try:
            if isinstance(inclist, list):
                for pathname in inclist:
                    for item in glob(join(src_dir, pathname)):
                        dstpath = join(tmp_dir, item[len(src_dir) + 1:])
                        if isfile(item):
                            if not isdir(dirname(dstpath)):
                                makedirs(dirname(dstpath))
                            copy(item, dstpath)
                        else:
                            copytree(item, dstpath, symlinks=True)
            # if "include" is a string then use it like a "mount" point
            elif isinstance(inclist, basestring):
                for item in glob(join(src_dir, inclist)):
                    if isfile(item):
                        copy(item, join(tmp_dir, basename(item)))
                    else:
                        for item2 in listdir(item):
                            itempath = join(item, item2)
                            dstpath = join(tmp_dir, item2)
                            if isfile(itempath):
                                copy(itempath, dstpath)
                            else:
                                copytree(itempath, dstpath, symlinks=True)

            # replace src_dir with filtered content
            rmtree(src_dir)
            copytree(tmp_dir, src_dir, symlinks=True)
        finally:
            if isdir(tmp_dir):
                rmtree(tmp_dir)
        return True

    def archive(self, src_dir):
        # put modified .library.json
        with open(join(src_dir, ".library.json"), "w") as f:
            json.dump(self.config, f, indent=4)
        util.download_file(self.lib.conf_url,
                           join(src_dir, self.get_manifest_name()))

        # pack lib's files
        archive_path = util.get_libarch_path(self.lib.id,
                                             self.lib.latest_version_id)
        if not isdir(dirname(archive_path)):
            makedirs(dirname(archive_path))
        util.create_archive(archive_path, src_dir)
        return isfile(archive_path)

    def sync_headers(self, src_dir):
        usednames = {}
        for _, _, files in walk(src_dir):
            for name in files:
                _name = name.lower()
                if _name.endswith((".h", ".hpp")) and _name not in usednames:
                    usednames[_name] = name

        newitems = []
        for name in sorted(usednames.values(), key=lambda s: s.lower()):
            found = False
            for header in self.lib.headers:
                if header.name == name:
                    newitems.append(header)
                    found = True
                    break
            if not found:
                newitems.append(models.LibHeaders(name=name))

        self.lib.headers = newitems
        self.lib.fts.headerslist = ",".join(usednames.values())
        return True

    def sync_examples(self, src_dir):
        exmdir = util.get_libexample_dir(self.lib.id)
        if isdir(exmdir):
            rmtree(exmdir)

        usednames = {}
        tmp_dir = mkdtemp()
        try:
            files = self.find_example_files(src_dir, tmp_dir)
            if files:
                makedirs(exmdir)
            for f in files:
                name = basename(f)
                _name = name.lower()
                if _name in usednames:
                    continue
                copy(f, join(exmdir, name))
                usednames[_name] = name
        finally:
            rmtree(tmp_dir)

        newitems = []
        for name in sorted(usednames.values(), key=lambda s: s.lower()):
            found = False
            for example in self.lib.examples:
                if example.name == name:
                    newitems.append(example)
                    found = True
                    break
            if not found:
                newitems.append(models.LibExamples(name=name))

        self.lib.examples = newitems
        self.lib.example_nums = len(usednames)
        return True

    def find_example_files(self, src_dir, tmp_dir):
        exmglobs = self.config.get("examples", None)
        exmfiles = []
        if exmglobs is None:
            for ext in ("*.ino", "*.pde", "*.c", "*.cpp"):
                _exmdir = join(src_dir, "[Ee]xamples")
                exmfiles += glob(join(_exmdir, ext))
                exmfiles += glob(join(_exmdir, "*", ext))
                exmfiles += glob(join(_exmdir, "*", "*", ext))
        else:
            if not isinstance(exmglobs, list):
                exmglobs = [exmglobs]
            repo_url = self.config.get('repository', {}).get("url", "")
            if util.is_mbed_repository(repo_url):
                exmfiles = self._fetch_mbed_example_files(exmglobs, tmp_dir)
            else:
                for fmask in exmglobs:
                    exmfiles += glob(join(src_dir, fmask))
        return [f for f in exmfiles if isfile(f)]

    def _fetch_mbed_example_files(self, urls, tmp_dir):
        actual_examples_dir = mkdtemp(dir=tmp_dir)
        files = []
        for url in urls:
            client = VCSClientFactory.newClient("hg", url)
            repo_name = client.url.split('/')[-2]
            repo_dir = mkdtemp(dir=tmp_dir)
            try:
                client.clone(repo_dir)
            except:
                logger.warn("Invalid mbed example %s" % url)
                continue
            for old_file_path in util.get_c_sources(repo_dir):
                if isdir(old_file_path):
                    continue
                new_file_path = join(actual_examples_dir,
                                     "%s_%s" % (repo_name,
                                                basename(old_file_path)))
                copy(old_file_path, new_file_path)
                files.append(new_file_path)
        return files


class PlatformIOLibSyncer(LibSyncerBase):

    @staticmethod
    def get_manifest_name():
        return "library.json"

    @staticmethod
    def load_config(manifest_url):
        config_text = requests.get(manifest_url).text.encode("utf-8")
        manifest = json.loads(config_text)
        if "url" in manifest:
            manifest['homepage'] = manifest['url']
            del manifest['url']

        for key in ("include", "exclude"):
            if key not in manifest:
                continue
            if "export" not in manifest:
                manifest['export'] = {}
            manifest['export'][key] = manifest[key]
            del manifest[key]

        # rename "espressif" platform
        platforms = manifest.get("platforms")
        if platforms and "espressif" in platforms:
            if not isinstance(platforms, list):
                platforms = [i for i in platforms.split(",")]
            platforms = list(set([i.lower().strip() for i in platforms]))
            if "espressif" in platforms:
                platforms[platforms.index("espressif")] = "espressif8266"
                manifest['platforms'] = platforms
        return manifest


class ArduinoLibSyncer(LibSyncerBase):

    @staticmethod
    def get_manifest_name():
        return "library.properties"

    @staticmethod
    def load_config(manifest_url):
        manifest = {}
        config_text = requests.get(manifest_url).text.encode("utf-8")
        for line in config_text.split("\n"):
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            manifest[key.strip()] = value.strip()
        assert set(manifest.keys()) >= set(
            ["name", "version", "author", "sentence"])

        #####
        keywords = []
        for keyword in re.split(r"[\s/]+",
                                manifest.get("category", "Uncategorized")):
            keyword = keyword.strip()
            if not keyword:
                continue
            keywords.append(keyword.lower())

        #####
        platforms = []
        platforms_map = {
            "avr": "atmelavr",
            "sam": "atmelsam",
            "samd": "atmelsam",
            "esp8266": "espressif8266",
            "esp32": "espressif32",
            "arc32": "intel_arc32",
            "stm32": "ststm32"
        }
        for arch in manifest.get("architectures", "").split(","):
            assert "particle-" not in arch, "Particle is not supported yet"
            arch = arch.strip()
            if arch == "*":
                platforms = "*"
                break
            if arch in platforms_map:
                platforms.append(platforms_map[arch])

        #####
        authors = []
        for author in manifest['author'].split(","):
            name, email = ArduinoLibSyncer.parse_author_name_and_email(author)
            if not name:
                continue
            authors.append(dict(name=name, email=email, maintainer=False))
        for author in manifest.get("maintainer", "").split(","):
            name, email = ArduinoLibSyncer.parse_author_name_and_email(author)
            if not name:
                continue
            found = False
            for item in authors:
                if item['name'].lower() != name.lower():
                    continue
                found = True
                item['maintainer'] = True
                if not item['email']:
                    item['email'] = email
            if not found:
                authors.append(dict(name=name, email=email, maintainer=True))

        #####
        repository = {"type": "git", "url": manifest.get("url")}
        repo_parse = urlparse(manifest_url)
        repo_path_tokens = repo_parse.path[1:].split("/")[:-1]
        if "github" in repo_parse.netloc:
            repository['url'] = "%s://github.com/%s" % (
                repo_parse.scheme, "/".join(repo_path_tokens[:2]))
        elif "raw" in repo_path_tokens:
            repository['url'] = "%s://%s/%s" % (
                repo_parse.scheme, repo_parse.netloc, "/".join(
                    repo_path_tokens[:repo_path_tokens.index("raw")]))

        #####
        include = None
        if "github" in repo_parse.netloc:
            include = "/".join(repo_path_tokens[3:]) or None
        elif "raw" in repo_path_tokens:
            include = "/".join(
                repo_path_tokens[repo_path_tokens.index("raw") + 2:]) or None

        #####
        homepage = None
        if manifest.get("url", None) and manifest['url'] != repository['url']:
            homepage = manifest['url']

        config = {
            "name": manifest['name'],
            "version": manifest['version'],
            "keywords": keywords,
            "description": manifest['sentence'],
            "frameworks": "arduino",
            "platforms": platforms,
            "authors": authors,
            "repository": repository,
            "homepage": homepage,
            "export": {
                "include":
                include,
                "exclude":
                ["extras", "docs", "tests", "test", "*.doxyfile", "*.pdf"]
            }
        }
        return config

    @staticmethod
    def parse_author_name_and_email(author):
        if author == "None" or "://" in author:
            return (None, None)
        name = author
        email = None
        for ldel, rdel in [("<", ">"), ("(", ")")]:
            if ldel in author and rdel in author:
                name = author[:author.index(ldel)]
                email = author[author.index(ldel) + 1:author.index(rdel)]
        return (name.strip(), email.strip() if email else None)


class YottaLibSyncer(LibSyncerBase):

    @staticmethod
    def get_manifest_name():
        return "module.json"

    @staticmethod
    def load_config(manifest_url):
        config_text = requests.get(manifest_url).text.encode("utf-8")
        manifest = json.loads(config_text)

        #####
        authors = []
        for author in manifest.get("author", "").split(","):
            name, email = ArduinoLibSyncer.parse_author_name_and_email(author)
            if not name:
                continue
            authors.append(dict(name=name, email=email, maintainer=False))

        #####
        repository = manifest.get("repository")
        if not repository or repository.get("url", "").startswith("git@"):
            assert "githubusercontent.com" in manifest_url
            username, reponame, _ = urlparse(manifest_url).path[1:].split(
                "/", 2)
            repository = {
                "type": "git",
                "url": "https://github.com/%s/%s" % (username, reponame)
            }

        config = {
            "name": manifest['name'],
            "version": manifest['version'],
            "keywords": manifest['keywords'],
            "description": manifest['description'],
            "frameworks": "mbed",
            "platforms": "*",
            "authors": authors,
            "repository": repository,
            "homepage": manifest.get("homepage"),
            "dependencies": manifest.get("dependencies"),
            "license": manifest.get("license"),
            "export": {
                "exclude": ["tests", "test", "*.doxyfile", "*.pdf"]
            }
        }
        return config
