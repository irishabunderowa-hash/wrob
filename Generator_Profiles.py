# -*- coding: utf-8 -*-
"""
Generator_Profiles.py — Генератор квест-профилей WRobot (EasyQuestProfile)
для приватного сервера WoW 3.3.5a "Azuroth Core" (форк TrinityCore/AzerothCore).

Особенности БД Azuroth Core (учтены в скрипте):
  - В quest_template ОТСУТСТВУЮТ: RequiredSpellCast1, ProvidedItemCount,
    SourceItemId, MaxLevel, ChanceOrQuestChance, MinCountOrRef.
  - В creature ОТСУТСТВУЮТ name и zone (имя берётся из creature_template).
  - В loot-таблицах колонка шанса называется просто Chance.
  - Отрицательный RequiredNpcOrGo = gameobject, положительный = creature.
  - Тип квеста хранится в числовой колонке QuestType.
  - Квесты с предмета помечаются полем StartItem в quest_template.

Запуск (Python 3.12, pymysql):
    python Generator_Profiles.py <QUEST_ID> [<QUEST_ID> ...]
Результат: файл Profiles/<ID>_<ИмяКвеста>.xml

XML собирается СТРОГО построчно через lines.append() — без xml.etree/minidom,
чтобы не экранировались символы '&' в C-блоках (C# компилятор WRobot).
"""

import os
import re
import sys
import datetime

import pymysql

# ============================================================================
# НАСТРОЙКИ ПОДКЛЮЧЕНИЯ
# ============================================================================
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "world",
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Profiles")

RADIUS_FILTER = 500          # ярдов от квестодателя
RADIUS_SQL = RADIUS_FILTER ** 2   # 250000 — сравнение квадратов дистанций
HOTSPOT_LIMIT = 15           # максимум случайных точек

# Маппинг числового QuestType (Azuroth Core) -> класс WRobot
QUEST_TYPE_MAP = {
    0: None,               # обычный квест (только Pickup/Pulse/TurnIn)
    1: "KillAndLoot",
    2: "KillAndLoot",
    3: "Gatherer",
    4: "UseItemOn",
    5: "InteractWithNpc",
    6: "UseSpellOn",
}


# ============================================================================
# УТИЛИТЫ
# ============================================================================
def clean_name(name):
    """Имя для тега <Name>: пробелы -> '_', убрать XML-небезопасные символы."""
    if not name:
        return "Unknown"
    s = str(name).strip().replace("&", "-").replace('"', "'")
    s = re.sub(r"<[^>]*>", "", s)          # вырезать инлайн-коды цветов
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^\w\-]", "", s, flags=re.UNICODE)
    return s or "Unknown"


def name_class(name):
    """Имя для NameClass: строго без пробелов и спецсимволов."""
    if not name:
        return "Unknown"
    s = str(name).strip().replace("&", "-")
    s = re.sub(r"<[^>]*>", "", s)
    s = re.sub(r"[^\w]", "", s, flags=re.UNICODE)
    if s and s[0].isdigit():
        s = "Q_" + s
    return s or "Unknown"


def xml_escape(text):
    """Мягкое экранирование только критичных символов (без двойного экранирования &)."""
    if text is None:
        return ""
    s = str(text)
    s = s.replace("<", "").replace(">", "")
    s = s.replace("&", "&amp;")
    s = s.replace('"', "&quot;")
    return s


def fmt(v):
    """Число в строку XML без экспоненты."""
    try:
        return "{:.4f}".format(float(v))
    except (TypeError, ValueError):
        return "0.0000"


def to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ============================================================================
# ДОСТУП К БД (с защитой от отсутствующих колонок Azuroth Core)
# ============================================================================
class WorldDB:
    def __init__(self):
        self.conn = pymysql.connect(**DB_CONFIG)
        self._cols_cache = {}

    def columns(self, table):
        if table not in self._cols_cache:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                    (table,),
                )
                self._cols_cache[table] = {r["COLUMN_NAME"] for r in cur.fetchall()}
        return self._cols_cache[table]

    def has_col(self, table, col):
        return col in self.columns(table)

    def q(self, sql, args=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, args or ())
            return cur.fetchall()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


# ============================================================================
# ЗАГРУЗКА ДАННЫХ КВЕСТА
# ============================================================================
def load_quest(db, quest_id):
    cols = ["ID", "LogTitle", "QuestType", "SpecialFlags", "Repeatable",
            "RequiredNpcOrGo1", "RequiredNpcOrGo2", "RequiredNpcOrGo3",
            "RequiredNpcOrGo4", "RequiredNpcOrGo5",
            "RequiredNpcOrGoCount1", "RequiredNpcOrGoCount2",
            "RequiredNpcOrGoCount3", "RequiredNpcOrGoCount4",
            "RequiredNpcOrGoCount5",
            "RequiredItemId1", "RequiredItemId2", "RequiredItemId3",
            "RequiredItemId4", "RequiredItemId5",
            "RequiredItemCount1", "RequiredItemCount2", "RequiredItemCount3",
            "RequiredItemCount4", "RequiredItemCount5",
            "RewardChoiceItemID1", "RewardItem1"]
    # Необязательные/отсутствующие в Azuroth колонки добавляем только если они есть
    for opt in ("StartItem", "ObjectiveText1", "MinLevel", "MaxLevel",
                "QuestLevel", "ZoneOrGroup"):
        if db.has_col("quest_template", opt):
            cols.append(opt)
    sql = "SELECT {} FROM quest_template WHERE ID = %s".format(", ".join(cols))
    rows = db.q(sql, (quest_id,))
    return rows[0] if rows else None


def get_item_name(db, item_id):
    if not item_id or item_id <= 0:
        return None
    rows = db.q("SELECT name FROM item_template WHERE entry = %s", (item_id,))
    return rows[0]["name"] if rows else None


def find_npcs(db, quest_id, role):
    """role: 'starter' | 'ender'. Возвращает список NPC с координатами."""
    table = "creature_queststarter" if role == "starter" else "creature_quest_ender"
    if db.has_col("creature", "map"):
        map_expr = "c.map AS map_id"
    elif db.has_col("creature", "map_id"):
        map_expr = "c.map_id AS map_id"
    else:
        map_expr = "0 AS map_id"
    sql = (
        "SELECT ct.entry AS id, ct.name AS name, "
        "c.position_x AS x, c.position_y AS y, c.position_z AS z, " + map_expr +
        " FROM {tbl} qs "
        "JOIN creature_template ct ON ct.entry = qs.id "
        "JOIN creature c ON c.id = (SELECT MIN(c2.id) FROM creature c2 "
        "                           WHERE c2.{eid} = ct.entry) "
        "WHERE qs.Quest = %s ORDER BY ct.entry LIMIT 10"
    ).format(tbl=table, eid="id1" if db.has_col("creature", "id1") else "id")
    try:
        return db.q(sql, (quest_id,))
    except pymysql.err.ProgrammingError:
        # fallback: без привязки к creature (NPC без спавна)
        sql2 = (
            "SELECT ct.entry AS id, ct.name AS name, "
            "ct.phase_use_phase AS phase, 0 AS x, 0 AS y, 0 AS z, 0 AS map_id "
            "FROM {tbl} qs JOIN creature_template ct ON ct.entry = qs.id "
            "WHERE qs.Quest = %s LIMIT 10"
        ).format(tbl=table)
        return db.q(sql2, (quest_id,))


def find_start_gameobjects(db, quest_id):
    """GO-стартеры (gameobject_queststarter), если таблица существует."""
    if not db.has_col("gameobject_queststarter", "Quest"):
        return []
    map_field = "go.map AS map_id" if db.has_col("gameobject", "map") else \
        ("go.map_id AS map_id" if db.has_col("gameobject", "map_id") else "0 AS map_id")
    sql = (
        "SELECT gt.entry AS id, gt.name AS name, "
        "go.position_x AS x, go.position_y AS y, go.position_z AS z, " + map_field +
        " FROM gameobject_queststarter gs "
        "JOIN gameobject_template gt ON gt.entry = gs.id "
        "JOIN gameobject go ON go.id = gt.entry "
        "WHERE gs.Quest = %s ORDER BY RAND() LIMIT 5"
    )
    try:
        return db.q(sql, (quest_id,))
    except pymysql.err.MySQLError:
        return []


# ============================================================================
# ПОИСК ЦЕЛЕЙ: МОБЫ (дропперы предметов / RequiredNpcOrGo > 0)
# ============================================================================
def mobs_dropping_item(db, item_id):
    """Entry мобов, с которых дропается предмет (Chance > 0)."""
    if not item_id or item_id <= 0:
        return []
    if not db.has_col("creature_loot_template", "Chance"):
        return []
    # В AzerothCore Reference = идентификатор лут-таблицы (= entry моба или group)
    sql = ("SELECT DISTINCT Reference AS entry FROM creature_loot_template "
           "WHERE Item = %s AND Chance > 0")
    rows = db.q(sql, (item_id,))
    entries = []
    for r in rows:
        ref = to_int(r["entry"])
        if ref > 0:
            entries.append(ref)
    # Отфильтровать: оставить только реальные creature_template
    result = []
    for e in entries:
        if db.q("SELECT entry FROM creature_template WHERE entry = %s", (e,)):
            result.append(e)
    return result


def creature_spawns(db, entries, anchor):
    """Координаты спавнов мобов с фильтром по радиусу от anchor (x,y,map)."""
    if not entries:
        return []
    ph = ",".join(["%s"] * len(entries))
    map_field = "map" if db.has_col("creature", "map") else "map_id"
    dist = "(POW(position_x - %s, 2) + POW(position_y - %s, 2))"
    rows = []
    if anchor:
        ax, ay, amap = anchor
        sql = ("SELECT entry, position_x x, position_y y, position_z z, {mf} m "
               "FROM creature WHERE entry IN ({ph}) AND {mf} = %s "
               "AND {d} < {lim} ORDER BY RAND() LIMIT {n}").format(
                   mf=map_field, ph=ph, d=dist, lim=RADIUS_SQL, n=HOTSPOT_LIMIT)
        rows = db.q(sql, list(entries) + [ax, ay, amap])
    if not rows:  # фоллбэк: вся карта (или все карты, если якоря нет)
        base = ("SELECT entry, position_x x, position_y y, position_z z, {mf} m "
                "FROM creature WHERE entry IN ({ph}) ORDER BY RAND() LIMIT {n}").format(
                    mf=map_field, ph=ph, n=HOTSPOT_LIMIT)
        if anchor:
            base += " AND " + map_field + " = %s"
            rows = db.q(base, list(entries) + [anchor[2]])
        else:
            rows = db.q(base, entries)
    return [{"x": r["x"], "y": r["y"], "z": r["z"], "map": to_int(r["m"])} for r in rows]


# ============================================================================
# ПОИСК ЦЕЛЕЙ: GAMEOBJECTS (RequiredNpcOrGo < 0 / сбор предметов с GO)
# ============================================================================
def go_column(db):
    """Имя колонки ссылки на entry в gameobject (Azuroth: id; классика: entry)."""
    if db.has_col("gameobject", "id"):
        return "id"
    if db.has_col("gameobject", "entry"):
        return "entry"
    return None


def find_go_by_item(db, item_name):
    """GO, собирающие предмет: поиск gameobject_template по имени LIKE '%Имя%'."""
    if not item_name:
        return []
    rows = db.q(
        "SELECT entry, name FROM gameobject_template "
        "WHERE type = 4 AND name LIKE %s LIMIT 10",
        ("%{}%".format(item_name),),
    )
    return rows


def go_spawns_for_entries(db, entries, anchor):
    if not entries:
        return []
    goc = go_column(db)
    if not goc:
        return []
    ph = ",".join(["%s"] * len(entries))
    map_field = "map" if db.has_col("gameobject", "map") else "map_id"
    dist = "(POW(position_x - %s, 2) + POW(position_y - %s, 2))"
    sel = ("SELECT {goc} entry, position_x x, position_y y, position_z z, {mf} m "
           "FROM gameobject WHERE {goc} IN ({ph})").format(goc=goc, ph=ph, mf=map_field)
    rows = []
    if anchor:
        ax, ay, amap = anchor
        sql = sel + " AND {mf} = %s AND {d} < {lim} ORDER BY RAND() LIMIT {n}".format(
            mf=map_field, d=dist, lim=RADIUS_SQL, n=HOTSPOT_LIMIT)
        rows = db.q(sql, list(entries) + [ax, ay, amap])
    if not rows:
        if anchor:
            sql = sel + " AND " + map_field + " = %s ORDER BY RAND() LIMIT %s"
            rows = db.q(sql, list(entries) + [amap, HOTSPOT_LIMIT])
        else:
            sql = sel + " ORDER BY RAND() LIMIT %s"
            rows = db.q(sql, list(entries) + [HOTSPOT_LIMIT])
    return [{"x": r["x"], "y": r["y"], "z": r["z"], "map": to_int(r["m"]),
             "entry": to_int(r["entry"])} for r in rows]


# ============================================================================
# ОПРЕДЕЛЕНИЕ ТИПА И ЦЕЛЕЙ КВЕСТА
# ============================================================================
def resolve_objectives(db, quest):
    """
    Возвращает dict:
      forced_type, target_entries (list), target_kind ('mob'|'go'),
      hotspots (list of dicts), item_id (для UseItemOn), gather_item_name
    """
    res = {
        "forced_type": None,
        "target_entries": [],
        "target_kind": None,
        "hotspots": [],
        "item_id": 0,
        "item_name": None,
    }

    npc_targets, go_targets = [], []
    for i in range(1, 6):
        v = to_int(quest.get("RequiredNpcOrGo{}".format(i)))
        if v > 0:
            npc_targets.append(v)
        elif v < 0:
            go_targets.append(-v)

    req_items = [to_int(quest.get("RequiredItemId{}".format(i))) for i in range(1, 6)]
    req_items = [x for x in req_items if x > 0]

    # Явные цели RequiredNpcOrGo
    if npc_targets:
        res["target_entries"] = npc_targets
        res["target_kind"] = "mob"
    elif go_targets:
        res["target_entries"] = go_targets
        res["target_kind"] = "go"

    # Принудительные правила
    first_npc = npc_targets[0] if npc_targets else 0
    first_item = req_items[0] if req_items else 0

    if first_npc > 0 and first_item > 0:
        # Правило 1: использовать предмет на NPC
        res["forced_type"] = "UseItemOn"
        res["item_id"] = first_item
        res["target_entries"] = [first_npc]
        res["target_kind"] = "mob"
    elif not res["target_entries"] and first_item > 0:
        item_name = get_item_name(db, first_item)
        res["item_name"] = item_name
        # Правило 2: предмет собирается с gameobject -> Gatherer
        go_rows = find_go_by_item(db, item_name) if item_name else []
        if go_rows:
            res["forced_type"] = "Gatherer"
            res["target_kind"] = "go"
            res["target_entries"] = [to_int(r["entry"]) for r in go_rows]
        else:
            # Иначе — мобы-дропперы
            droppers = mobs_dropping_item(db, first_item)
            if droppers:
                res["target_kind"] = "mob"
                res["target_entries"] = droppers
                if to_int(quest.get("QuestType")) in (0,):
                    res["forced_type"] = "KillAndLoot"

    return res


def determine_quest_type(quest, objectives):
    if objectives["forced_type"]:
        return objectives["forced_type"]
    qt = to_int(quest.get("QuestType"))
    wtype = QUEST_TYPE_MAP.get(qt, "KillAndLoot")
    # Корректировка: KillAndLoot без целей мобов, но с целями-GO -> Gatherer
    if wtype == "KillAndLoot" and objectives["target_kind"] == "go":
        wtype = "Gatherer"
    if wtype in ("Gatherer",) and objectives["target_kind"] == "mob":
        wtype = "KillAndLoot"
    return wtype


# ============================================================================
# СБОРКА XML (построчно, lines.append)
# ============================================================================
CLASS_XSI = {
    "KillAndLoot": "KillAndLootEasyQuestClass",
    "Gatherer": "GathererEasyQuestClass",
    "UseItemOn": "UseItemOnEasyQuestClass",
    "InteractWithNpc": "InteractWithNpcEasyQuestClass",
    "UseSpellOn": "UseSpellOnEasyQuestClass",
    None: "EasyQuestClass",
}

TARGET_TAG = {
    "KillAndLoot": "EntryTarget",
    "Gatherer": "EntryIdObjects",
    "UseItemOn": "EntryIdTarget",
    "InteractWithNpc": "EntryIdTarget",
    "UseSpellOn": "EntryIdTarget",
}


def build_hotspots_lines(objectives):
    lines = ["<HotSpots>"]
    for hs in objectives["hotspots"][:HOTSPOT_LIMIT]:
        lines.append('<Vector3 X="{x}" Y="{y}" Z="{z}"/>'.format(
            x=fmt(hs["x"]), y=fmt(hs["y"]), z=fmt(hs["z"])))
    if not objectives["hotspots"]:
        lines.append('<Vector3 X="0" Y="0" Z="0"/>')
    lines.append("</HotSpots>")
    return lines


def build_easyquest(quest, wtype, objectives):
    qid = to_int(quest["ID"])
    qname = clean_name(quest.get("LogTitle"))
    counts = [max(1, to_int(quest.get("RequiredNpcOrGoCount{}".format(i))))
              if to_int(quest.get("RequiredNpcOrGo{}".format(i))) != 0 else 0
              for i in range(1, 6)]
    # Для предметов-целей считаем количество из RequiredItemCount
    for i in range(1, 6):
        if counts[i - 1] == 0 and to_int(quest.get("RequiredItemId{}".format(i))) > 0:
            counts[i - 1] = max(1, to_int(quest.get("RequiredItemCount{}".format(i))))

    lines = []
    lines.append("<EasyQuest>")
    lines.append("<Name>{}</Name>".format(xml_escape(qname)))
    lines.append("<QuestId><int>{}</int></QuestId>".format(qid))
    lines.append("<QuestType>{}</QuestType>".format(wtype if wtype else "None"))
    xsi = CLASS_XSI.get(wtype, "EasyQuestClass")
    lines.append('<QuestClass xsi:type="{}">'.format(xsi))

    if wtype:
        lines.extend(build_hotspots_lines(objectives))
        tag = TARGET_TAG[wtype]
        lines.append("<{}>".format(tag))
        for e in objectives["target_entries"]:
            lines.append("<int>{}</int>".format(e))
        lines.append("</{}>".format(tag))
        if wtype == "UseItemOn" and objectives["item_id"]:
            lines.append("<ItemId>{}</ItemId>".format(objectives["item_id"]))
    lines.append("<IsGrinderNotQuest>false</IsGrinderNotQuest>")
    lines.append("</QuestClass>")

    # Порядок: сначала ObjectiveCount1..5, затем AutoDetectObjectiveCount1..5
    for i in range(1, 6):
        lines.append("<ObjectiveCount{i}>{v}</ObjectiveCount{i}>".format(i=i, v=counts[i - 1]))
    for i in range(1, 6):
        ad = "true" if counts[i - 1] > 0 else "false"
        lines.append("<AutoDetectObjectiveCount{i}>{v}</AutoDetectObjectiveCount{i}>".format(i=i, v=ad))

    lines.append("<CanCondition/>")
    lines.append("<IsCompleteCondition>return Quest.IsObjectiveComplete(1, {});</IsCompleteCondition>".format(qid))
    repeatable = "true" if to_int(quest.get("Repeatable")) == 1 else "false"
    lines.append("<RepeatableQuest>{}</RepeatableQuest>".format(repeatable))
    lines.append("<NotRequiredInQuestLog>false</NotRequiredInQuestLog>")

    start_item = to_int(quest.get("StartItem", 0))
    if start_item > 0:
        lines.append("<PickUpQuestOnItem>true</PickUpQuestOnItem>")
        lines.append("<PickUpQuestOnItemID>{}</PickUpQuestOnItemID>".format(start_item))
    else:
        lines.append("<PickUpQuestOnItem>false</PickUpQuestOnItem>")
        lines.append("<PickUpQuestOnItemID>0</PickUpQuestOnItemID>")

    lines.append("<Comment/>")
    lines.append("<GossipOptionRewardItem>1</GossipOptionRewardItem>")
    lines.append("<RequiredQuest>0</RequiredQuest>")
    max_lvl = to_int(quest.get("MaxLevel", 0)) if quest.get("MaxLevel") is not None else 0
    lines.append("<MaxLevel>{}</MaxLevel>".format(max_lvl if max_lvl > 0 else 100))
    min_lvl = to_int(quest.get("MinLevel", 0))
    lines.append("<MinLevel>{}</MinLevel>".format(min_lvl))
    lines.append("<WoWClass>None</WoWClass>")
    lines.append("</EasyQuest>")
    return lines


def build_npcquest(id_, name, x, y, z, pickup_ids, turnin_ids, gameobject=False):
    lines = []
    lines.append('<NPCQuest Id="{}" Name="{}" GameObject="{}">'.format(
        id_, xml_escape(str(name)), "true" if gameobject else "false"))
    if pickup_ids:
        inner = "".join("<int>{}</int>".format(q) for q in sorted(set(pickup_ids)))
        lines.append("<PickUpQuests>{}</PickUpQuests>".format(inner))
    else:
        lines.append("<PickUpQuests/>")
    if turnin_ids:
        inner = "".join("<int>{}</int>".format(q) for q in sorted(set(turnin_ids)))
        lines.append("<TurnInQuests>{}</TurnInQuests>".format(inner))
    else:
        lines.append("<TurnInQuests/>")
    lines.append('<Position X="{}" Y="{}" Z="{}" Type="Flying"/>'.format(fmt(x), fmt(y), fmt(z)))
    lines.append("</NPCQuest>")
    return lines


SCRIPT_BLOCK = [
    "<Script>",
    "public class MyCustomScript",
    "{",
    "    static MyCustomScript()",
    "    {",
    "        // Safe code here",
    "    }",
    "}",
    "</Script>",
]


def generate_profile(db, quest_id):
    """Генерация профиля по ID квеста."""
    quest = load_quest(db, quest_id)
    if not quest:
        print("[!] Квест {} не найден в quest_template.".format(quest_id))
        return None

    qid = to_int(quest["ID"])
    title = quest.get("LogTitle") or "Quest_{}".format(qid)
    nc = name_class(title)
    objectives = resolve_objectives(db, quest)
    wtype = determine_quest_type(quest, objectives)

    # Стартовые NPC (для PickUp) и сдаточные (TurnIn)
    starters = find_npcs(db, qid, "starter") if db.has_col("creature_queststarter", "Quest") else []
    enders = find_npcs(db, qid, "ender") if db.has_col("creature_quest_ender", "Quest") else []
    start_gos = find_start_gameobjects(db, qid)

    start_item = to_int(quest.get("StartItem", 0))

    # Якорь для фильтрации хотспотов: первый стартер, иначе первый эндер
    anchor = None
    if starters:
        anchor = (float(starters[0]["x"]), float(starters[0]["y"]), to_int(starters[0]["map_id"]))
    elif enders:
        anchor = (float(enders[0]["x"]), float(enders[0]["y"]), to_int(enders[0]["map_id"]))

    # Хотспоты целей
    if objectives["target_entries"]:
        if objectives["target_kind"] == "go":
            objectives["hotspots"] = go_spawns_for_entries(db, objectives["target_entries"], anchor)
        else:
            objectives["hotspots"] = creature_spawns(db, objectives["target_entries"], anchor)

    # ---------- Сборка файла ----------
    lines = []
    lines.append('<?xml version="1.0" encoding="utf-8"?>')
    lines.append('<EasyQuestProfile xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                 'xmlns:xsd="http://www.w3.org/2001/XMLSchema">')

    # QuestsSorted
    lines.append("<QuestsSorted>")
    if wtype is None:
        lines.append('<QuestsSorted Action="PickUp" NameClass="{}"/>'.format(nc))
        lines.append('<QuestsSorted Action="Pulse" NameClass="{}"/>'.format(nc))
        lines.append('<QuestsSorted Action="TurnIn" NameClass="{}"/>'.format(nc))
    else:
        lines.append('<QuestsSorted Action="PulseAllInOne" NameClass="{}"/>'.format(nc))
    lines.append("</QuestsSorted>")

    # NpcQuest — стартер НЕ добавляем, если квест стартует с предмета (StartItem)
    lines.append("<NpcQuest>")
    added = False
    if start_item == 0:
        for s in starters:
            lines.extend(build_npcquest(to_int(s["id"]), s["name"], s["x"], s["y"], s["z"],
                                        [qid], [], gameobject=False))
            added = True
        if not added:
            for g in start_gos:
                lines.extend(build_npcquest(to_int(g["id"]), g["name"], g["x"], g["y"], g["z"],
                                            [qid], [], gameobject=True))
                added = True
    for e in enders:
        lines.extend(build_npcquest(to_int(e["id"]), e["name"], e["x"], e["y"], e["z"],
                                    [] if start_item else [], [qid], gameobject=False))
    lines.append("</NpcQuest>")

    lines.append("<Npc/>")
    lines.append("<Blackspots/>")
    lines.append("<BlackGuids/>")

    # EasyQuests
    lines.append("<EasyQuests>")
    lines.extend(build_easyquest(quest, wtype, objectives))
    lines.append("</EasyQuests>")

    # Script (многострочный C#, одинарные backslash недопустимы — их нет)
    lines.extend(SCRIPT_BLOCK)
    lines.append("<OffMeshConnections/>")
    lines.append("</EasyQuestProfile>")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "{}_{}.xml".format(qid, clean_name(title)))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("[OK] Квест {}: '{}' -> тип={}, цели={} ({}), хотспотов={}, файл={}".format(
        qid, title, wtype or "None", objectives["target_entries"],
        objectives["target_kind"] or "-", len(objectives["hotspots"]), out_path))
    return out_path


def main():
    ids = []
    for a in sys.argv[1:]:
        if a.isdigit():
            ids.append(int(a))
    if not ids:
        try:
            raw = input("Введите ID квеста (через пробел): ")
            ids = [int(x) for x in raw.split() if x.isdigit()]
        except (EOFError, KeyboardInterrupt):
            pass
    if not ids:
        print("Использование: python Generator_Profiles.py <QUEST_ID> [...]")
        sys.exit(1)

    db = WorldDB()
    try:
        for qid in ids:
            try:
                generate_profile(db, qid)
            except Exception as ex:
                print("[ERROR] Квест {}: {}".format(qid, ex))
    finally:
        db.close()


if __name__ == "__main__":
    main()
