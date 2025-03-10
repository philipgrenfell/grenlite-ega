import uvicorn
from fastapi import FastAPI, HTTPException
import httpx
import os

app = FastAPI()

# Replace these with your own tenant and client credentials
TENANT_ID = "000d9c9a-f008-4116-9dca-4579f2a629ab"
CLIENT_ID = "435c0419-c254-4c6c-abff-c0fd7ff12064"
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")

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
    access_token = await get_access_token()

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        folder_resp = await client.get(FOLDER_LIST_URL, headers=headers)
        folder_resp.raise_for_status()
        all_items = folder_resp.json().get("value", [])

    # Build the entire tree
    hierarchy = build_folder_hierarchy(all_items)
    return hierarchy


@app.get("/subfolders/{server_id}")
async def get_subfolders(server_id: str):
    """
    Given a unique 'server_id' (GUID from @odata.etag),
    return the sub-tree for that folder. If not found, return 404.
    """
    access_token = await get_access_token()

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        folder_resp = await client.get(FOLDER_LIST_URL, headers=headers)
        folder_resp.raise_for_status()
        all_items = folder_resp.json().get("value", [])

    hierarchy = build_folder_hierarchy(all_items)

    # Try to find the node matching the requested server_id
    matching_node = find_folder_by_id_in_hierarchy(hierarchy, server_id)

    if not matching_node:
        raise HTTPException(status_code=404, detail="Folder not found.")

    return matching_node


async def get_access_token():
    """
    Helper function to fetch an OAuth2 token using client_credentials flow.
    """
    form_data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(TOKEN_URL, data=form_data)
        token_resp.raise_for_status()
        return token_resp.json()["access_token"]


def build_folder_hierarchy(items):
    """
    Given a list of SharePoint 'items' (folders) from Graph,
    build a nested folder structure based on parentReference.id.
    """
    nodes = {}

    for item in items:
        etag_str = item.get("@odata.etag", "")
        # Remove surrounding quotes, then split on ',' to isolate the GUID
        server_id = etag_str.strip('"').split(",")[0]
        parent_id = item.get("parentReference", {}).get("id")  # The parent's GUID

        display_name = item.get("fields", {}).get("FileLeafRef")

        nodes[server_id] = {
            "name": display_name,
            "serverID": server_id,
            "parentID": parent_id,
            "children": [],
            "rawItem": item,  # if you need the raw data
        }

    # Build the hierarchy
    roots = []
    for sid, node in nodes.items():
        pid = node["parentID"]
        if pid and pid in nodes:
            nodes[pid]["children"].append(node)
        else:
            # It's a top-level node
            roots.append(node)

    return roots


def find_folder_by_id_in_hierarchy(tree, target_id: str):
    """
    Recursively search the hierarchy (list of root nodes) for a folder
    whose 'serverID' matches 'target_id'. Return that node with children.
    """
    for node in tree:
        if node["serverID"] == target_id:
            return node
        # Otherwise, search in children
        result = find_folder_by_id_in_hierarchy(node["children"], target_id)
        if result:
            return result
    return None


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
