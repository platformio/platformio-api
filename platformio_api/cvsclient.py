# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

import logging
import re
from datetime import datetime
from os import listdir, mkdir, remove
from os.path import dirname, isdir, isfile, join
from shutil import copy, copytree, rmtree
from subprocess import CalledProcessError, check_call
from sys import modules
from tempfile import mkdtemp, mkstemp

import requests
from PyGithub import BlockingBuilder

from platformio_api import config
from platformio_api.util import download_file, extract_archive

logger = logging.getLogger(__name__)


class CVSClientFactory(object):

    @staticmethod
    def newClient(type_, url):
        assert type_ in ("git", "svn", "hg")
        if "github.com/" in url:
            type_ = "github"
        if "developer.mbed.org" in url:
            type_ = "mbed"
        clsname = "%sClient" % type_.title()
        obj = getattr(modules[__name__], clsname)(url)
        assert isinstance(obj, BaseClient)
        return obj


class BaseClient(object):

    def __init__(self, url):
        self.url = url

    def clone(self, destination_dir):
        raise NotImplementedError()

    def get_last_commit(self):
        raise NotImplementedError()

    def get_type(self):
        return self.__class__.__name__.lower().replace("client", "")

    def _download_and_unpack_archive(self, url, destination_dir):
        arch_path = mkstemp(".tar.gz")[1]
        tmpdir = mkdtemp()
        try:
            download_file(url, arch_path)
            extract_archive(arch_path, tmpdir)

            srcdir = join(tmpdir, listdir(tmpdir)[0])
            assert isdir(srcdir)

            for item in listdir(srcdir):
                item_path = join(srcdir, item)
                if isfile(item_path):
                    copy(item_path, join(destination_dir, item))
                else:
                    copytree(item_path, join(destination_dir, item))
        finally:
            remove(arch_path)
            rmtree(tmpdir)


class GitClient(BaseClient):

    def __init__(self, url):
        raise NotImplementedError()


class HgClient(BaseClient):

    def __init__(self, url):
        raise NotImplementedError()


class SvnClient(BaseClient):

    def __init__(self, url):
        raise NotImplementedError()


class GithubClient(BaseClient):

    def __init__(self, url):
        BaseClient.__init__(self, url)
        self._repoapi = None

    def get_last_commit(self, path=None):
        commit = None
        folder_depth = 20
        while folder_depth:
            folder_depth -= 1
            commits = list(self._repoapi_instance().get_commits(
                path=path, per_page=1))

            if commits:
                commit = commits[1]

            if commit or not path or path == "/":
                break
            path = dirname(path)

        assert commit is not None
        return dict(
            sha=commit.sha,
            date=commit.commit.author.date
        )

    def get_owner(self):
        api = self._repoapi_instance()
        return dict(
            name=api.owner.name if api.owner.name else api.owner.login,
            email=api.owner.email,
            url=api.owner.html_url
        )

    def clone(self, destination_dir):
        api = self._repoapi_instance()
        url = ("https://codeload.github.com/%s/legacy.tar.gz/%s" % (
            api.full_name, api.default_branch
        ))
        self._download_and_unpack_archive(url, destination_dir)

    def _repoapi_instance(self):
        if self._repoapi is None:
            api = BlockingBuilder().Login(
                config['GITHUB_LOGIN'], config['GITHUB_PASSWORD']).Build()

            _url = self.url[self.url.index("github.com/")+11:]
            if _url.endswith(".git"):
                _url = _url[:-4]
            _login, _reponame = _url.split("/")[:2]
            self._repoapi = api.get_repo((_login, _reponame))

        return self._repoapi


class MbedClient(BaseClient):

    def __init__(self, url):
        BaseClient.__init__(self, url)
        self._last_commit = None

    def get_last_commit(self, path=None):
        if self._last_commit is not None:
            return self._last_commit
        history_url = self.url + "shortlog"
        logger.debug("Fetching commit metadata on URL: %s" % history_url)
        r = requests.get(history_url)
        assert 200 == r.status_code, \
            "HTTP status code is not OK. Returned code: %s" % r.status_code
        html = r.text
        sha = re.search("\d+:(?P<sha>[a-f0-9]{12})", html)
        date_string = re.search("\d{2} [a-zA-Z]{3} [0-9]{4}", html).group()
        date = datetime.strptime(date_string, "%d %b %Y").date()
        assert sha and date, "Unable to fetch commit metadata. " \
                             "SHA: %s. Date: %s." % (sha, date)
        self._last_commit = dict(
            sha=sha.groupdict()['sha'],
            date=date
        )
        return self._last_commit

    def clone(self, destination_dir):
        try:
            archive_url = "%(repo_url)sarchive/%(sha)s.tar.gz" % dict(
                repo_url=self.url, sha=self.get_last_commit()['sha'])
            self._download_and_unpack_archive(archive_url, destination_dir)
        except CalledProcessError:
            logger.info("Unable to extract repo archive. Cloning archive with "
                        "hg.")
            rmtree(destination_dir)
            mkdir(destination_dir)
            check_call(["hg", "clone", self.url, destination_dir])
