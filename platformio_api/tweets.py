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
import os
import re
from time import time
from os.path import basename, join, isdir, isfile, getmtime

import requests
from bs4 import BeautifulSoup

try:
    from urlparse import urlparse
except ImportError:
    from urllib.parse import urlparse

MEDIA_CACHE_EXPIRE = 86400 * 180  # 180 days

session = requests.Session()


def get_twitter_content(username, url, stream=False):
    headers = {
        "Accept":
        "application/json, text/javascript, */*; q=0.01",
        "Referer":
        "https://twitter.com/%s" % username,
        "User-Agent":
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_6) AppleWebKit"
         "/603.3.8 (KHTML, like Gecko) Version/10.1.2 Safari/603.3.8"),
        "X-Twitter-Active-User":
        "yes",
        "X-Requested-With":
        "XMLHttpRequest"
    }
    r = session.get(url, headers=headers, stream=stream, timeout=2)
    r.raise_for_status()
    return r if stream else r.text


def parse_tweets(username, output_dir, output_base_url):
    if not isdir(output_dir):
        os.makedirs(output_dir)
    cleanup_media_files(output_dir)

    api_url = ("https://twitter.com/i/profiles/show/%s/timeline/tweets?"
               "include_available_features=1&include_entities=1&"
               "include_new_items_bar=true") % username
    content = json.loads(get_twitter_content(username, api_url))
    assert "items_html" in content
    soup = BeautifulSoup(content['items_html'], "html.parser")
    find_attrs = {"class": "tweet", "data-tweet-id": True}
    tweet_nodes = soup.find_all("div", attrs=find_attrs)

    items = []
    for node in tweet_nodes:
        item = _parse_tweet_node(username, node)
        item = _cache_tweet_media(username, item, output_dir, output_base_url)
        items.append(item)
    return items


def _parse_tweet_node(username, tweet):
    # remove non-visible items
    for node in tweet.find_all(class_=["invisible", "u-hidden"]):
        node.decompose()
    twitter_url = "https://twitter.com"
    time_node = tweet.find("span", attrs={"data-time": True})
    text_node = tweet.find(class_="tweet-text")
    quote_text_node = tweet.find(class_="QuoteTweet-text")
    if quote_text_node and not text_node.get_text().strip():
        text_node = quote_text_node
    photos = [
        node.get("data-image-url") for node in (tweet.find_all(class_=[
            "AdaptiveMedia-photoContainer", "QuoteMedia-photoContainer"
        ]) or [])
    ]
    urls = [
        node.get("data-expanded-url")
        for node in (quote_text_node or text_node).find_all(
            class_="twitter-timeline-link",
            attrs={"data-expanded-url": True}
        )
    ]  # yapf: disable

    # fetch data from iframe card
    if (not photos or not urls) and tweet.get("data-card2-type"):
        iframe_node = tweet.find("div",
                                 attrs={"data-full-card-iframe-url": True})
        if iframe_node:
            iframe_card = _fetch_iframe_card(
                username,
                twitter_url + iframe_node.get("data-full-card-iframe-url"))
            if not photos and iframe_card['photo']:
                photos.append(iframe_card['photo'])
            if not urls and iframe_card['url']:
                urls.append(iframe_card['url'])
            if iframe_card['text_node']:
                text_node = iframe_card['text_node']

    if not photos:
        photos.append(tweet.find("img", class_="avatar").get("src"))

    def _fetch_text(text_node):
        text = text_node.decode_contents(formatter="html").strip()
        text = re.sub(r'href="/', 'href="%s/' % twitter_url, text)
        if "</p>" not in text and "<br" not in text:
            text = re.sub(r"\n+", "<br />", text)
        return text

    return {
        "tweetId": tweet.get("data-tweet-id"),
        "tweetUrl": twitter_url + tweet.get("data-permalink-path"),
        "author": tweet.get("data-name"),
        "time": int(time_node.get("data-time")),
        "timeFormatted": time_node.string,
        "text": _fetch_text(text_node),
        "entries": {
            "urls": urls,
            "photos": [uri for uri in photos]
        },
        "isPinned": "user-pinned" in tweet.get("class")
    }


def _fetch_iframe_card(username, url):
    html = get_twitter_content(username, url)
    soup = BeautifulSoup(html, "html.parser")
    photo_node = soup.find("img", attrs={"data-src": True})
    url_node = soup.find("a", class_="TwitterCard-container")
    text_node = soup.find("div", class_="SummaryCard-content")
    if text_node:
        text_node.find("span", class_="SummaryCard-destination").decompose()
    return {
        "photo": photo_node.get("data-src") if photo_node else None,
        "text_node": text_node,
        "url": url_node.get("href") if url_node else None
    }


def _cache_tweet_media(username, item, output_dir, output_base_url):
    if not item['entries']['photos']:
        return item
    new_photos = []
    for url in item['entries']['photos']:
        parsed_url = urlparse(url)
        file_name = basename(parsed_url.path)
        if "." not in file_name and "format" in parsed_url.query:
            for param in parsed_url.query.split("&"):
                if "=" not in param:
                    continue
                name, value = param.split("=", 1)
                if name == "format":
                    file_name += "." + value
        cache_path = join(output_dir, file_name)
        if not isfile(cache_path):
            with open(cache_path, "wb") as fp:
                for chunk in get_twitter_content(username, url, stream=True):
                    fp.write(chunk)
        new_photos.append("%s/%s" % (output_base_url, basename(cache_path)))

    item['entries']['photos'] = new_photos
    return item


def cleanup_media_files(output_dir):
    for item in os.listdir(output_dir):
        media_path = join(output_dir, item)
        if not isfile(media_path) or item.endswith(".json"):
            continue
        if (time() - getmtime(media_path)) > MEDIA_CACHE_EXPIRE:
            os.remove(media_path)
