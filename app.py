import uvicorn
from fastapi import FastAPI
import httpx

app = FastAPI()

# Replace these with your own tenant and client credentials
TENANT_ID = "000d9c9a-f008-4116-9dca-4579f2a629ab"
CLIENT_ID = "435c0419-c254-4c6c-abff-c0fd7ff12064"
CLIENT_SECRET = "nET8Q~Sh5KqzqCsWWQOaGhtM8wo7LWNLeDgHmbfr"

# The token endpoint for OAuth 2.0 client_credentials flow
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

# This is the API endpoint for retrieving the list of folders
FOLDER_LIST_URL = (
    "https://graph.microsoft.com/v1.0/sites/"
    "f09e9314-eee5-4148-8b51-7b7513684d40,c70bf214-5dc0-48ed-a0ec-edb1849fd9c9/"
    "lists/Documents/items?expand=fields"
)

@app.get("/folders")
async def get_folders():
    """
    1. Acquire OAuth2 token with client credentials.
    2. Pull folder items from Microsoft Graph.
    3. Build and return a nested folder hierarchy.
    """
    # Step 1: Get an access token
    form_data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(TOKEN_URL, data=form_data)
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

    # Step 2: Pull list items (folders) from Graph
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        folder_resp = await client.get(FOLDER_LIST_URL, headers=headers)
        folder_resp.raise_for_status()
        all_items = folder_resp.json().get("value", [])

    # Step 3: Build hierarchy and return it
    hierarchy = build_folder_hierarchy(all_items)
    return hierarchy


def build_folder_hierarchy(items):
    """
    Given a list of SharePoint 'items' (folders) from Graph,
    build a nested folder structure based on parentReference.id.
    """
    # 1) Parse each item and store a node keyed by the "serverID" (from the etag).
    #    We'll also store "parentID" from item["parentReference"]["id"] if present.
    nodes = {}

    for item in items:
        # Example etag: "9935d002-1e2e-47d4-85d4-85a7deb48052,1"
        etag_str = item.get("@odata.etag", "")
        # Remove surrounding quotes, then split on ',' to isolate the first GUID part
        server_id = etag_str.strip('"').split(",")[0]
        parent_id = item.get("parentReference", {}).get("id")  # This is the parent GUID

        display_name = item.get("fields", {}).get("FileLeafRef")
        # You can include other fields as needed — e.g., item IDs, Avaza_CompanyID, etc.

        # Initialize a dict for this node
        nodes[server_id] = {
            "name": display_name,
            "serverID": server_id,
            "parentID": parent_id,
            "children": [],
            "rawItem": item,  # Optional, if you need the raw data
        }

    # 2) For each node, attach it to its parent (if the parent exists in the dictionary).
    #    If parentID is not in nodes, that folder is top-level (root in this subset).
    roots = []
    for server_id, node in nodes.items():
        parent_id = node["parentID"]
        if parent_id and parent_id in nodes:
            # Attach this node to the parent's children
            nodes[parent_id]["children"].append(node)
        else:
            # This node is top-level in our hierarchy
            roots.append(node)

    return roots


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)