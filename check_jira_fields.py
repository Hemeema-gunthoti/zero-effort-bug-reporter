import sys, os, base64
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
import httpx

base_url = os.getenv('JIRA_BASE_URL')
email    = os.getenv('JIRA_EMAIL')
token    = os.getenv('JIRA_API_TOKEN')

creds   = base64.b64encode(f'{email}:{token}'.encode()).decode()
headers = {
    'Authorization': f'Basic {creds}',
    'Accept': 'application/json'
}

print(f"Connecting to: {base_url}")
print(f"Email: {email}")
print("")

with httpx.Client(verify=False) as client:
    r = client.get(f'{base_url}/rest/api/2/field', headers=headers)

print(f"Status code: {r.status_code}")
print("")

if r.status_code == 200:
    fields = r.json()
    print("ALL CUSTOM FIELDS FOUND:")
    print("-" * 50)
    for f in fields:
        if 'customfield' in f.get('id', '').lower():
            print(f"  {f['id']:30s} -> {f['name']}")
else:
    print(f"Error: {r.text}")