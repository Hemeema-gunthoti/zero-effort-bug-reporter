import json, glob, os

files = glob.glob('artifacts/bug_reports/*.json')
if files:
    latest = max(files, key=os.path.getmtime)
    print(f"File: {latest}")
    with open(latest) as f:
        r = json.load(f)
    print('components:', r.get('components'))
    print('labels:    ', r.get('labels'))
else:
    print('no bug reports found — run pytest + python main.py first')