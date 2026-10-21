#!/usr/bin/env python3
"""Update the Intermedia GitHub Pages site from Latin Soccer SD.

The script reads the public Intermedia page, extracts the current Apertura
standings and every completed match with a numeric final score, then replaces
only the generated data block in index.html.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://latinsoccersd.com/intermedia/"
INDEX_PATH = Path(__file__).with_name("index.html")
START = "/* AUTO_DATA_START */"
END = "/* AUTO_DATA_END */"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-US,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def get_html() -> str:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.get(SOURCE_URL, headers=HEADERS, timeout=30)
            r.raise_for_status()
            if len(r.text) < 5000:
                raise RuntimeError(f"Source page looks unexpectedly short ({len(r.text)} bytes)")
            return r.text
        except Exception as exc:  # retry transient site/network failures
            last_error = exc
            if attempt < 2:
                time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"Could not download {SOURCE_URL}: {last_error}")


def heading_with_text(soup: BeautifulSoup, pattern: str):
    rx = re.compile(pattern, re.I)
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        if rx.search(clean(tag.get_text(" ", strip=True))):
            return tag
    return None


def table_rows(table):
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if cells:
            rows.append([clean(c.get_text(" ", strip=True)) for c in cells])
    return rows


def parse_standings(soup: BeautifulSoup) -> list[list[object]]:
    heading = heading_with_text(soup, r"TORNEO\s+APERTURA\s+2026")
    table = heading.find_next("table") if heading else None
    if table is None:
        # Fallback: identify by characteristic column headings.
        for candidate in soup.find_all("table"):
            txt = clean(candidate.get_text(" ", strip=True)).upper()
            if all(x in txt for x in ("EQUIPO", "PJ", "PG", "PE", "PP", "PTS")):
                table = candidate
                break
    if table is None:
        raise RuntimeError("Could not find standings table")

    teams = []
    for cells in table_rows(table):
        if len(cells) < 10 or not re.fullmatch(r"\d+", cells[0]):
            continue
        try:
            # [name, PJ, PG, PE, PP, GF, GC, DG, Pts]
            teams.append([cells[1]] + [int(x) for x in cells[2:10]])
        except ValueError:
            continue
    if len(teams) < 10:
        raise RuntimeError(f"Standings parse returned only {len(teams)} teams")
    return teams


def parse_date(cell: str) -> str | None:
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", cell)
    if m:
        return m.group(1)
    # Fallback for visible Spanish dates such as "octubre 18, 2026".
    low = cell.lower()
    for name, month in MONTHS.items():
        m = re.search(rf"{name}\s+(\d{{1,2}}),?\s+(20\d{{2}})", low)
        if m:
            return f"{int(m.group(2)):04d}-{month:02d}-{int(m.group(1)):02d}"
    return None


def parse_matches(soup: BeautifulSoup) -> list[dict[str, object]]:
    heading = heading_with_text(soup, r"^RESULTADOS$")
    table = heading.find_next("table") if heading else None
    if table is None:
        # Fallback: among fixture-like tables, choose the one with the most score patterns.
        candidates = []
        for candidate in soup.find_all("table"):
            txt = clean(candidate.get_text(" ", strip=True))
            if "Partido" in txt and "Hora/Resultados" in txt:
                candidates.append((len(re.findall(r"\b\d+\s*-\s*\d+\b", txt)), candidate))
        if candidates:
            table = max(candidates, key=lambda x: x[0])[1]
    if table is None:
        raise RuntimeError("Could not find results table")

    matches = []
    seen = set()
    for cells in table_rows(table):
        if len(cells) < 5:
            continue
        date = parse_date(cells[0])
        if not date or " vs " not in cells[1].lower():
            continue
        score = re.search(r"(?<!\d)(\d{1,2})\s*-\s*(\d{1,2})(?!\d)", cells[2])
        if not score:
            # A time such as 11:15 am is not a result; skip until a final score is posted.
            continue
        # Split case-insensitively but preserve original team spelling.
        parts = re.split(r"\s+vs\s+", cells[1], maxsplit=1, flags=re.I)
        if len(parts) != 2:
            continue
        home, away = map(clean, parts)
        hs, ascore = map(int, score.groups())
        item = {
            "date": date,
            "home": home,
            "away": away,
            "hs": hs,
            "ascore": ascore,
            "field": clean(cells[3]),
            "round": clean(cells[4]),
        }
        key = (date, home, away, hs, ascore, item["field"], item["round"])
        if key not in seen:
            matches.append(item)
            seen.add(key)

    if len(matches) < 20:
        raise RuntimeError(f"Results parse returned only {len(matches)} completed matches")
    matches.sort(key=lambda m: m["date"], reverse=True)
    return matches


def replace_data(teams: list[list[object]], matches: list[dict[str, object]]) -> bool:
    html = INDEX_PATH.read_text(encoding="utf-8")
    if START not in html or END not in html:
        raise RuntimeError("index.html is missing AUTO_DATA markers. Upload the supplied index.html first.")

    teams_js = json.dumps(teams, ensure_ascii=False, separators=(",", ":"))
    matches_js = json.dumps(matches, ensure_ascii=False, separators=(",", ":"))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    generated = (
        f"{START}\n"
        f"const teams={teams_js},matches={matches_js};\n"
        f"const updatedAt={json.dumps(stamp)};\n"
        f"{END}"
    )
    new_html = re.sub(
        re.escape(START) + r".*?" + re.escape(END),
        lambda _: generated,
        html,
        flags=re.S,
    )
    if new_html == html:
        return False
    INDEX_PATH.write_text(new_html, encoding="utf-8")
    return True


def main() -> int:
    html = get_html()
    soup = BeautifulSoup(html, "html.parser")
    teams = parse_standings(soup)
    matches = parse_matches(soup)
    changed = replace_data(teams, matches)
    print(f"Parsed {len(teams)} teams and {len(matches)} completed matches.")
    print("index.html updated." if changed else "No data changes detected.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
