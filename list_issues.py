import urllib.request, json
url = "https://api.github.com/repos/repowise-dev/repowise/issues?state=open&per_page=15"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req) as response:
    data = json.loads(response.read().decode())
    
for item in data:
    if "pull_request" not in item:
        labels = [l["name"] for l in item["labels"]]
        print(f"#{item['number']}: {item['title']} (Labels: {', '.join(labels)})")
