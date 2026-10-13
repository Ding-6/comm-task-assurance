import io, sys, json

# 用 utf-8-sig 兼容 BOM；保留中文不损坏
stream = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8-sig")
lines = [json.loads(l.lstrip("\ufeff")) for l in stream if l.strip()]
findings = [l for l in lines if l.get("type") == "finding"]
print("total findings:", len(findings))
for f in findings:
    print(
        f"{f['severity'].upper():>7} | {f['fileName']} | "
        f"L{f['startLine']}-L{f['endLine']} | {f['title']}"
    )
