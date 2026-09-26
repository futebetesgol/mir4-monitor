import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://forum.mir4global.com"

URLS = {
    "hidden_valley": f"{BASE}/war/hiddenvalley",
    "castle": f"{BASE}/war/castlesiege",
    "sabuk": f"{BASE}/war/sabuksiege",
    "power": f"{BASE}/rank?ranktype=1",
    "clan": f"{BASE}/rank?ranktype=4",
}

# Classes que aparecem atualmente no ranking oficial do MIR4.
CLASSES = {
    "warrior": 1,
    "sorcerer": 2,
    "taoist": 3,
    "arbalist": 4,
    "lancer": 5,
    "darkist": 6,
    "lionheart": 7,
}

OUT = Path("data.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

session = requests.Session()
session.headers.update(HEADERS)


def clean(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def get_soup(url):
    print(f"Baixando: {url}")
    response = session.get(url, timeout=30)
    response.raise_for_status()

    if not response.text.strip():
        raise RuntimeError(f"Resposta vazia: {url}")

    return BeautifulSoup(response.text, "html.parser")


def server_region(server):
    s = server.upper()

    if s.startswith("BASIA") or s.startswith("ASIA"):
        return "ASIA"
    if s.startswith("INMENA"):
        return "INMENA"
    if s.startswith("EU"):
        return "EU"
    if s.startswith("SA"):
        return "SA"
    if s.startswith("NA"):
        return "NA"

    return "OTHER"


def looks_like_server(value):
    value = clean(value).upper()

    patterns = [
        r"^ASIA\d+$",
        r"^BASIA\d+$",
        r"^INMENA\d+$",
        r"^EU\d+$",
        r"^SA\d+$",
        r"^NA\d+$",
    ]

    return any(re.match(pattern, value) for pattern in patterns)


def all_tables(soup):
    result = []

    for table in soup.find_all("table"):
        rows = []

        for tr in table.find_all("tr"):
            cells = [
                clean(cell.get_text(" ", strip=True))
                for cell in tr.find_all(["th", "td"])
            ]

            if cells:
                rows.append(cells)

        if rows:
            result.append(rows)

    return result


def extract_servers_from_page(soup):
    servers = set()

    for text in soup.stripped_strings:
        for match in re.findall(
            r"\b(?:BASIA|ASIA|INMENA|EU|SA|NA)\d{3,}\b",
            clean(text),
            flags=re.I,
        ):
            if looks_like_server(match):
                servers.add(match.upper())

    return sorted(servers)


def parse_hidden_valley():
    soup = get_soup(URLS["hidden_valley"])
    records = []

    for table in all_tables(soup):
        for row in table:
            if len(row) < 4:
                continue

            if not looks_like_server(row[0]):
                continue

            records.append({
                "server": row[0].upper(),
                "region": server_region(row[0]),
                "bicheon_valley": row[1],
                "snake_valley": row[2],
                "redmoon_valley": row[3],
            })

    # Fallback para alterações pequenas no HTML.
    if not records:
        lines = [clean(x) for x in soup.stripped_strings if clean(x)]

        for i, value in enumerate(lines):
            if looks_like_server(value) and i + 3 < len(lines):
                records.append({
                    "server": value.upper(),
                    "region": server_region(value),
                    "bicheon_valley": lines[i + 1],
                    "snake_valley": lines[i + 2],
                    "redmoon_valley": lines[i + 3],
                })

    # Remove duplicados.
    unique = {}
    for item in records:
        unique[item["server"]] = item

    return list(unique.values()), extract_servers_from_page(soup)


def parse_ranking(url, clan=False):
    soup = get_soup(url)
    ranking = []

    for table in all_tables(soup):
        for row in table:
            if clan:
                # Ranking | Clan | Clan Leader | Clan Power score
                if len(row) < 4:
                    continue

                rank = re.sub(r"\D.*$", "", row[0]).strip()

                if not rank.isdigit():
                    continue

                power = re.sub(r"[^\d]", "", row[3])

                ranking.append({
                    "rank": int(rank),
                    "clan": row[1],
                    "leader": row[2],
                    "power": int(power) if power else 0,
                })

            else:
                # Ranking | Character | Server | Clan | Power Score
                if len(row) < 5:
                    continue

                rank_match = re.match(r"^\s*(\d+)", row[0])

                if not rank_match:
                    continue

                if not looks_like_server(row[2]):
                    continue

                power = re.sub(r"[^\d]", "", row[4])

                ranking.append({
                    "rank": int(rank_match.group(1)),
                    "character": row[1],
                    "server": row[2].upper(),
                    "region": server_region(row[2]),
                    "clan": row[3],
                    "power": int(power) if power else 0,
                })

    # Remove duplicados pela posição + entidade.
    unique = {}

    for item in ranking:
        if clan:
            key = (item["rank"], item["clan"])
        else:
            key = (item["rank"], item["character"], item["server"])

        unique[key] = item

    return sorted(unique.values(), key=lambda x: x["rank"])


def parse_castle():
    soup = get_soup(URLS["castle"])

    # A página de Castelo não necessariamente usa uma tabela HTML simples.
    # Guardamos o texto estruturado e os servidores detectados para que
    # nenhuma informação falsa seja criada caso o layout oficial mude.
    text = [clean(x) for x in soup.stripped_strings if clean(x)]

    return {
        "servers": extract_servers_from_page(soup),
        "official_text": text,
    }


def parse_sabuk():
    soup = get_soup(URLS["sabuk"])

    text = [clean(x) for x in soup.stripped_strings if clean(x)]

    rounds = []
    seen = set()

    for value in text:
        match = re.search(
            r"Round\s+(\d+)\s*\((\d{4}/\d{2}/\d{2})\)",
            value,
            flags=re.I,
        )

        if match:
            key = (match.group(1), match.group(2))

            if key not in seen:
                seen.add(key)
                rounds.append({
                    "round": int(match.group(1)),
                    "date": match.group(2),
                })

    return {
        "rounds": rounds,
        "servers": extract_servers_from_page(soup),
        "official_text": text,
    }


def load_previous():
    if not OUT.exists():
        return None

    try:
        with OUT.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main():
    previous = load_previous()

    data = {
        "source": "MIR4 Official Community",
        "source_url": BASE,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "servers": [],
        "hidden_valley": [],
        "bicheon_castle": {},
        "sabuk": {},
        "rankings": {
            "power": [],
            "classes": {},
            "clans": [],
        },
        "errors": [],
    }

    server_set = set()

    # Hidden Valley
    try:
        valleys, servers = parse_hidden_valley()
        data["hidden_valley"] = valleys

        for server in servers:
            server_set.add(server)

        for item in valleys:
            server_set.add(item["server"])

        print(f"Hidden Valley: {len(valleys)} servidores.")
    except Exception as e:
        data["errors"].append(f"Hidden Valley: {e}")

        if previous:
            data["hidden_valley"] = previous.get("hidden_valley", [])

    # Power ranking
    try:
        power = parse_ranking(URLS["power"])
        data["rankings"]["power"] = power

        for player in power:
            server_set.add(player["server"])

        print(f"Power Ranking: {len(power)} jogadores.")
    except Exception as e:
        data["errors"].append(f"Power Ranking: {e}")

        if previous:
            data["rankings"]["power"] = (
                previous.get("rankings", {}).get("power", [])
            )

    # Ranking por classe
    for class_name, class_id in CLASSES.items():
        try:
            url = (
                f"{BASE}/rank?"
                f"classtype={class_id}&ranktype=1000"
            )

            ranking = parse_ranking(url)
            data["rankings"]["classes"][class_name] = ranking

            for player in ranking:
                server_set.add(player["server"])

            print(
                f"Classe {class_name}: "
                f"{len(ranking)} jogadores."
            )

        except Exception as e:
            data["errors"].append(
                f"Classe {class_name}: {e}"
            )

            if previous:
                old_classes = (
                    previous
                    .get("rankings", {})
                    .get("classes", {})
                )

                data["rankings"]["classes"][class_name] = (
                    old_classes.get(class_name, [])
                )

    # Ranking de clãs
    try:
        clans = parse_ranking(URLS["clan"], clan=True)
        data["rankings"]["clans"] = clans

        print(f"Clan Ranking: {len(clans)} clãs.")
    except Exception as e:
        data["errors"].append(f"Clan Ranking: {e}")

        if previous:
            data["rankings"]["clans"] = (
                previous.get("rankings", {}).get("clans", [])
            )

    # Bicheon Castle
    try:
        castle = parse_castle()
        data["bicheon_castle"] = castle

        for server in castle.get("servers", []):
            server_set.add(server)

        print(
            "Bicheon Castle: "
            f"{len(castle.get('servers', []))} servidores detectados."
        )
    except Exception as e:
        data["errors"].append(f"Bicheon Castle: {e}")

        if previous:
            data["bicheon_castle"] = previous.get(
                "bicheon_castle", {}
            )

    # Sabuk
    try:
        sabuk = parse_sabuk()
        data["sabuk"] = sabuk

        for server in sabuk.get("servers", []):
            server_set.add(server)

        print(
            "Sabuk: "
            f"{len(sabuk.get('rounds', []))} rodadas detectadas."
        )
    except Exception as e:
        data["errors"].append(f"Sabuk: {e}")

        if previous:
            data["sabuk"] = previous.get("sabuk", {})

    # Mantém servidores anteriores também.
    if previous:
        for item in previous.get("servers", []):
            if isinstance(item, dict):
                server = item.get("server", "")
            else:
                server = str(item)

            if looks_like_server(server):
                server_set.add(server.upper())

    data["servers"] = [
        {
            "server": server,
            "region": server_region(server),
        }
        for server in sorted(server_set)
    ]

    # Não substitui um arquivo bom por um arquivo completamente vazio.
    if (
        not data["hidden_valley"]
        and not data["rankings"]["power"]
        and not previous
    ):
        print("ERRO: nenhum dado principal foi coletado.")
        print("\n".join(data["errors"]))
        sys.exit(1)

    if data["errors"]:
        data["status"] = "partial"

    with OUT.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("")
    print("===================================")
    print("MIR4 Monitor atualizado.")
    print(f"Servidores detectados: {len(data['servers'])}")
    print(f"Status: {data['status']}")
    print(f"Erros: {len(data['errors'])}")
    print("Arquivo criado: data.json")
    print("===================================")

    if data["errors"]:
        for error in data["errors"]:
            print(f"AVISO: {error}")


if __name__ == "__main__":
    main()
