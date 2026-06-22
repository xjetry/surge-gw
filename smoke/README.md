# fake-ip / 域名透传验证

目的:确认 Surge 增强模式下,交给 socks5 代理的目标是**域名**(ATYP=domain),
而不是 fake-ip(198.18.x.x)。若是 fake-ip,整套架构不成立。

步骤:
1. 运行:`python3 smoke/socks_logger.py`(监听 127.0.0.1:11800)。
2. Surge 配置临时加一个代理与一条规则:
   `[Proxy]`  `SmokeTest = socks5, 127.0.0.1, 11800`
   `[Rule]`   `DOMAIN-SUFFIX,example.com,SmokeTest`
3. Surge 打开「增强模式」。
4. 浏览器/终端访问 `http://example.com`。
5. 看 logger 输出:
   - `CONNECT DOMAIN -> example.com:80`  → 通过 ✅(架构成立)
   - `CONNECT IP -> 198.18.x.x:80`       → 失败 ⛔(需重评估,见 spec §8/§18)

记录结论到本文件末尾。

## 结论(2026-06-21)

**通过 ✅ —— fake-ip 不到达 socks5,域名透传成立。**

观测:Surge 增强模式下,DNS 给客户端返回虚拟 IP(`example.com → 198.18.1.37`),
但当连接经规则交给 `SmokeTest`(socks5)时,logger 打印的是
`CONNECT DOMAIN -> example.com:80` —— ATYP=domain,目标是域名而非 `198.18.x.x`。

含义:fake-ip 在交给代理前已被 Surge 还原回域名,mihomo 拿到的是域名、在出口解析。
整套架构的头号正确性前提(spec §8)成立,可继续转换层开发。

