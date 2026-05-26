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

with httpx.Client(verify=False) as client:
    r = client.get(
        f'{base_url}/rest/api/2/user/search',
        headers=headers,
        params={'query': email}
    )

print(f"Status: {r.status_code}")
for user in r.json():
    print(f"Email     : {user.get('emailAddress')}")
    print(f"Name      : {user.get('displayName')}")
    print(f"accountId : {user.get('accountId')}")