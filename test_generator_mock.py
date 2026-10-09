# -*- coding: utf-8 -*-
"""Тест логики генератора на mock-БД в стиле Azuroth Core (без MySQL)."""
import sys, types, re

# Подменяем pymysql до импорта генератора
fake = types.ModuleType("pymysql")
cursors = types.ModuleType("pymysql.cursors")
cursors.DictCursor = object
err = types.ModuleType("pymysql.err")
class ProgrammingError(Exception): pass
class MySQLError(Exception): pass
err.ProgrammingError = ProgrammingError
err.MySQLError = MySQLError
fake.cursors = cursors
fake.err = err
fake.connect = lambda **kw: None
sys.modules["pymysql"] = fake
sys.modules["pymysql.cursors"] = cursors
sys.modules["pymysql.err"] = err

import Generator_Profiles as G

QUESTS = {
    # Простой убийственный квест (QuestType=1), цель —RequiredNpcOrGo>0
    1: dict(ID=1, LogTitle="Kill & Loot: Rats!", QuestType=1, Repeatable=0, StartItem=0,
            RequiredNpcOrGo1=1128, RequiredNpcOrGoCount1=8,
            RequiredItemId1=0, MinLevel=1, MaxLevel=None),
    # UseItemOn force: NPC + предмет, QuestType=2 (в БД KillAndLoot)
    2: dict(ID=2, LogTitle="Speak with the Elder", QuestType=2, Repeatable=0, StartItem=0,
            RequiredNpcOrGo1=3456, RequiredNpcOrGoCount1=1,
            RequiredItemId1=2589, MinLevel=5, MaxLevel=None),
    # Gatherer force: предмет собирается с GO (поиск по имени)
    3: dict(ID=3, LogTitle="Herbs for the Witch", QuestType=0, Repeatable=0, StartItem=0,
            RequiredNpcOrGo1=0, RequiredNpcOrGoCount1=0,
            RequiredItemId1=765, MinLevel=1, MaxLevel=None),
    # Квест с предмета: StartItem > 0
    4: dict(ID=4, LogTitle="A Special Delivery", QuestType=0, Repeatable=0, StartItem=4488,
            RequiredNpcOrGo1=-35, RequiredNpcOrGoCount1=1,
            RequiredItemId1=0, MinLevel=10, MaxLevel=None),
}
ITEM_NAMES = {2589: "Witch's Draught", 765: "Moonleaf Shard"}
GO_BY_ITEM = {"Moonleaf Shard": [dict(entry=1617, name="Moonleaf Shard")]}.get
DROPPERS = {}  # для теста не используется

class MockDB:
    def has_col(self, table, col):
        schema = {
            "quest_template": {"ID","LogTitle","QuestType","SpecialFlags","Repeatable","StartItem",
                               "MinLevel","ObjectiveText1",
                               *["RequiredNpcOrGo%d"%i for i in range(1,6)],
                               *["RequiredNpcOrGoCount%d"%i for i in range(1,6)],
                               *["RequiredItemId%d"%i for i in range(1,6)],
                               *["RequiredItemCount%d"%i for i in range(1,6)]},
            "creature": {"id","map","position_x","position_y","position_z"},   # НЕТ name/zone!
            "gameobject": {"id","map","position_x","position_y","position_z"},
            "creature_loot_template": {"Reference","Item","Chance"},           # шанс = Chance
            "gameobject_template": {"entry","name","type"},
            "item_template": {"entry","name"},
            "creature_template": {"entry","name"},
            "creature_queststarter": {"id","Quest"},
            "creature_quest_ender": {"id","Quest"},
            "gameobject_queststarter": {"id","Quest"},
        }
        return col in schema.get(table, set())
    def q(self, sql, args=None):
        s = " ".join(sql.split())
        if "FROM quest_template" in s:
            return [QUESTS[args[0]]]
        if "item_template" in s:
            return [dict(name=ITEM_NAMES.get(args[0]))]
        if "gameobject_template" in s and "LIKE" in s:
            nm = args[0].strip("%")
            return GO_BY_ITEM(nm) or []
        if "creature_loot_template" in s:
            return [dict(entry=e) for e in DROPPERS.get(args[0], [])]
        if "SELECT entry FROM creature_template" in s:
            return [dict(entry=args[0])]
        if "creature_queststarter" in s:
            return [dict(id=3256, name="Marshal McGowan", x=-8821.0, y=604.0, z=85.0, map_id=0)]
        if "creature_quest_ender" in s:
            return [dict(id=3257, name="Chief Engineer", x=-8810.5, y=610.2, z=85.3, map_id=0)]
        if "gameobject_queststarter" in s:
            return []
        if "FROM creature WHERE entry IN" in s:
            return [dict(entry=args[0], x=-8700.0, y=650.0, z=80.0, m=0),
                    dict(entry=args[1] if len(args)>1 else args[0], x=-8750.0, y=620.0, z=81.0, m=0)]
        if "FROM gameobject WHERE id IN" in s:
            return [dict(entry=args[0], x=-8790.0, y=590.0, z=86.0, m=0)]
        return []
    def close(self): pass

db = MockDB()
G.WorldDB = lambda: db

ok = True
for qid, exp_type in [(1,"KillAndLoot"),(2,"UseItemOn"),(3,"Gatherer"),(4,None)]:
    path = G.generate_profile(db, qid)
    xml = open(path, encoding="utf-8").read()
    # Проверки
    assert xml.startswith('<?xml version="1.0" encoding="utf-8"?>'), "header"
    assert "<EasyQuestProfile" in xml and "</EasyQuestProfile>" in xml, "root"
    assert "&amp;" not in xml.split("<Script>")[0].replace("&amp;amp;",""), "no double escape"
    m = re.search(r"<QuestType>(.*?)</QuestType>", xml)
    got = m.group(1) if m.group(1) != "None" else None
    if got != exp_type:
        print("[FAIL] quest %d: expected %r, got %r" % (qid, exp_type, got)); ok = False
    else:
        print("[PASS] quest %d type=%s" % (qid, exp_type))
    # Порядок ObjectiveCount перед AutoDetect
    oc = xml.index("<ObjectiveCount1>"); ac = xml.index("<AutoDetectObjectiveCount1>")
    assert oc < ac, "order counts"
    # Теги целей
    if exp_type == "KillAndLoot": assert "<EntryTarget>" in xml
    if exp_type == "Gatherer": assert "<EntryIdObjects>" in xml
    if exp_type == "UseItemOn":
        assert "<EntryIdTarget>" in xml and "<ItemId>2589</ItemId>" in xml
    # StartItem
    if qid == 4:
        assert "<PickUpQuestOnItem>true</PickUpQuestOnItem>" in xml
        assert "<PickUpQuestOnItemID>4488</PickUpQuestOnItemID>" in xml
        assert "<PickUpQuests>" not in xml, "starter NPC must be absent for StartItem quest"
    else:
        assert "<PickUpQuestOnItem>false</PickUpQuestOnItem>" in xml
    # QuestsSorted
    if exp_type is None:
        assert 'Action="PickUp"' in xml and 'Action="Pulse"' in xml and 'Action="TurnIn"' in xml
        assert 'Action="PulseAllInOne"' not in xml
    else:
        assert 'Action="PulseAllInOne"' in xml
    assert 'Action= "' not in xml and '" PickUp' not in xml, "no stray spaces"
    # Script блок
    assert "<Script>\npublic class MyCustomScript\n{\n" in xml
    assert "\\" not in xml.split("<Script>")[1].split("</Script>")[0], "no backslash in C#"
    # NameClass без пробелов
    nc = re.search(r'NameClass="([^"]*)"', xml).group(1)
    assert " " not in nc and "&" not in nc, "NameClass clean: %r" % nc
    # Имя квеста с & -> пробелы в _, спецсимволы вырезаны
    if qid == 1:
        assert "<Name>Kill_-_Loot_Rats</Name>" in xml, re.search(r"<Name>.*</Name>", xml).group(0)

print("\nALL TESTS PASSED" if ok else "TESTS FAILED")
sys.exit(0 if ok else 1)
