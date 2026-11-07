#!/usr/bin/env python3
"""把 NapCat 4.15.19 的「引用消息解析失败就丢弃 reply 段」修掉。

背景（2026-11-03 主人实测）：
    在私聊里引用一条**机器人自己发的长提示词**（Moss 布局 JSON，3.7k 字）再发「画个」，
    bot 收到的 OneBot 事件里 **没有 reply 段**（qmid=0），于是「画个」被当成无源指令。
    NapCat 容器日志同一条消息却显示它收到了 `[回复消息 ... {JSON}...]`，紧跟：
        快速回复，跳过方法1查询，序号: 1995, 消息数: 1
        所有查找方法均失败，获取不到带记录的引用消息 1995
    根因有两个：
      ① `replyElement(element, msg, _, quick_reply)` 的第 4 个参数实际是**选项对象**
         `{parseMultMsg, disableGetUrl, quick_reply}`，永远为 truthy ⇒ 方法1查不到时
         直接 `return undefined`，方法2/3（按 seq / V3）永远不跑；
      ② 三条查询全失败后 `return null` ⇒ 整个 reply 段被删掉，而事件里
         `element.sourceMsgIdInRecords` + `msg.records` 明明还带着被引消息的 msgId。

修法（最小、可回滚）：
      A. 签名改成 `(element, msg, _, { quick_reply } = {})`，让 quick_reply 恢复布尔语义；
      B. 两个「所有查找方法均失败」之后，用事件自带的 `records.msgId` 兜底生成 reply 段
         （NapCat 自己对 284840486/1094950020 两个号就是这么直接用的，id 可被 get_msg 解析）。

用法：
    python3 patch_napcat_reply.py /app/napcat/napcat.mjs
幂等：已打过（检测到 marker）就直接退出 0；锚点对不上则退出 1（构建期会当场失败）。
"""
import sys

MARK = "回退用事件自带记录生成引用段"

SIG_OLD = "replyElement: async (element, msg, _, quick_reply) => {"
SIG_NEW = "replyElement: async (element, msg, _, { quick_reply } = {}) => {"

ERR1_OLD = ('        this.core.context.logger.logError('
            '"所有查找方法均失败，获取不到带记录的引用消息", element.replayMsgSeq);')
ERR1_NEW = ERR1_OLD + (
    '\n        if (records?.msgId) { this.core.context.logger.logWarn('
    '"引用解析失败，' + MARK + '"); return createReplyData(records.msgId); }')

ERR2_OLD = ('        this.core.context.logger.logError('
            '"所有查找方法均失败，获取不到旧客户端的引用消息", element.replayMsgSeq);')
ERR2_NEW = ERR2_OLD + (
    '\n        if (records?.msgId) { this.core.context.logger.logWarn('
    '"引用解析失败(旧客户端)，' + MARK + '"); return createReplyData(records.msgId); }')


def main(path):
    with open(path, "r", encoding="utf-8") as f:
        s = f.read()
    if MARK in s:
        print("already patched:", path)
        return 0
    for name, anchor in (("signature", SIG_OLD), ("err1", ERR1_OLD), ("err2", ERR2_OLD)):
        n = s.count(anchor)
        if n != 1:
            print("FAIL: anchor %s count=%d (expect 1)" % (name, n), file=sys.stderr)
            return 1
    s = s.replace(SIG_OLD, SIG_NEW, 1)
    s = s.replace(ERR1_OLD, ERR1_NEW, 1)
    s = s.replace(ERR2_OLD, ERR2_NEW, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)
    print("patched:", path)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: patch_napcat_reply.py <napcat.mjs>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
