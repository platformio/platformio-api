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
from datetime import datetime
from os import listdir, mkdir, remove
from os.path import dirname, exists, isdir, isfile, join
from shutil import copy, copytree, rmtree
from subprocess import CalledProcessError, check_call
from sys import modules
from tempfile import mkdtemp, mkstemp

import requests
from git import Repo
from github import Github, GithubObject

from platformio_api import config, util

logger = logging.getLogger(__name__)


class VCSClientFactory(object):

    @staticmethod
    def newClient(type_, url, branch=None, tag=None):
        assert type_ in ("git", "svn", "hg")
        if "github.com/" in url:
            type_ = "github"
        if util.is_mbed_repository(url):
            type_ = "mbed"
        if "bitbucket.org" in url:
            type_ = "bitbucket"
        clsname = "%sVCSClient" % type_.title()
        obj = getattr(modules[__name__], clsname)(url, branch, tag)
        assert isinstance(obj, VCSBaseClient)
        return obj


class VCSBaseClient(object):

    def __init__(self, url, branch=None, tag=None):
        self.url = url
        self.branch = branch
        self.tag = tag

    def clone(self, destination_dir):
        raise NotImplementedError()

    @property
    def default_branch(self):
        raise NotImplementedError

    def get_last_commit(self, path=None):
        raise NotImplementedError()

    def get_type(self):
        return self.__class__.__name__.lower().replace("vcsclient", "")

    def _download_and_unpack_archive(self, url, destination_dir):
        arch_path = mkstemp(".tar.gz")[1]
        try:
            util.download_file(url, arch_path)
            util.extract_archive(arch_path, destination_dir)

            items = listdir(destination_dir)
            subdir = None
            if len(items) == 1 and isdir(join(destination_dir, items[0])):
                subdir = join(destination_dir, items[0])
            if subdir:
                for item in listdir(subdir):
                    item_path = join(subdir, item)
                    if isfile(item_path):
                        copy(item_path, join(destination_dir, item))
                    else:
                        copytree(
                            item_path,
                            join(destination_dir, item),
                            symlinks=True)
                rmtree(subdir)
        finally:
            remove(arch_path)


class GitVCSClient(VCSBaseClient):

    def __init__(self, url, branch=None, tag=None):
        VCSBaseClient.__init__(self, url, branch, tag)
        self._repo = self._init_repo()

    def _init_repo(self):
        kwargs = {"single-branch": True}
        if not self.tag:
            kwargs['depth'] = 1
        if self.branch:
            kwargs['branch'] = self.branch
        repo = Repo.clone_from(
            self.url, mkdtemp(prefix='gitclient-repo-'), **kwargs)

        if not self.tag:
            return repo

        _tag = None
        if self.tag in repo.tags:
            _tag = self.tag
        elif ("v" + self.tag) in repo.tags:
            _tag = "v" + self.tag
        if not _tag:
            return repo
        repo.git.checkout(_tag)
        return repo

    def clone(self, destination_dir):
        if isdir(destination_dir):
            rmtree(destination_dir)
        copytree(self._repo.working_tree_dir, destination_dir, symlinks=True)
        rmtree(join(destination_dir, ".git"))

    def get_last_commit(self, path=None):
        if path:
            raise NotImplementedError(
                "`path` is not supported by GitVCSClient")

        commit = self._repo.commit()
        return dict(
            sha=commit.hexsha,
            date=datetime.fromtimestamp(commit.committed_date))

    def __del__(self):
        if self._repo and isdir(self._repo.working_tree_dir):
            rmtree(self._repo.working_tree_dir)


class HgVCSClient(VCSBaseClient):

    def __init__(self, url, branch=None, tag=None):
        raise NotImplementedError()


class SvnVCSClient(VCSBaseClient):

    def __init__(self, url, branch=None, tag=None):
        raise NotImplementedError()


class GithubVCSClient(VCSBaseClient):

    def __init__(self, url, branch=None, tag=None):
        VCSBaseClient.__init__(self, url, branch, tag)
        self._repo = self._init_repo()

    def _init_repo(self):
        api = Github(config['GITHUB_LOGIN'], config['GITHUB_PASSWORD'])

        reposlug = self.url[self.url.index("github.com/") + 11:]
        if reposlug.endswith(".git"):
            reposlug = reposlug[:-4]
        reposlug = reposlug.rstrip("/")
        repo = api.get_repo(reposlug)

        if not self.tag:
            return repo

        _tag = None
        for tag in repo.get_tags():
            if tag.name in (self.tag, "v" + self.tag):
                _tag = tag.name
                break
        self.tag = _tag

        return repo

    @property
    def default_branch(self):
        return self._repo.default_branch

    def get_last_commit(self, path=None):
        path = path or GithubObject.NotSet
        commit = None
        revision = (self.tag or self.branch or self.default_branch
                    or GithubObject.NotSet)
        folder_depth = 20
        while folder_depth:
            folder_depth -= 1
            commits = self._repo.get_commits(sha=revision, path=path)

            if commits:
                try:
                    commit = commits[0]
                except IndexError:
                    pass

            if commit or not path or path == "/":
                break
            path = dirname(path)

        assert commit is not None
        return dict(sha=commit.sha, date=commit.commit.author.date)

    def get_owner(self):
        return dict(
            name=self._repo.owner.name
            if self._repo.owner.name else self._repo.owner.login,
            email=self._repo.owner.email,
            url=self._repo.owner.html_url)

    def clone(self, destination_dir):
        url = ("https://codeload.github.com/%s/legacy.tar.gz/%s" %
               (self._repo.full_name,
                self.tag or self.branch or self.default_branch))
        self._download_and_unpack_archive(url, destination_dir)


class MbedVCSClient(VCSBaseClient):

    def __init__(self, url, branch=None, tag=None):
        VCSBaseClient.__init__(self, url, branch, tag)
        self._last_commit = None

    def get_last_commit(self, path=None):
        try:
            self._last_commit = self._get_last_commit_by_ref(path)
        except:
            self._last_commit = self._get_last_commit_by_home(path)
        assert self._last_commit
        return self._last_commit

    def _get_last_commit_by_ref(self, path=None):
        if self._last_commit is not None:
            return self._last_commit
        lastrev_url = self.url + "rev/"
        logger.debug("Fetching last revision on URL: %s" % lastrev_url)
        r = requests.get(lastrev_url)
        assert 200 == r.status_code, \
            "HTTP status code is not OK. Returned code: %s" % r.status_code
        html = r.text
        sha = re.search(r"Revision \d+:([a-f\d]{12}),", html).group(1)
        # Fri Nov 18 11:10:04 2016 -0600
        date_string = re.search(
            r"([a-z]{3} [a-z]{3} \d{2} [\d:]{8} \d{4}) (?:\+|\-)\d{4}",
            html,
            flags=re.I).group(1)
        # Fri Nov 18 11:10:04 2016
        date = datetime.strptime(date_string, "%a %b %d %H:%M:%S %Y")
        assert sha and date
        return dict(sha=sha, date=date)

    def _get_last_commit_by_home(self, path=None):
        if self._last_commit is not None:
            return self._last_commit
        logger.debug("Fetching last revision on URL: %s" % self.url)
        r = requests.get(self.url)
        assert 200 == r.status_code, \
            "HTTP status code is not OK. Returned code: %s" % r.status_code
        html = r.text
        sha = re.search(r"Files at revision \d+:([a-f\d]{12})", html).group(1)
        # 2014-03-08T21:44:56+00:00
        date_string = re.search(r'="([\d\-]{10}T[\d:]{8})\+00:00"',
                                html).group(1)
        # 2014-03-08T21:44:56
        date = datetime.strptime(date_string, "%Y-%m-%dT%H:%M:%S")
        assert sha and date
        return dict(sha=sha, date=date)

    def clone(self, destination_dir):
        revision = self.tag or self.get_last_commit()['sha']
        try:
            archive_url = "%(repo_url)sarchive/%(revision)s.tar.gz" % dict(
                repo_url=self.url, revision=revision)
            self._download_and_unpack_archive(archive_url, destination_dir)
        except CalledProcessError:
            logger.info("Unable to extract repo archive. Cloning archive with "
                        "hg.")
            if exists(destination_dir):
                rmtree(destination_dir)
            mkdir(destination_dir)
            check_call([
                "hg", "clone", "--updaterev", revision, self.url,
                destination_dir
            ])


class BitbucketVCSClient(VCSBaseClient):

    MAIN_BRANCH_URL = "https://api.bitbucket.org/1.0/repositories/%(owner)s/" \
                      "%(repo_slug)s/main-branch"
    COMMITS_URL = "https://bitbucket.org/" \
                  "api/2.0/repositories/%(owner)s/%(repo_slug)s/commits/" \
                  "%(revision)s"
    TAGS_URL = "https://bitbucket.org/" \
                  "api/2.0/repositories/%(owner)s/%(repo_slug)s/refs/tags"

    ARCHIVE_URL = "https://bitbucket.org/" \
                  "%(owner)s/%(repo_slug)s/get/%(revision)s.tar.gz"

    def __init__(self, url, branch=None, tag=None):
        VCSBaseClient.__init__(self, url, branch, tag)
        self._last_commit = None
        self._init_repo()

    def _init_repo(self):
        _, valuable_part = self.url.split("bitbucket.org/")
        parts = valuable_part.split('/')
        self._owner = parts[0].lower()
        self._repo_slug = parts[1].lower()

        if not self.tag:
            return

        response = requests.get(self.TAGS_URL % dict(
            owner=self._owner,
            repo_slug=self._repo_slug,
        ))
        assert 200 == response.status_code, "Bitbucket API request failed"
        _tag = None
        for value in response.json()['values']:
            if value['type'] != "tag":
                continue
            if value['name'] in (self.tag, "v" + self.tag):
                _tag = value['name']
                break
        self.tag = _tag

    def get_last_commit(self, path=None):
        if self._last_commit:
            return self._last_commit
        revision = self.tag or self.branch or self.get_main_branch()
        response = requests.get(self.COMMITS_URL % dict(
            owner=self._owner,
            repo_slug=self._repo_slug,
            revision=revision,
        ))
        assert 200 == response.status_code, "Bitbucket API request failed"

        commit = response.json()["values"][0]
        self._last_commit = dict(
            sha=commit["hash"],
            date=datetime.strptime(commit["date"], "%Y-%m-%dT%H:%M:%S+00:00"))
        return self._last_commit

    def clone(self, destination_dir):
        revision = self.get_last_commit()['sha']
        url = self.ARCHIVE_URL % dict(
            owner=self._owner,
            repo_slug=self._repo_slug,
            revision=revision,
        )
        self._download_and_unpack_archive(url, destination_dir)

    def get_main_branch(self):
        response = requests.get(self.MAIN_BRANCH_URL % dict(
            owner=self._owner,
            repo_slug=self._repo_slug,
        ))
        assert 200 == response.status_code, "Bitbucket API request failed"
        return response.json()["name"]
