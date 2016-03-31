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
from github import Github, GithubObject
from git import Repo, GitCommandError

from platformio_api import config
from platformio_api.util import download_file, extract_archive

logger = logging.getLogger(__name__)


class CVSClientFactory(object):

    @staticmethod
    def newClient(type_, url, branch=None):
        assert type_ in ("git", "svn", "hg")
        if "github.com/" in url:
            type_ = "github"
        if "developer.mbed.org" in url:
            type_ = "mbed"
        if "bitbucket.org" in url:
            type_ = "bitbucket"
        clsname = "%sClient" % type_.title()
        obj = getattr(modules[__name__], clsname)(url, branch)
        assert isinstance(obj, BaseClient)
        return obj


class BaseClient(object):

    def __init__(self, url, branch):
        self.url = url
        self.branch = branch

    def clone(self, destination_dir, revision=None):
        """Clone the source code into the destination_dir.

        Optional `revision` argument may be the identifier of commit (usually
        SHA1), branch name or tag name. Defaults to the last commit in the
        branch specified during initialization. If branch is not specified,
        main repository branch will be used.
        """
        raise NotImplementedError()

    def get_last_commit(self, path=None):
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
                    copytree(
                        item_path,
                        join(destination_dir, item),
                        symlinks=True
                    )
        finally:
            remove(arch_path)
            rmtree(tmpdir)


class GitClient(BaseClient):

    def __init__(self, url, branch):
        super(GitClient, self).__init__(url, branch)
        self.repo = None

    def clone(self, destination_dir, revision=None):
        repo = self._initialize_repo()
        if revision:
            if revision != self.branch and revision not in repo.tags:
                try:
                    repo.git.fetch('origin', 'tag', revision, depth=1)
                except GitCommandError:
                    pass
            repo.git.checkout(revision)

        if isdir(destination_dir):
            rmtree(destination_dir)
        copytree(repo.working_tree_dir, destination_dir)
        rmtree(join(destination_dir, ".git"))

    def get_last_commit(self, path=None):
        if path:
            raise NotImplementedError("`path` is not supported by GitClient")

        repo = self._initialize_repo()
        commit = repo.commit()
        return dict(sha=commit.hexsha,
                    date=datetime.fromtimestamp(commit.committed_date))

    def _initialize_repo(self):
        if not self.repo:
            kwargs = {
                'depth': 1,
                'single-branch': True,
            }
            if self.branch:
                kwargs['branch'] = self.branch
            self.repo = Repo.clone_from(
                self.url, mkdtemp(prefix='gitclient-repo-'), **kwargs
            )

        return self.repo

    def __del__(self):
        if self.repo and isdir(self.repo.working_tree_dir):
            rmtree(self.repo.working_tree_dir)


class HgClient(BaseClient):

    def __init__(self, url, branch):
        raise NotImplementedError()


class SvnClient(BaseClient):

    def __init__(self, url, branch):
        raise NotImplementedError()


class GithubClient(BaseClient):

    def __init__(self, url, branch):
        BaseClient.__init__(self, url, branch)
        self._repoapi = None

    def get_last_commit(self, path=None):
        path = path or GithubObject.NotSet

        commit = None
        repo = self._repoapi_instance()
        revision = self.branch or repo.default_branch or GithubObject.NotSet
        folder_depth = 20
        while folder_depth:
            folder_depth -= 1
            commits = repo.get_commits(sha=revision,
                                       path=path)

            if commits:
                commit = commits[0]

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

    def clone(self, destination_dir, revision=None):
        api = self._repoapi_instance()
        if revision is None:
            revision = self.branch or api.default_branch
        url = ("https://codeload.github.com/%s/legacy.tar.gz/%s" % (
            api.full_name, revision
        ))
        self._download_and_unpack_archive(url, destination_dir)

    def _repoapi_instance(self):
        if self._repoapi is None:
            api = Github(config['GITHUB_LOGIN'], config['GITHUB_PASSWORD'])

            repo = self.url[self.url.index("github.com/") + 11:]
            if repo.endswith(".git"):
                repo = repo[:-4]
            repo = repo.rstrip("/")
            self._repoapi = api.get_repo(repo)

        return self._repoapi


class MbedClient(BaseClient):

    def __init__(self, url, branch):
        BaseClient.__init__(self, url, branch)
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

    def clone(self, destination_dir, revision=None):
        if revision is None:
            revision = self.get_last_commit()['sha']
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
            check_call(["hg", "clone", "--updaterev", revision, self.url,
                        destination_dir])


class BitbucketClient(BaseClient):

    MAIN_BRANCH_URL = "https://api.bitbucket.org/1.0/repositories/%(owner)s/" \
                      "%(repo_slug)s/main-branch"
    COMMITS_URL = "https://bitbucket.org/" \
                  "api/2.0/repositories/%(owner)s/%(repo_slug)s/commits/" \
                  "%(revision)s"
    ARCHIVE_URL = "https://bitbucket.org/" \
                  "%(owner)s/%(repo_slug)s/get/%(revision)s.tar.gz"

    def __init__(self, url, branch):
        BaseClient.__init__(self, url, branch)
        self._last_commit = None

        # Extract username and repo slug from url
        _, valuable_part = self.url.split("bitbucket.org/")
        parts = valuable_part.split('/')
        self.owner = parts[0]
        self.repo_slug = parts[1]

    def get_last_commit(self, path=None):
        if self._last_commit is not None:
            return self._last_commit

        if not self.branch:
            self.retrieve_main_branch()

        response = requests.get(self.COMMITS_URL % dict(
            owner=self.owner,
            repo_slug=self.repo_slug,
            revision=self.branch,
        ))
        assert 200 == response.status_code, "Bitbucket API request failed"

        commit = response.json()["values"][0]
        self._last_commit = dict(
            sha=commit["hash"],
            date=datetime.strptime(commit["date"], "%Y-%m-%dT%H:%M:%S+00:00")
        )
        return self._last_commit

    def clone(self, destination_dir, revision=None):
        if revision is None:
            revision = self.get_last_commit()['sha']
        url = self.ARCHIVE_URL % dict(
            owner=self.owner, repo_slug=self.repo_slug,
            revision=revision,
        )
        self._download_and_unpack_archive(url, destination_dir)

    def retrieve_main_branch(self):
        response = requests.get(self.MAIN_BRANCH_URL % dict(
            owner=self.owner,
            repo_slug=self.repo_slug,
        ))
        assert 200 == response.status_code, "Bitbucket API request failed"

        self.branch = response.json()["name"]
