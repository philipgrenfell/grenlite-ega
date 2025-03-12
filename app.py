import uvicorn
from fastapi import FastAPI, HTTPException, Query
import httpx
import os
from pydantic import BaseModel
from dotenv import load_dotenv

app = FastAPI()

# Load environment variables from a .env file
load_dotenv()  # defaults to .env in current directory

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

SITE_ID = os.getenv("SITE_ID")
TEMPLATE_FOLDER_ID = os.getenv("TEMPLATE_FOLDER_ID")
DOCUMENTS_DRIVE_ID = os.getenv("DOCUMENTS_DRIVE_ID")
DOCS_LIST_ID = os.getenv("DOCS_LIST_ID")

# The token endpoint for OAuth 2.0 client_credentials flow
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

# This is the API endpoint for retrieving the list of folders
FOLDER_LIST_URL = (
    f"https://graph.microsoft.com/v1.0/sites/{SITE_ID}/lists/Documents/items?expand=fields"
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

# Request model for folder creation
class FolderRequest(BaseModel):
    parent_folder_id: str  # The folder 'id' from your get_folders API (server_id)
    folder_name: str       # The name of the new folder

@app.post("/create_folder")
async def create_folder(req: FolderRequest):
    """
    Creates a new folder in a known 'Documents' drive inside a specific subfolder.
    """
    # 1) Get the access token
    access_token = await get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    # 2) Microsoft Graph endpoint to create an item (folder) in a parent folder
    create_folder_url = (
        f"https://graph.microsoft.com/v1.0/sites/{SITE_ID}/drives/{DOCUMENTS_DRIVE_ID}"
        f"/items/{req.parent_folder_id}/children"
    )

    # 3) JSON payload marking this item as a folder
    payload = {
        "name": req.folder_name,
        "folder": {},
        "@microsoft.graph.conflictBehavior": "fail"  # or "rename" if you prefer
    }

    # 4) Create the folder
    async with httpx.AsyncClient() as client:
        response = await client.post(create_folder_url, headers=headers, json=payload)
        response.raise_for_status()

    # Return the newly created folder's metadata
    return response.json()

class CopyFolderRequest(BaseModel):
    # This is the ListItem "server ID" (from @odata.etag) of the destination folder
    destination_server_id: str

@app.post("/copy_template_folder")
async def copy_template_folder(req: CopyFolderRequest):
    """
    1) Accepts a destination folder's "server ID" (from the SharePoint list).
    2) Translates that server ID -> numeric item ID -> driveItem ID.
    3) Copies the hard-coded template folder to that driveItem.
    """
    # 1) Get the access token
    access_token = await get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    # 2) Fetch the entire folder hierarchy so we can find the "rawItem" for the given server ID
    async with httpx.AsyncClient() as client:
        all_items_resp = await client.get(
            f"https://graph.microsoft.com/v1.0/sites/{SITE_ID}/lists/Documents/items?expand=fields",
            headers=headers
        )
        all_items_resp.raise_for_status()
        all_items = all_items_resp.json().get("value", [])

    hierarchy = build_folder_hierarchy(all_items)
    matching_node = find_folder_by_id_in_hierarchy(hierarchy, req.destination_server_id)
    if not matching_node:
        raise HTTPException(status_code=404, detail="Destination folder not found.")

    # 3) Extract the numeric list item ID from the node's raw data
    #    - It's typically found in item["fields"]["ID"] (the integer ID)
    raw_item = matching_node["rawItem"]
    fields = raw_item.get("fields", {})
    print(fields)
    numeric_item_id = fields.get("id")  # this should be the int-based ID
    if not numeric_item_id:
        raise HTTPException(
            status_code=400,
            detail="Could not find the numeric list item ID from fields['ID']."
        )

    # 4) Convert that numeric item ID to a driveItem via /lists/{LIST_ID}/items/{ITEM_ID}/driveItem
    #    For example: GET /sites/{SITE_ID}/lists/{DOCS_LIST_ID}/items/25/driveItem
    #    We'll get back the driveItem resource, including its .id
    list_item_drive_item_url = (
        f"https://graph.microsoft.com/v1.0/sites/{SITE_ID}/lists/{DOCS_LIST_ID}/items/{numeric_item_id}/driveItem"
    )
    async with httpx.AsyncClient() as client:
        drive_item_resp = await client.get(list_item_drive_item_url, headers=headers)
        drive_item_resp.raise_for_status()
        drive_item_data = drive_item_resp.json()
    
    parent_drive_item_id = drive_item_data.get("id")
    if not parent_drive_item_id:
        raise HTTPException(
            status_code=400,
            detail="Could not retrieve a valid driveItem.id for the destination folder."
        )

    # 5) Now, use the driveItem ID as the destination for the copy
    copy_url = (
        f"https://graph.microsoft.com/v1.0/sites/{SITE_ID}/drives/{DOCUMENTS_DRIVE_ID}/items/{TEMPLATE_FOLDER_ID}/copy"
    )
    payload = {
        "parentReference": {
            "driveId": DOCUMENTS_DRIVE_ID,
            "id": parent_drive_item_id
        },
        "name": "Copied_Template_Folder"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(copy_url, headers=headers, json=payload)
        # If the copy is accepted, we'll get 202
        if response.status_code == 202:
            return {
                "status": "Copy in progress",
                "message": "The template folder is being copied (including subfolders/files)."
            }
        else:
            # If it's not 202, raise an error or pass back details
            response.raise_for_status()
            return response.json()

@app.get("/folders_get_children/{server_id}")
async def folders_get_children(server_id: str):
    """
    Return the *immediate* child folders (no files) whose parentID == server_id.
    - 404 if the parent folder (server_id) doesn't exist.
    - An empty list if it exists but has no child folders.
    """
    access_token = await get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient() as client:
        # Fetch all items (files + folders) from the 'Documents' list
        resp = await client.get(FOLDER_LIST_URL, headers=headers)
        resp.raise_for_status()
        all_items = resp.json().get("value", [])

    # Helper to extract an item's serverID (from @odata.etag):
    def extract_server_id(item):
        etag_str = item.get("@odata.etag", "")
        return etag_str.strip('"').split(",")[0]

    # First, check if the parent folder actually exists
    parent_exists = any(extract_server_id(item) == server_id for item in all_items)
    if not parent_exists:
        raise HTTPException(status_code=404, detail="Parent folder not found.")

    # Filter for items whose parentReference.id == server_id
    # AND is a folder (FSObjType == 1)
    child_folders = []
    for item in all_items:
        parent_id = item.get("parentReference", {}).get("id")
        print(parent_id)
        if parent_id == server_id:
            child_folders.append({
                "name": item.get("fields", {}).get("FileLeafRef"),
                "serverId": extract_server_id(item)
            })

    return child_folders

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
