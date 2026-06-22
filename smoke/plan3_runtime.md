# 运行时 smoke(真实 mihomo + 真订阅)

自动化测试用进程内 fake,不跑真实 mihomo。本闸门用真实二进制端到端验证。

前置:本机装好 `mihomo`(在 PATH 或设 `MIHOMO_BIN`),准备一个真实 `SUBSCRIPTION_URL`。

步骤:
1. 起服务:
   `SUBSCRIPTION_URL=<你的订阅> DATA_DIR=./_smoke_data .venv/bin/python -m surge_gw`
   日志应打印 token 与监听地址。
2. 等首次刷新完成(看日志/`curl -s 127.0.0.1:8080/health | python3 -m json.tool`,`nodes>0`)。
3. 取订阅:`curl -s "127.0.0.1:8080/surge?token=<token>" | head -40`
   应见 `#!MANAGED-CONFIG` 头、`[Proxy]` 里的 `socks5, 127.0.0.1, 12xx`、`[Proxy Group]`、`[Rule]`。
4. 穿某个 socks 端口验出口:`curl -s --socks5-hostname 127.0.0.1:1200 https://api.ipify.org; echo`
   返回 IP 应是该节点出口,而非本机。
5. 验自托管 ruleset:从 `/surge` 里挑一条 `RULE-SET/DOMAIN-SET, http://127.0.0.1:8080/ruleset/<n>?token=...`,
   `curl -s` 该 URL 应返回转换后的域名/规则列表。
6. (可选)观察 reload 是否保连接:刷新时(`curl -XPOST .../refresh?token=`)看已建连接是否中断。

把结论(节点数、出口是否走对、reload 是否保连接)记到本文件末尾。

## 结论(真实 mihomo 1.19.x + 纯 proxy-provider 订阅)

- 节点:proxy-provider 摊平为顶层 proxy 后,17 个可钉定节点;抽样 socks 端口出口 IP 与本机均不同,流量确走节点出口。
- 自托管 ruleset:15 个 https rule-provider 经 socks 隧道上叠 TLS 拉取并托管,Surge 引用为 14 条 DOMAIN-SET + 1 条 RULE-SET。
- reload:`POST /refresh` 后节点保持、无 last_error,既有端口仍正常出口。
- 已知遗留:未设 `GEOSITE_URL` 时 `GEOSITE,cn` 无 geosite.dat,/surge 留一条指向 `/ruleset/geosite-cn` 的 dangling 行(strict=false 容忍;属已记录的 dangling-line 硬化项)。
