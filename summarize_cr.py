import sys, json
lines = [json.loads(l) for l in sys.stdin if l.strip()]
findings = [l for l in lines if l.get("type") == "finding"]
print("total findings:", len(findings))
for f in findings:
    print(f"{f['severity'].upper():>7} | {f['fileName']} | L{f['startLine']}-L{f['endLine']} | {f['title']}")
