import requests

url = "https://dev299893.service-now.com/api/now/table/incident"

username = "svc_infra_mfdm"
password = "AiTest@2134"

params = {
    "sysparm_limit": 2
}

headers = {
    "Accept": "application/json"
}

response = requests.get(
    url,
    params=params,
    headers=headers,
    auth=(username, password),
    timeout=30
)

print("URL:", response.url)
print("HTTP Status:", response.status_code)
print("Response:", response.text)