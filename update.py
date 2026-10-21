#!/usr/bin/env python3
"""Mirror the public Latin Soccer SD Intermedia page into the GitHub Pages site.

Updates:
- League notices/rules
- Apertura standings
- Upcoming fixtures/calendar
- Pending games without posted scores
- Completed results

Only the generated data block inside index.html is changed, so the site's design and
interactivity remain untouched.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://latinsoccersd.com/intermedia/"
INDEX_PATH = Path(__file__).with_name("index.html")
START = "/* AUTO_DATA_START */"
END = "/* AUTO_DATA_END */"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-US,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def get_html() -> str:
    last_error = None
    for attempt in range(3):
        try:
            r = requests.get(SOURCE_URL, headers=HEADERS, timeout=30)
            r.raise_for_status()
            if len(r.text) < 5000:
                raise RuntimeError(f"Source page looks unexpectedly short ({len(r.text)} bytes)")
            return r.text
        except Exception as exc:
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
            rows.append((tr, [clean(c.get_text(" ", strip=True)) for c in cells]))
    return rows


def parse_date(cell: str) -> str | None:
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", cell)
    if m:
        return m.group(1)
    low = cell.lower()
    for name, month in MONTHS.items():
        m = re.search(rf"{name}\s+(\d{{1,2}}),?\s+(20\d{{2}})", low)
        if m:
            return f"{int(m.group(2)):04d}-{month:02d}-{int(m.group(1)):02d}"
    return None


def parse_time(text: str) -> str:
    t = clean(text)
    # Prefer a conventional visible time such as 9:30 am over hidden timestamp fragments.
    matches = re.findall(r"\b(\d{1,2}:\d{2})\s*(am|pm)\b", t, re.I)
    if matches:
        hhmm, ap = matches[-1]
        return f"{hhmm} {ap.lower()}"
    return t


def split_matchup(text: str):
    parts = re.split(r"\s+vs\s+", clean(text), maxsplit=1, flags=re.I)
    return parts if len(parts) == 2 else None


def row_link(tr) -> str:
    a = tr.find("a", href=True)
    return urljoin(SOURCE_URL, a["href"]) if a else ""


def parse_notices(soup: BeautifulSoup) -> list[str]:
    heading = heading_with_text(soup, r"TORNEO\s+APERTURA")
    if heading is None:
        return []
    notices = []
    for node in heading.find_all_previous(["p", "div", "h2", "h3", "h4", "h5", "h6"], limit=20):
        txt = clean(node.get_text(" ", strip=True))
        if not txt or "INTERMEDIA" == txt.upper() or "TORNEO APERTURA" in txt.upper():
            continue
        up = txt.upper()
        if any(k in up for k in ("REGISTRO", "UNIFORME", "REPORTE", "JUGADOR", "PROTESTA")):
            notices.append(txt)
    notices.reverse()
    # dedupe while preserving order
    out=[]
    for n in notices:
        if n not in out:
            out.append(n)
    return out[:8]


def parse_standings(soup: BeautifulSoup) -> list[list[object]]:
    heading = heading_with_text(soup, r"TORNEO\s+APERTURA")
    table = heading.find_next("table") if heading else None
    if table is None:
        for candidate in soup.find_all("table"):
            txt = clean(candidate.get_text(" ", strip=True)).upper()
            if all(x in txt for x in ("EQUIPO", "PJ", "PG", "PE", "PP", "PTS")):
                table = candidate
                break
    if table is None:
        raise RuntimeError("Could not find standings table")
    teams=[]
    for _, cells in table_rows(table):
        if len(cells) < 10 or not re.fullmatch(r"\d+", cells[0]):
            continue
        try:
            teams.append([cells[1]] + [int(x) for x in cells[2:10]])
        except ValueError:
            pass
    if len(teams) < 10:
        raise RuntimeError(f"Standings parse returned only {len(teams)} teams")
    return teams


def parse_fixture_table(table, source_label: str) -> tuple[list[dict], list[dict]]:
    fixtures, results = [], []
    if table is None:
        return fixtures, results
    seen=set()
    for tr, cells in table_rows(table):
        if len(cells) < 5:
            continue
        date=parse_date(cells[0])
        parts=split_matchup(cells[1])
        if not date or not parts:
            continue
        home, away = map(clean, parts)
        score = re.search(r"(?<!\d)(\d{1,2})\s*-\s*(\d{1,2})(?!\d)", cells[2])
        common={
            "date":date, "home":home, "away":away, "field":clean(cells[3]),
            "round":clean(cells[4]), "link":row_link(tr), "source":source_label,
        }
        if score:
            hs,ascore=map(int,score.groups())
            item={**common,"hs":hs,"ascore":ascore}
            key=(date,home,away,hs,ascore,common["field"],common["round"])
            if key not in seen:
                results.append(item); seen.add(key)
        else:
            raw=clean(cells[2])
            item={**common,"time":parse_time(raw),"status":"Pendiente" if "pendiente" in (common["field"]+" "+common["round"]).lower() else "Programado"}
            key=(date,home,away,item["time"],common["field"],common["round"])
            if key not in seen:
                fixtures.append(item); seen.add(key)
    return fixtures, results


def find_fixture_table_after(heading):
    if heading is None:
        return None
    nxt=heading.find_next("table")
    return nxt


def parse_all_games(soup: BeautifulSoup):
    upcoming_heading=heading_with_text(soup,r"PROXIMOS\s+JUEGOS")
    results_heading=heading_with_text(soup,r"^RESULTADOS$")
    upcoming_table=find_fixture_table_after(upcoming_heading)
    results_table=find_fixture_table_after(results_heading)

    fixtures1, results1 = parse_fixture_table(upcoming_table,"Próximos juegos")
    fixtures2, results2 = parse_fixture_table(results_table,"Resultados")

    # Some entries without a score are temporarily listed inside RESULTADOS on the source.
    # Keep them visible as scheduled/pending so the mirror never hides information.
    fixtures=[]; results=[]; seenf=set(); seenr=set()
    for item in fixtures1+fixtures2:
        key=(item["date"],item["home"],item["away"],item.get("time",""),item["field"],item["round"])
        if key not in seenf:
            fixtures.append(item); seenf.add(key)
    for item in results1+results2:
        key=(item["date"],item["home"],item["away"],item["hs"],item["ascore"],item["field"],item["round"])
        if key not in seenr:
            results.append(item); seenr.add(key)
    fixtures.sort(key=lambda m:(m["date"],m.get("time","")))
    results.sort(key=lambda m:m["date"], reverse=True)
    if len(results) < 20:
        raise RuntimeError(f"Results parse returned only {len(results)} completed matches")
    return fixtures, results


def replace_data(notices, teams, fixtures, matches) -> bool:
    html=INDEX_PATH.read_text(encoding="utf-8")
    if START not in html or END not in html:
        raise RuntimeError("index.html is missing AUTO_DATA markers. Upload the supplied index.html first.")
    stamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    generated=(
        f"{START}\n"
        f"const notices={json.dumps(notices,ensure_ascii=False,separators=(',',':'))};\n"
        f"const teams={json.dumps(teams,ensure_ascii=False,separators=(',',':'))};\n"
        f"const fixtures={json.dumps(fixtures,ensure_ascii=False,separators=(',',':'))};\n"
        f"const matches={json.dumps(matches,ensure_ascii=False,separators=(',',':'))};\n"
        f"const updatedAt={json.dumps(stamp)};\n"
        f"{END}"
    )
    new_html=re.sub(re.escape(START)+r".*?"+re.escape(END),lambda _:generated,html,flags=re.S)
    changed=new_html!=html
    if changed:
        INDEX_PATH.write_text(new_html,encoding="utf-8")
    return changed


def main() -> int:
    html=get_html(); soup=BeautifulSoup(html,"html.parser")
    notices=parse_notices(soup)
    teams=parse_standings(soup)
    fixtures,matches=parse_all_games(soup)
    changed=replace_data(notices,teams,fixtures,matches)
    print(f"Parsed {len(teams)} teams, {len(fixtures)} scheduled/pending games, {len(matches)} completed matches, and {len(notices)} notices.")
    print("index.html updated." if changed else "No data changes detected.")
    return 0

if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}",file=sys.stderr)
        raise SystemExit(1)
