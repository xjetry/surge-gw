from __future__ import annotations

from dataclasses import dataclass

from surge_gw.models import RulesetArtifact, SkippedItem

# v2ray Domain.Type
TYPE_PLAIN = 0     # 子串关键字
TYPE_REGEX = 1     # 正则
TYPE_DOMAIN = 2    # 域名 + 子域(后缀)
TYPE_FULL = 3      # 精确域名


@dataclass(frozen=True)
class GeoDomain:
    type: int
    value: str
    attrs: frozenset[str]


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _iter_fields(buf: bytes):
    """遍历一个 protobuf 消息,产出 (field_no, value)。
    只支持本 schema 用到的 wire type:0(varint)、2(length-delimited)。"""
    pos, n = 0, len(buf)
    while pos < n:
        tag, pos = _read_varint(buf, pos)
        field_no, wire = tag >> 3, tag & 0x7
        if wire == 0:
            val, pos = _read_varint(buf, pos)
            yield field_no, val
        elif wire == 2:
            length, pos = _read_varint(buf, pos)
            yield field_no, buf[pos : pos + length]
            pos += length
        else:
            raise ValueError(f"unsupported wire type {wire}")


def _decode_attribute(buf: bytes) -> str:
    for field_no, val in _iter_fields(buf):
        if field_no == 1 and isinstance(val, bytes):
            return val.decode("utf-8")
    return ""


def _decode_domain(buf: bytes) -> GeoDomain:
    dtype, value, attrs = TYPE_PLAIN, "", set()
    for field_no, val in _iter_fields(buf):
        if field_no == 1 and isinstance(val, int):
            dtype = val
        elif field_no == 2 and isinstance(val, bytes):
            value = val.decode("utf-8")
        elif field_no == 3 and isinstance(val, bytes):
            key = _decode_attribute(val)
            if key:
                attrs.add(key)
    return GeoDomain(dtype, value, frozenset(attrs))


def _decode_geosite(buf: bytes) -> tuple[str, list[GeoDomain]]:
    code, domains = "", []
    for field_no, val in _iter_fields(buf):
        if field_no == 1 and isinstance(val, bytes):
            code = val.decode("utf-8")
        elif field_no == 2 and isinstance(val, bytes):
            domains.append(_decode_domain(val))
    return code, domains


def decode_geosite_dat(data: bytes) -> dict[str, list[GeoDomain]]:
    """解码 v2ray GeoSiteList protobuf 为 {分类大写名: [GeoDomain]}。
    .dat 内 include 已在编译期展平,无需递归;只做结构解码。"""
    result: dict[str, list[GeoDomain]] = {}
    for field_no, val in _iter_fields(data):
        if field_no == 1 and isinstance(val, bytes):   # GeoSiteList.entry
            code, domains = _decode_geosite(val)
            result[code.upper()] = domains
    return result


_TYPE_TO_RULE = {TYPE_FULL: "DOMAIN", TYPE_DOMAIN: "DOMAIN-SUFFIX", TYPE_PLAIN: "DOMAIN-KEYWORD"}


def split_geosite_ref(ref: str) -> tuple[str, str | None]:
    """'google@cn' → ('GOOGLE', 'cn');'google' → ('GOOGLE', None)。"""
    if "@" in ref:
        category, attr = ref.split("@", 1)
        return category.upper(), (attr or None)
    return ref.upper(), None


def build_geosite_artifact(domains: list[GeoDomain], attr: str | None) -> RulesetArtifact:
    """按 @attr 过滤后做类型映射。无 keyword(仅 Full/Domain)→ DOMAIN-SET;
    含 keyword(Plain)→ RULE-SET;Regex 跳过 + 计数。"""
    selected = [d for d in domains if attr is None or attr in d.attrs]
    skipped: list[SkippedItem] = []
    kept: list[GeoDomain] = []
    for d in selected:
        if d.type == TYPE_REGEX:
            skipped.append(SkippedItem("geosite-regexp", d.value, "regex domain unsupported"))
        else:
            kept.append(d)

    if any(d.type == TYPE_PLAIN for d in kept):
        lines = [f"{_TYPE_TO_RULE[d.type]},{d.value}" for d in kept]
        return RulesetArtifact(lines=lines, kind="RULE-SET", skipped=skipped)

    # 纯 domain/full → DOMAIN-SET:精确写裸名,后缀加前导点
    lines = [("." + d.value if d.type == TYPE_DOMAIN else d.value) for d in kept]
    return RulesetArtifact(lines=lines, kind="DOMAIN-SET", skipped=skipped)
