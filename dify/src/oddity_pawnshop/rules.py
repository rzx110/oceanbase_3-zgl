"""Pure rules, also embedded verbatim in the Dify Code node by build_dsl.py."""
import hashlib
import json

ITEMS = {
    "umbrella": {"title": "阴阳怪气的雨伞", "voice": "嘴硬心软，喜欢用天气比喻情绪",
                 "secret": "我能替你挡住明天的尴尬，但挡不住你昨天没写完的作业。"},
    "clock": {"title": "只响一次的闹钟", "voice": "像一个爱讲冷笑话的退休班主任",
              "secret": "我只在截止日期前五分钟响一次；对拖延症，我一视同仁。"},
    "key": {"title": "开不了门的钥匙", "voice": "一本正经地胡说八道，自认为是哲学家",
            "secret": "我开不了门，但能打开一个你忘记很久的借口。"},
}
ACTIONS = {"greet": "打招呼", "prank": "恶作剧", "apologize": "道歉", "gift": "送礼", "explore": "打听秘密", "recall": "翻旧账"}


def initial_state():
    return {"turn": 0, "trust": 0, "grudge": 0, "pranks": 0, "gifts": 0, "history": []}


def plan_turn(player_code, item, action, utterance, state):
    if item not in ITEMS or action not in ACTIONS:
        raise ValueError("未知物品或行动")
    utterance = utterance or ""
    if not isinstance(utterance, str) or len(utterance) > 140:
        raise ValueError("留言最多 140 字")
    character = ITEMS[item]
    previous = [x["summary"] for x in state["history"]]
    remembered = previous[-1] if previous else "这是初次见面，没有旧账。"
    new = json.loads(json.dumps(state, ensure_ascii=False))
    unlocked = False
    if action != "recall":
        new["turn"] += 1
        if action == "greet":
            new["trust"] = min(10, new["trust"] + 1)
            reaction = "它假装没看你，却把最亮的一面朝向你。"
        elif action == "prank":
            new["trust"] = max(-5, new["trust"] - 2)
            new["grudge"] += 1
            new["pranks"] += 1
            reaction = "它翻出一本《人类离谱行为登记簿》，郑重写下了这一笔。"
        elif action == "apologize":
            new["trust"] = min(10, new["trust"] + 2)
            new["grudge"] = max(0, new["grudge"] - 1)
            reaction = "它把这一页轻轻折起：原谅可以，失忆不行。"
        elif action == "gift":
            new["trust"] = min(10, new["trust"] + 2)
            new["gifts"] += 1
            reaction = "它收下礼物，还装作这只是一次严肃的学术交流。"
        else:
            unlocked = state["trust"] >= 3 and state["grudge"] == 0
            reaction = character["secret"] if unlocked else "它把秘密藏得更深：先攒够 3 点信任，而且别欠着旧账。"
        summary = f"第{new['turn']}轮，你对{character['title']}{ACTIONS[action]}"
        if utterance:
            summary += f"，留下的话是「{utterance}」"
        new["history"] = (new["history"] + [{"turn": new["turn"], "action": action, "summary": summary}])[-8:]
    else:
        reaction = "它摊开旧账本，连你说话的标点都想圈起来。" if previous else "账本空空如也；它决定先给你一次做人的机会。"
    fallback = f"{character['title']}：{reaction}\n\n它记得：{remembered}"
    if action == "prank" and new["pranks"] >= 3:
        fallback += "\n你获得称号：当铺首席整活官。"
    identity = json.dumps({"player": player_code, "item": item, "action": action, "utterance": utterance,
                           "expected_turn": state["turn"]}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"player_code": player_code, "item": item, "action": action, "utterance": utterance,
            "expected_turn": state["turn"], "operation_id": hashlib.sha256(identity.encode()).hexdigest(),
            "state": new, "fallback": fallback, "unlocked": unlocked,
            "write": action != "recall", "character": character, "remembered": remembered}


def format_reply(state, narrative, memory_source):
    history = "\n".join("- " + x["summary"] for x in state["history"]) or "- 账本还空着，欢迎先打个招呼。"
    proof = memory_source["source_id"] if memory_source else "尚未产生记忆记录"
    return (f"## 奇物当铺 · 第 {state['turn']} 轮\n\n{narrative}\n\n"
            f"**信任 {state['trust']}** · **旧账 {state['grudge']}** · 恶作剧累计 {state['pranks']} 次\n\n"
            f"### 真实记忆账本\n{history}\n\n"
            "**怎么玩**：打招呼 +1 信任；送礼/道歉 +2；恶作剧扣 2。信任至少 3 且没有旧账时，可以打听秘密。\n\n"
            "保留玩家代号，下次换一场 Dify 运行，奇物仍认得你。更换代号可从空白账本开始。\n\n"
            f"> PowerContext 记忆：`{proof}`")
