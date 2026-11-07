"""往群里发一条带真·蓝色@的消息（qq_send 只能发文字，at 段要直接打 NapCat）。

用法：python tools/_at_send.py <群号> <被@的QQ> <正文>
"""
import json
import sys
import urllib.request
from pathlib import Path

CFG = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8"))
BASE = CFG["napcat_http"]
HDR = {"Content-Type": "application/json", "Authorization": "Bearer " + CFG["token"]}


def send(group_id, at_qq, text):
    body = {"group_id": int(group_id),
            "message": [{"type": "at", "data": {"qq": str(at_qq)}},
                        {"type": "text", "data": {"text": "：" + text}}]}
    req = urllib.request.Request(BASE + "/send_group_msg", data=json.dumps(body).encode(), headers=HDR)
    return json.load(urllib.request.urlopen(req, timeout=25))


if __name__ == "__main__":
    r = send(*sys.argv[1:4])
    print(r.get("retcode"), r.get("status"), (r.get("data") or {}).get("message_id"), r.get("message"))
