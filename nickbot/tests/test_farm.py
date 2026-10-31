"""代理农场（proxyfarm）离线测试。

重点验证三件事：
1. 解析：只接受公网 ip:port，RFC1918 私网地址必须被拒绝（粘贴站上那种
   10.x.x.x:port 列表就是废的）；
2. 池子：add/remove/排序/持久化，站点验证过的排前面；
3. 国家校验的批量查询与站点探测的过滤逻辑（用假数据，不联网）。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nickbot.proxyfarm import Farm, norm_proxy, parse_lines  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name}  {extra}")


def t_parse():
    print("[1] 地址解析")
    check("裸 ip:port", norm_proxy("103.88.237.97:82") == "http://103.88.237.97:82")
    check("http 前缀", norm_proxy("http://45.65.226.174:999") == "http://45.65.226.174:999")
    check("socks5 前缀", norm_proxy("socks5://49.51.244.112:8888") == "socks5://49.51.244.112:8888")
    check("去掉尾随标点", norm_proxy("1.2.3.4:8080,") == "http://1.2.3.4:8080")

    # 关键：粘贴站截图里的 10.x 私网地址不是公网代理
    check("拒绝 10.x 私网", norm_proxy("10.6.20.1:62790") is None)
    check("拒绝 192.168", norm_proxy("192.168.1.5:8080") is None)
    check("拒绝 172.16", norm_proxy("172.16.4.4:3128") is None)
    check("拒绝 127.0.0.1", norm_proxy("127.0.0.1:8080") is None)
    check("拒绝垃圾端口 443", norm_proxy("8.8.8.8:443") is None)
    check("拒绝超范围 IP", norm_proxy("300.1.1.1:8080") is None)
    check("拒绝非地址文本", norm_proxy("no proxy here") is None)

    lines = parse_lines("1.1.1.1:80\n\nbad\nsocks4://2.2.2.2:4145\n10.0.0.9:80\n")
    check("批量解析只留合法项", lines == ["http://1.1.1.1:80", "socks4://2.2.2.2:4145"], lines)
    check("默认 scheme 可指定", parse_lines("3.3.3.3:1080", "socks5") == ["socks5://3.3.3.3:1080"])


def t_pool():
    print("[2] 池子行为")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "in.json"
        f = Farm(country="IN", path=p)

        f.add("1.1.1.1:8080")
        f.add("2.2.2.2:8080")
        f.add("10.0.0.1:8080")           # 私网，必须被拒
        check("只加进公网地址", f.stats()["total"] == 2, f.stats())

        # 模拟一条站点验证通过、延迟更低
        f._db["http://2.2.2.2:8080"].update({"site": True, "latency": 120})
        f._db["http://1.1.1.1:8080"].update({"site": False, "latency": 90})
        check("site-ok 优先于纯延迟", f.sample(1) == ["http://2.2.2.2:8080"], f.sample(2))

        f.drop("http://1.1.1.1:8080")
        check("一次失败只降级不删", "http://1.1.1.1:8080" in f._db)
        f.drop("http://1.1.1.1:8080")
        check("连续两次失败才删", "http://1.1.1.1:8080" not in f._db)

        check("remove 生效", f.remove("2.2.2.2:8080") is True)
        check("remove 不存在的返回 False", f.remove("9.9.9.9:1") is False)

        # 持久化
        f.add("5.5.5.5:8080")
        f2 = Farm(country="IN", path=p)
        check("重启后仍在", "http://5.5.5.5:8080" in f2._db, json.dumps(f2._db))
        check("池文件是合法 JSON", isinstance(json.loads(p.read_text()), dict))

        n = f.clear()
        check("clear 返回条数", n >= 1)
        check("clear 后为空", f.stats()["total"] == 0)


def t_geo_filter():
    print("[3] 国家过滤与站点探测")
    with tempfile.TemporaryDirectory() as d:
        f = Farm(country="IN", path=Path(d) / "in.json")
        f._db = {
            "http://1.1.1.1:80": {"ok": True, "cc": "IN", "latency": 100, "checked": 9e9},
            "http://2.2.2.2:80": {"ok": True, "cc": "US", "latency": 50, "checked": 9e9},
            "http://3.3.3.3:80": {"ok": False, "cc": "IN", "latency": 10, "checked": 9e9},
        }
        ranked = f._ranked()
        check("只返回本国 + ok 的", ranked == ["http://1.1.1.1:80"], ranked)

        # 站点探测：用假 transport 不方便，直接验证 wall 判定函数
        async def probe():
            return await f._probe_site("http://127.0.0.1:1", "http://127.0.0.1:1/x", 1.0)

        check("连不上 = 不可用", asyncio.run(probe()) is False)


def main():
    t_parse()
    t_pool()
    t_geo_filter()
    print(f"\n结果：{ok} 通过 / {fail} 失败")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
