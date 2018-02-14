# Copyright (c) 2014-present PlatformIO <contact@platformio.org>
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
import os
import shutil
import subprocess
from tempfile import mkdtemp
from urlparse import urlparse

from github import Github

from platformio_api import models, util
from platformio_api.database import db_session

logging.basicConfig()
logger = logging.getLogger('git-terrier')
logger.setLevel(20)


class GithubTerrier(object):
    def __init__(self, gh_login, gh_pass, query, min_repo_stars):
        self.gh_login = gh_login
        self.gh_pass = gh_pass
        self.query = query
        self.min_repo_stars = min_repo_stars
        self._known_repos = set()

    def add_known_repo(self, manifest_url):
        self._known_repos.add(self.get_repo_fullname(manifest_url))

    def is_known_manifest(self, url):
        return self.get_repo_fullname(url) in self._known_repos

    def get_repo_fullname(self, manifest_url):
        url_parse = urlparse(manifest_url)
        if not url_parse.netloc.startswith(
            ("raw.githubusercontent.com", "github.com")):
            return None
        path_tokens = url_parse.path.split('/')
        if len(path_tokens) < 3:
            return None
        author = path_tokens[1]
        repo = path_tokens[2]
        return '%s/%s' % (author, repo)

    def run(self):
        for (conf_url, ) in db_session.query(
                models.PendingLibs.conf_url).all():
            self.add_known_repo(conf_url)
        g = Github(self.gh_login, self.gh_pass, per_page=1000)
        for result in g.search_code(self.query):
            if self.is_known_manifest(result.html_url):
                continue
            self._process_repository(result.repository)

    def _process_repository(self, repository):
        logger.info("Processing repo: %s" % repository.full_name)
        if repository.stargazers_count < self.min_repo_stars:
            return
        manifest_url = self._maybe_manifest_url(repository)
        if not manifest_url:
            return
        self.register_manifest(manifest_url)

    def register_manifest(self, manifest_url):
        self.add_known_repo(manifest_url)
        subprocess.call(["platformio", "lib", "register", manifest_url])

    def _maybe_manifest_url(self, repository):
        unzip_folder = mkdtemp()
        archive_path = os.path.join(unzip_folder,
                                    "%s.zip" % repository.default_branch)
        util.download_file("https://github.com/%s/archive/%s.zip" %
                           (repository.full_name,
                            repository.default_branch), archive_path)
        try:
            util.extract_archive(archive_path, unzip_folder)
            file_extensions = set()
            for _, __, files in os.walk(unzip_folder):
                for lib_file in files:
                    file_extensions.add(os.path.splitext(lib_file)[1])
        finally:
            shutil.rmtree(unzip_folder)

        if set(['.c', '.cpp', '.h']).isdisjoint(file_extensions):
            return
        if ".json" in file_extensions:
            manifest_type = ".json"
        else:
            manifest_type = ".properties"
        return "https://raw.githubusercontent.com/%s/%s/library%s" % (
            repository.full_name, repository.default_branch, manifest_type)
