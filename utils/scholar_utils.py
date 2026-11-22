# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections.abc import Callable, Iterable
import datetime
import html
import os
import re

import requests
from thefuzz import fuzz  # type: ignore
from dotenv import load_dotenv  # type: ignore

cur_dir = os.path.dirname(os.path.realpath(__file__))
load_dotenv(os.path.join(cur_dir, "../.env"))

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
CROSSREF_WORKS_URL = "https://api.crossref.org/works"
S2_FIELDS = "title,authors,venue,year,abstract,citationCount,journal,publicationDate"
Metadata = dict[str, object]
TitleSearch = Callable[[str, int | None, str | None], Metadata | None]


def _get_s2_headers() -> dict[str, str]:
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    return {"X-API-KEY": key} if key else {}


def _get_openalex_email() -> str:
    return os.environ.get("OPENALEX_EMAIL", "").strip()


def _date_parts(value: str) -> tuple[int, ...]:
    try:
        parts = tuple(map(int, value.split("-")))
        if 1 <= len(parts) <= 3:
            datetime.date(*(parts + (1,) * (3 - len(parts))))
            return parts
    except ValueError:
        pass
    return ()


def is_date_valid(p_date: object, p_year: object, cutoff_date: str | None) -> bool:
    if not cutoff_date or not (cutoff := _date_parts(cutoff_date)):
        return True
    published = _date_parts(str(p_date)) or _date_parts(str(p_year))
    if not published:
        return True
    if len(cutoff) == 1:
        return published[0] <= cutoff[0]
    published_day = published + (1,) * (3 - len(published))
    if len(cutoff) == 2:
        # Keep S2's existing exclusive first-of-month cutoff.
        return published_day < (*cutoff, 1)
    return published_day <= cutoff


def reconstruct_openalex_abstract(index: dict[str, list[int]] | None) -> str:
    return " ".join(word for _, word in sorted(
        (position, word) for word, positions in (index or {}).items() for position in positions
    ))


def strip_jats_tags(abstract: str | None) -> str:
    text = re.sub(r"</(?:jats:)?(?:p|title|sec)>", " ", abstract or "", flags=re.IGNORECASE)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", text)).split())


def _get_json(
    url: str, params: dict[str, str | int], headers: dict[str, str] | None = None
) -> Metadata:
    try:
        response = requests.get(url, params=params, headers=headers, timeout=5)
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, dict):
                return payload
    except (requests.RequestException, ValueError):
        # Request errors can embed URLs or credentials.
        pass
    print(f"      Literature request unsuccessful: {url}")
    return {}


def _items(value: object) -> list[Metadata]:
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def _s2_results(query: str, limit: int, fields: str) -> list[Metadata]:
    payload = _get_json(S2_SEARCH_URL, {"query": query, "limit": limit, "fields": fields},
                        _get_s2_headers())
    return _items(payload.get("data"))


def _openalex_results(query: str, limit: int) -> list[Metadata]:
    params: dict[str, str | int] = {"search": query, "per-page": limit}
    if email := _get_openalex_email():
        params["mailto"] = email
    return _items(_get_json(OPENALEX_WORKS_URL, params).get("results"))


def _mapping(value: object) -> Metadata:
    return value if isinstance(value, dict) else {}


def _page_range(biblio: Metadata) -> str | None:
    start, end = biblio.get("first_page"), biblio.get("last_page")
    if start and end and start != end:
        return f"{start}-{end}"
    page = start or end
    return str(page) if page else None


def _openalex_metadata(work: Metadata) -> Metadata:
    source = _mapping(_mapping(work.get("primary_location")).get("source"))
    venue = source.get("display_name") or ""
    biblio = _mapping(work.get("biblio"))
    authors = [
        {"name": name}
        for entry in _items(work.get("authorships"))
        if (name := _mapping(entry.get("author")).get("display_name"))
    ]
    index = work.get("abstract_inverted_index")
    return {
        "title": work.get("title") or work.get("display_name"),
        "authors": authors, "venue": venue, "year": work.get("publication_year"),
        "abstract": reconstruct_openalex_abstract(index if isinstance(index, dict) else None),
        "citationCount": work.get("cited_by_count") or 0,
        "journal": {"name": venue, "volume": biblio.get("volume"), "pages": _page_range(biblio)},
        "publicationDate": work.get("publication_date"),
    }


def _crossref_date(work: Metadata) -> tuple[int | None, str | None]:
    for name in ("published", "published-print", "published-online", "issued"):
        parts = _mapping(work.get(name)).get("date-parts")
        if isinstance(parts, list) and parts and parts[0] and isinstance(parts[0][0], int):
            year, *rest = parts[0]
            return year, f"{year:04d}" + "".join(f"-{part:02d}" for part in rest)
    return None, None


def _first_text(value: object) -> str:
    return value[0] if isinstance(value, list) and value and isinstance(value[0], str) else ""


def _crossref_metadata(work: Metadata) -> Metadata:
    venue = _first_text(work.get("container-title"))
    year, publication_date = _crossref_date(work)
    abstract = work.get("abstract")
    authors = [
        {"name": name}
        for author in _items(work.get("author"))
        if (name := " ".join(str(author[key]) for key in ("given", "family") if author.get(key))
            or author.get("name"))
    ]
    return {
        "title": _first_text(work.get("title")), "authors": authors, "venue": venue,
        "year": year, "abstract": strip_jats_tags(abstract if isinstance(abstract, str) else None),
        "citationCount": work.get("is-referenced-by-count") or 0,
        "journal": {"name": venue, "volume": work.get("volume"), "pages": work.get("page")},
        "publicationDate": publication_date,
    }


def _select_best_title_match(
    results: Iterable[Metadata], title_query: str, year_hint: int | None, cutoff_date: str | None
) -> Metadata | None:
    best, score = None, 70
    for result in results:
        title = result.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        if not is_date_valid(result.get("publicationDate"), result.get("year"), cutoff_date):
            continue
        ratio = fuzz.ratio(title_query.casefold(), title.casefold())
        if year_hint and result.get("year") == year_hint:
            ratio += 10
        if ratio > score:
            best, score = result, ratio
    return best


def s2_title_search(
    title_query: str, year_hint: int | None = None, cutoff_date: str | None = None
) -> Metadata | None:
    return _select_best_title_match(_s2_results(title_query, 3, S2_FIELDS),
                                    title_query, year_hint, cutoff_date)


def openalex_title_search(
    title_query: str, year_hint: int | None = None, cutoff_date: str | None = None
) -> Metadata | None:
    works = map(_openalex_metadata, _openalex_results(title_query, 3))
    return _select_best_title_match(works, title_query, year_hint, cutoff_date)


def crossref_title_search(
    title_query: str, year_hint: int | None = None, cutoff_date: str | None = None
) -> Metadata | None:
    payload = _get_json(CROSSREF_WORKS_URL, {"query.title": title_query, "rows": 3})
    works = map(_crossref_metadata, _items(_mapping(payload.get("message")).get("items")))
    return _select_best_title_match(works, title_query, year_hint, cutoff_date)


def enrich_paper_metadata(
    title_query: str, year_hint: int | None = None, cutoff_date: str | None = None
) -> Metadata | None:
    searches: list[tuple[str, TitleSearch]] = [
        ("S2", s2_title_search), ("OpenAlex", openalex_title_search), ("Crossref", crossref_title_search),
    ]
    preferred = None
    for source, search in searches:
        match = search(title_query, year_hint, cutoff_date)
        if match is None:
            continue
        if preferred is None:
            preferred = {**match, "_source": source, "_abstract_source": None}
        abstract = match.get("abstract")
        if isinstance(abstract, str) and abstract.strip():
            return {**preferred, "abstract": abstract, "_abstract_source": source}
    return preferred


def _candidates(works: Iterable[Metadata], source: str, cutoff_date: str | None) -> list[Metadata]:
    return [
        {"title": work["title"], "year": work.get("year"), "reason": f"Found via {source} search"}
        for work in works
        if work.get("title") and is_date_valid(work.get("publicationDate"), work.get("year"), cutoff_date)
    ]


def s2_search(query: str, limit: int = 10, cutoff_date: str | None = None) -> list[Metadata]:
    return _candidates(_s2_results(query, limit, "title,year,publicationDate"), "S2", cutoff_date)


def openalex_search(query: str, limit: int = 10, cutoff_date: str | None = None) -> list[Metadata]:
    works = map(_openalex_metadata, _openalex_results(query, limit))
    return _candidates(works, "OpenAlex", cutoff_date)
