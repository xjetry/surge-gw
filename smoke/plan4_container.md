# 容器内 smoke(真实 mihomo + 真订阅,单容器)

自动化测试不跑真实容器。本闸门用 docker compose 端到端验证部署层。

前置:OrbStack(支持 `network_mode: host`,容器绑回环即宿主可直连)运行;
`cp .env.example .env` 并把 `SUBSCRIPTION_URL` 改成真订阅。

步骤:
1. 起服务:`docker compose up -d --build`。
2. 看监听日志:`docker compose logs | grep "serving on"`。
3. 等就绪:`docker inspect --format '{{.State.Health.Status}}' $(docker compose ps -q surge-gw)` 转 `healthy`,
   或 `curl -s 127.0.0.1:8080/health | python3 -m json.tool` 见 `nodes>0`。
4. 取订阅:`curl -s "127.0.0.1:8080/surge" | head -40`
   应见 `#!MANAGED-CONFIG` 头、`[Rule]` 顶部一批节点服务器 `IP-CIDR/DOMAIN ...,DIRECT`、
   `[Proxy]` 里 `socks5, 127.0.0.1, 12xx`、`[Proxy Group]`。
5. 穿某 socks 端口验出口:`curl -s --socks5-hostname 127.0.0.1:1200 https://api.ipify.org; echo`
   返回 IP 应是该节点出口而非本机。
6. 验断环(宿主 Surge 处于 TUN/增强模式时):`docker compose logs --tail=50` 不应反复出现
   `dial ... <节点服务器IP> ... connect error` 风暴;Surge 连接列表无 `OrbStack Helper → 节点服务器IP`
   经节点策略反复重连。
7. 验自托管 ruleset:从 `/surge` 取一条 `RULE-SET/DOMAIN-SET, http://127.0.0.1:8080/ruleset/<n>`,
   `curl -s` 该 URL 应返回转换后的域名/规则列表。
8. 验优雅退出:`time docker compose stop surge-gw` 应秒级返回(非等 10s SIGKILL);
   `docker compose logs --tail=20` 无 mihomo 孤儿/异常退出告警。
9. (可选)设 `GEOSITE_URL` 后复跑,`GEOSITE,cn` 应有自托管 ruleset 可拉。

把结论(就绪用时、节点数、出口是否走对、stop 是否秒退)记到本文件末尾。

## 结论(host 网络 + 仅绑回环 + 无 token + 真实 mihomo v1.19.27 + 纯 proxy-provider 订阅)

- 镜像构建:`docker build` 成功,镜像内 mihomo 为固定版本 v1.19.27,二进制 sha256 校验通过。
- 网络:`network_mode: host` 下容器内 HTTP/socks 仅绑 `127.0.0.1`,宿主经 `127.0.0.1` 直连(无 publish、不向局域网暴露);宿主 `curl 127.0.0.1:8080/health` 直达。
- 无 token:`/surge`、`/ruleset/<n>`、`/health` 均无鉴权直出;`/surge` 中 `?token=` 计数为 0。
- 就绪:首刷数秒内完成,`/health` 报 `nodes=17`、`skipped=2`(未设 `GEOSITE_URL` 时 `GEOSITE,cn` 等干净跳过)、`last_error=null`;容器 HEALTHCHECK 转 `healthy`。
- 断环:`/surge` 的 `[Rule]` 顶部为一批 `IP-CIDR,<节点服务器IP>/32,DIRECT,no-resolve` + 若干 `DOMAIN,<域名节点>,DIRECT`,排在 `GEOIP`/`FINAL` 之前;曾死循环的那个节点服务器 IP 已 DIRECT。新容器整段日志 `connect error`、该 IP 命中均为 0,无 dial 风暴。
- 出口:穿 socks `1200` 出口为对应节点出口 IP 而非本机,且 12s 内即返回(不再因环路 hang)。
- 自托管 ruleset:`/ruleset/<n>` 经 socks 隧道拉取 + 转换后正常返回(如 `claude-classical` 返回转换后的 classical 规则)。
- 优雅退出:`docker compose stop` 秒级返回(远低于 10s SIGKILL 超时),PID 1 收到 SIGTERM 后干净终止 mihomo 子进程,无孤儿。
- 注意:宿主 Surge 须重载该 MANAGED-CONFIG(等回取间隔或手动 reload)后,新的节点服务器 DIRECT 规则才在 Surge 侧生效、彻底断环。
