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
from time import sleep

import requests
from urlparse import urlparse
from github import Github, enable_console_debug_logging
from platformio_api import config, models, util
from platformio_api.database import db_session

DEBUG = True
logging.basicConfig()
logger = logging.getLogger('git-terrier')
logger.setLevel(20)


def get_known_manifests():
    return [
        conf_url for (
            conf_url, ) in db_session.query(models.PendingLibs.conf_url).all()
    ]


# Get n-stars library
def search_github_repos(search_request, gh_user, gh_password, gh_stars):
    def _get_repo(url):
        url_parse = urlparse(url)
        if not url_parse.netloc.startswith(
            ("raw.githubusercontent.com", "github.com")):
            return None
        path_tokens = url_parse.path.split('/')
        if len(path_tokens) < 3:
            return None
        author = path_tokens[1]
        repo = path_tokens[2]
        return '%s/%s' % (author, repo)

    def _is_unknown_repository(url, known_repos):
        return not _get_repo(url) in known_repos

    g = Github(gh_user, gh_password, per_page=1000)
    # enable_console_debug_logging()
    github_repos = []
    counter = 0
    known_repos = set(
        [_get_repo(manifest) for manifest in get_known_manifests()])
    for result in g.search_code(search_request):
        if _is_unknown_repository(result.html_url, known_repos):
            continue
        if result.repository.stargazers_count < gh_stars:
            continue
        full_name_tokens = result.repository.full_name.split('/')
        github_repos.append({
            "owner": full_name_tokens[0],
            "name": full_name_tokens[1],
            "default_branch": result.repository.default_branch
        })
        if DEBUG:
            counter += 1
        if counter == 5:
            print github_repos
            break
    return github_repos


# ensure that found libs are arduino libs
def check_repos(repos):
    checked_repos = set()
    for repo in repos:
        unzip_folder = mkdtemp()
        archive_path = os.path.join(unzip_folder,
                                    "%s.zip" % repo["default_branch"])
        util.download_file("https://github.com/%s/%s/archive/%s.zip" %
                           (repo["owner"], repo["name"],
                            repo["default_branch"]), archive_path)
        try:
            util.extract_archive(archive_path, unzip_folder)
            logger.info("Zip ok! for repo: %s" % repo["name"])
            file_extensions = set()
            for _, __, files in os.walk(unzip_folder):
                for lib_file in files:
                    file_extensions.add(os.path.splitext(lib_file)[1])
        finally:
            shutil.rmtree(unzip_folder)
        if set(['.c', '.cpp', '.h']).isdisjoint(file_extensions):
            continue
        if ".json" in file_extensions:
            checked_repos.add(
                "https://raw.githubusercontent.com/%s/%s/%s/library.json" %
                (repo["owner"], repo["name"], repo["default_branch"]))
        else:
            checked_repos.add(
                "https://raw.githubusercontent.com/%s/%s/%s/library.properties"
                % (repo["owner"], repo["name"], repo["default_branch"]))
    return checked_repos


# register found libraries in Platformio
def register_new_repos(new_repos):
    for new_repo in new_repos:
        logger.info(new_repo)
        if not DEBUG:
            subprocess.call(["platformio", "lib", "register", new_repo])
