# 混合实验镜像：NapCat 4.15.19（社区公认抗踢版本，issue #1728）+ QQ 3.2.34（当前可登录版本）
# 原理：v4.15.19 镜像里焊死的 QQ 3.2.21 已被腾讯拒登（错误码 45），
#       本镜像在其基础上把 QQ 原地升级为 3.2.34 并重新打 loadNapCat 注入补丁。
# 旁证：Windows 注入版时代即 NapCat 4.15.19 + 本机新 QQ，登录正常、票据从未被吊销。
# 回滚：compose 的 image 改回 docker.m.daocloud.io/mlikiowa/napcat-docker:latest 后 up -d 即可。
FROM mlikiowa/napcat-docker:v4.15.19

RUN arch=$(arch | sed s/aarch64/arm64/ | sed s/x86_64/amd64/) && \
    curl -fL --retry 3 --connect-timeout 30 --max-time 600 -o /tmp/linuxqq.deb \
        "https://qqdl.gtimg.cn/qqfile/QQNT/9.9.36/beta/9ee04bef/linuxqq_3.2.34-53644_${arch}.deb" && \
    dpkg -i --force-depends /tmp/linuxqq.deb && rm /tmp/linuxqq.deb && \
    echo "(async () => {await import('file:///app/napcat/napcat.mjs');})();" > /opt/QQ/resources/app/loadNapCat.js && \
    sed -i 's|"main": "[^"]*"|"main": "./loadNapCat.js"|' /opt/QQ/resources/app/package.json

# 2026-11-03 引用解析修复：4.15.19 在「引用的消息查不到记录」时把 reply 段整个丢掉，
# 导致 bot 收到 qmid=0（主人实测：引用长提示词发「画个」被当无源指令）。
# 补丁是幂等的、锚点校验失败会直接让构建失败，详见 patch_napcat_reply.py 顶部说明。
COPY patch_napcat_reply.py /tmp/patch_napcat_reply.py
RUN python3 /tmp/patch_napcat_reply.py /app/napcat/napcat.mjs && rm -f /tmp/patch_napcat_reply.py
