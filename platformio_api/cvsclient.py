# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

from os import listdir, remove
from os.path import dirname, isdir, isfile, join
from shutil import copy, copytree, rmtree
from sys import modules
from tempfile import mkdtemp, mkstemp

from PyGithub import BlockingBuilder

from platformio_api import config
from platformio_api.util import download_file, extract_archive


class CVSClientFactory(object):

    @staticmethod
    def newClient(type_, url):
        assert type_ in ("git", "svn")
        if "github.com/" in url:
            type_ = "github"
        clsname = "%sClient" % type_.title()
        obj = getattr(modules[__name__], clsname)(url)
        assert isinstance(obj, BaseClient)
        return obj


class BaseClient(object):

    def __init__(self, url):
        self.url = url

    def get_last_commit(self):
        raise NotImplementedError

    def get_type(self):
        return self.__class__.__name__.lower().replace("client", "")


class GitClient(BaseClient):

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
