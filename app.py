import uvicorn
from fastapi import FastAPI, HTTPException, Query
import httpx
import os
from pydantic import BaseModel
from dotenv import load_dotenv
import base64
from io import BytesIO, StringIO
from pypdf import PdfReader, PdfWriter
from datetime import datetime, timedelta
import csv
from fastapi.responses import StreamingResponse
from typing import List, Dict, Any
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# This is the API endpoint for retrieving the list of folders/items
FOLDER_LIST_URL = (
    f"https://graph.microsoft.com/v1.0/sites/{SITE_ID}/lists/Documents/items?expand=fields"
)

# SharePoint file upload API template
SHAREPOINT_UPLOAD_URL_TEMPLATE = (
    "https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/items/{parent_id}:/{file_name}:/content"
)

# ================================
# Pydantic Models
# ================================
class FileUploadRequest(BaseModel):
    file_name: str
    server_id: str
    file_data: str  # Base64-encoded file content

class FolderRequest(BaseModel):
    parent_folder_id: str  # The folder 'id' from your get_folders API (server_id)
    folder_name: str       # The name of the new folder

class CopyFolderRequest(BaseModel):
    destination_server_id: str  # The "server ID" (from @odata.etag) of the destination folder

class CombinePDFRequest(BaseModel):
    """
    pdf_to_append_b64: Base64-encoded PDF that will be appended to the 
    Word-doc-conversion PDF.
    """
    pdf_to_append_b64: str

class TimeEntry(BaseModel):
    total_time: float
    type: str

class TimesheetEntry(BaseModel):
    client: str
    project_number: str
    date: str
    employee_first_name: str
    employee_last_name: str
    employee_email: str
    employee_unique_id: str
    time_entries: List[TimeEntry]

class TimesheetRequest(BaseModel):
    start_date: str
    end_date: str
    timesheets: List[TimesheetEntry]

class DeleteFileRequest(BaseModel):
    file_url: str  # The webUrl returned from upload_file endpoint

# ================================
# Main Endpoints
# ================================
@app.post("/upload_file")
async def upload_file(request: FileUploadRequest):
    """
    Uploads a base64-encoded file to a given SharePoint folder (server_id).
    """
    access_token = await get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/octet-stream"
    }

    # Decode the base64 file content 
    try:
        file_content = base64.b64decode(request.file_data)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 file data")

    # Construct SharePoint file upload URL
    upload_url = SHAREPOINT_UPLOAD_URL_TEMPLATE.format(
        site_id=SITE_ID,
        drive_id=DOCUMENTS_DRIVE_ID,    
        parent_id=request.server_id,
        file_name=request.file_name
    )

    # Upload the file to SharePoint
    async with httpx.AsyncClient() as client:
        response = await client.put(upload_url, headers=headers, content=file_content)

    if response.status_code not in (200, 201):
        raise HTTPException(status_code=response.status_code, detail=response.text)

    response_data = response.json()
    
    # Store the item ID for reliable deletion and construct a standard URL
    item_id = response_data.get("id")
    
    # Get the parent path and filename to construct a consistent URL
    parent_reference = response_data.get("parentReference", {})
    parent_path = parent_reference.get("path", "").replace("/drive/root:", "")
    file_name = response_data.get("name", request.file_name)
    
    # Construct a standard SharePoint document library URL format
    # This format works for deletion regardless of file type
    if parent_path:
        file_path = f"{parent_path}/{file_name}"
    else:
        file_path = file_name
    
    # Remove leading slash if present
    if file_path.startswith("/"):
        file_path = file_path[1:]
    
    # Return both the constructed path and item ID for deletion
    # We'll embed the item_id in a way that delete can extract it
    file_url = f"item_id:{item_id}|path:{file_path}"

    return {"message": "File uploaded successfully", "file_url": file_url}


@app.post("/delete_file")
async def delete_file(request: DeleteFileRequest):
    """
    Deletes a file from SharePoint using the file_url returned from upload_file.
    Supports both new item_id format and legacy webUrl format.
    """
    access_token = await get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    
    try:
        delete_url = None
        
        # Check if this is the new format with item_id
        if request.file_url.startswith("item_id:"):
            # Extract item_id from the new format
            parts = request.file_url.split("|")
            item_id = parts[0].replace("item_id:", "")
            
            # Use the item ID for direct deletion - more reliable
            delete_url = f"https://graph.microsoft.com/v1.0/sites/{SITE_ID}/drives/{DOCUMENTS_DRIVE_ID}/items/{item_id}"
            
            logger.info(f"Attempting to delete file by item ID: {item_id}")
            
        else:
            # Legacy webUrl format - parse the URL to extract the relative path
            from urllib.parse import urlparse, unquote
            parsed_url = urlparse(request.file_url)
            
            # Extract the path after "Shared Documents" or "Documents"
            path_parts = unquote(parsed_url.path).split('/')
            
            # Find the index of "Documents" in the path
            docs_index = -1
            for i, part in enumerate(path_parts):
                if part in ["Documents", "Shared%20Documents", "Shared Documents"]:
                    docs_index = i
                    break
            
            if docs_index == -1:
                raise HTTPException(status_code=400, detail="Invalid file URL format - could not find Documents folder")
            
            # Get the relative path after Documents
            relative_path = '/'.join(path_parts[docs_index + 1:])
            
            if not relative_path:
                raise HTTPException(status_code=400, detail="Invalid file URL - no file path found")
            
            # Construct the Graph API delete URL
            delete_url = f"https://graph.microsoft.com/v1.0/sites/{SITE_ID}/drives/{DOCUMENTS_DRIVE_ID}/root:/{relative_path}"
            
            logger.info(f"Attempting to delete file at path: {relative_path}")
        
        # Delete the file
        async with httpx.AsyncClient() as client:
            response = await client.delete(delete_url, headers=headers)
            
            if response.status_code == 204:
                return {"message": "File deleted successfully", "file_url": request.file_url}
            elif response.status_code == 404:
                raise HTTPException(status_code=404, detail="File not found")
            else:
                logger.error(f"Delete failed with status {response.status_code}: {response.text}")
                response.raise_for_status()
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting file: {str(e)}")


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
    matching_node = find_folder_by_id_in_hierarchy(hierarchy, server_id)
    if not matching_node:
        raise HTTPException(status_code=404, detail="Folder not found.")

    return matching_node


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
        "@microsoft.graph.conflictBehavior": "fail"  # or "rename"
    }

    # 4) Create the folder
    async with httpx.AsyncClient() as client:
        response = await client.post(create_folder_url, headers=headers, json=payload)
        response.raise_for_status()

    return response.json()


@app.post("/copy_template_folder")
async def copy_template_folder(req: CopyFolderRequest):
    """
    1) Accepts a destination folder's "server ID" (from the SharePoint list).
    2) Translates that server ID -> numeric item ID -> driveItem ID.
    3) Copies the hard-coded template folder to that driveItem.
    """
    access_token = await get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    # 2) Fetch the entire folder hierarchy to find the rawItem for the given server ID
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

    # 3) Extract the numeric list item ID
    raw_item = matching_node["rawItem"]
    fields = raw_item.get("fields", {})
    numeric_item_id = fields.get("id")  # integer ID for the list item
    if not numeric_item_id:
        raise HTTPException(
            status_code=400,
            detail="Could not find the numeric list item ID from fields['ID']."
        )

    # 4) Convert numeric item ID to a driveItem ID
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

    # 5) Copy using the driveItem ID
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
        # Microsoft Graph returns 202 if accepted
        if response.status_code == 202:
            return {
                "status": "Copy in progress",
                "message": "The template folder is being copied."
            }
        else:
            response.raise_for_status()
            return response.json()


@app.get("/folders_get_children/{server_id}")
async def folders_get_children(server_id: str):
    """
    Return details of the current folder (by server_id) and its immediate subfolders.
    """
    access_token = await get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient() as client:
        resp = await client.get(FOLDER_LIST_URL, headers=headers)
        resp.raise_for_status()
        all_items = resp.json().get("value", [])

    # Helper
    def extract_server_id(item):
        etag_str = item.get("@odata.etag", "")
        return etag_str.strip('"').split(",")[0]

    # 1) Locate the item
    queried_item = next(
        (item for item in all_items if extract_server_id(item) == server_id),
        None
    )
    if not queried_item:
        raise HTTPException(status_code=404, detail="Folder not found.")

    # 2) Extract parent
    queried_folder_parent_id = queried_item.get("parentReference", {}).get("id")
    queried_folder_name = queried_item.get("fields", {}).get("FileLeafRef")

    # 3) Locate the parent item (if any)
    queried_folder_parent_name = None
    if queried_folder_parent_id:
        parent_item = next(
            (item for item in all_items if extract_server_id(item) == queried_folder_parent_id),
            None
        )
        if parent_item:
            queried_folder_parent_name = parent_item.get("fields", {}).get("FileLeafRef")

    # 4) Collect immediate child folders
    child_folders = []
    for item in all_items:
        parent_id = item.get("parentReference", {}).get("id")
        content_type = item.get("fields", {}).get("ContentType")
        if parent_id == server_id and content_type == "Folder":
            child_folders.append({
                "name": item.get("fields", {}).get("FileLeafRef"),
                "serverId": extract_server_id(item)
            })
    
    child_folders.sort(key=lambda x: x["name"].lower())
    # 5) Return
    return {
        "queriedFolderParentId": queried_folder_parent_id,
        "queriedFolderParentName": queried_folder_parent_name,
        "queriedFolderServerId": server_id,
        "queriedFolderName": queried_folder_name,
        "childrenFolders": child_folders
    }

@app.post("/convert_doc_to_pdf/{server_id}")
async def convert_doc_to_pdf(server_id: str, req: CombinePDFRequest):
    """
    1) Takes a 'server_id' for an existing Word doc in SharePoint.
    2) Converts it to PDF (via Microsoft Graph).
    3) Decodes the user-provided PDF from 'pdf_to_append_b64'.
    4) Appends that PDF to the newly converted PDF (end-to-end).
    5) Returns the combined PDF as base64.
    """
    access_token = await get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    # 1) Get the entire set of list items to locate this file
    async with httpx.AsyncClient() as client:
        all_items_resp = await client.get(FOLDER_LIST_URL, headers=headers)
        all_items_resp.raise_for_status()
        all_items = all_items_resp.json().get("value", [])

    # 2) Find the file item
    matching_node = find_folder_by_id_in_hierarchy(build_folder_hierarchy(all_items), server_id)
    if not matching_node:
        raise HTTPException(status_code=404, detail="Could not find an item with this server_id.")

    raw_item = matching_node["rawItem"]
    fields = raw_item.get("fields", {})
    numeric_item_id = fields.get("id")  # integer ID for the list item
    if fields.get("ContentType") != "Document":
        raise HTTPException(status_code=400, detail="The specified item is not a document.")
    if not numeric_item_id:
        raise HTTPException(status_code=400, detail="Could not find numeric list item ID.")

    # 3) Convert numeric item ID -> driveItem ID
    list_item_drive_item_url = (
        f"https://graph.microsoft.com/v1.0/sites/{SITE_ID}"
        f"/lists/{DOCS_LIST_ID}/items/{numeric_item_id}/driveItem"
    )
    async with httpx.AsyncClient() as client:
        drive_item_resp = await client.get(list_item_drive_item_url, headers=headers)
        drive_item_resp.raise_for_status()
        drive_item_data = drive_item_resp.json()

    drive_item_id = drive_item_data.get("id")
    if not drive_item_id:
        raise HTTPException(status_code=400, detail="Could not retrieve driveItem.id for the file.")

    # 4) Request the doc as PDF
    pdf_url = (
        f"https://graph.microsoft.com/v1.0/sites/{SITE_ID}/drives/{DOCUMENTS_DRIVE_ID}"
        f"/items/{drive_item_id}/content?format=pdf"
    )
    async with httpx.AsyncClient(follow_redirects=True) as client:
        pdf_resp = await client.get(pdf_url, headers=headers)
        if pdf_resp.status_code != 200:
            raise HTTPException(
                status_code=pdf_resp.status_code,
                detail=f"Conversion failed. Graph error: {pdf_resp.text}",
            )
        word_doc_pdf_bytes = pdf_resp.content

    # 5) Decode user's PDF from base64
    try:
        append_pdf_bytes = base64.b64decode(req.pdf_to_append_b64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 PDF to append.")

    # 6) Combine the two PDFs in memory
    try:
        # Read the newly converted doc PDF
        doc_pdf_reader = PdfReader(BytesIO(word_doc_pdf_bytes))
        # Read the PDF to append
        append_pdf_reader = PdfReader(BytesIO(append_pdf_bytes))

        # Prepare a single writer
        pdf_writer = PdfWriter()
        
        # Copy pages from doc_pdf_reader
        for page in doc_pdf_reader.pages:
            pdf_writer.add_page(page)
        # Then from append_pdf_reader
        for page in append_pdf_reader.pages:
            pdf_writer.add_page(page)

        # Write to memory
        merged_pdf_io = BytesIO()
        pdf_writer.write(merged_pdf_io)
        merged_pdf_io.seek(0)
        merged_pdf_bytes = merged_pdf_io.read()

        #write to local
        local_filename = f"testing/{server_id}_combined.pdf"
        with open(local_filename, "wb") as f:
            f.write(merged_pdf_bytes)

        # Base64-encode the final PDF
        final_pdf_base64 = base64.b64encode(merged_pdf_bytes).decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error combining PDFs: {str(e)}")

   # Identify the parent folder's driveItem ID so we can place the merged PDF there
    parent_drive_item_id = drive_item_data.get("parentReference", {}).get("id")
    if not parent_drive_item_id:
        raise HTTPException(
            status_code=400, 
            detail="Could not find the parent folder's driveItem ID."
        )

    # 7) UPLOAD the merged PDF to the same folder using the existing '/upload_file' logic.
    #    We'll re-encode 'merged_pdf_bytes' as base64 and let `upload_file` handle the rest.
    from typing import cast
    upload_request_data = FileUploadRequest(
        file_name=f"{server_id}_combined.pdf",
        server_id=parent_drive_item_id,  # same parent folder as original doc
        file_data=base64.b64encode(merged_pdf_bytes).decode("utf-8")
    )

    # We call our existing /upload_file function *directly*. 
    # This is an async function, so we must await it.
    upload_response = await upload_file(upload_request_data)
    # e.g. upload_response -> { "message": "...", "file_url": "<SharePoint link>" }
    sharepoint_file_url = upload_response.get("file_url", None)

    # 8) Return the final PDF + the new SharePoint file URL
    return {
        "combined_pdf_base64": final_pdf_base64,
        "sharepoint_file_url": sharepoint_file_url
    }

@app.post("/process_timesheets")
async def process_timesheets(request: TimesheetRequest):
    """
    Process timesheet JSON and return a CSV with employee details, client info,
    and daily hours for each date in the specified range.
    """
    # Log the incoming request for debugging
    logger.info(f"Received timesheet request: start_date={request.start_date}, end_date={request.end_date}")
    logger.info(f"Number of timesheets: {len(request.timesheets)}")
    logger.info(f"Full request data: {request.model_dump()}")
    
    try:
        # Parse start and end dates
        start_date = datetime.strptime(request.start_date, "%d-%b-%Y")
        end_date = datetime.strptime(request.end_date, "%d-%b-%Y")
        
        # Generate all dates between start and end
        date_range = []
        current_date = start_date
        while current_date <= end_date:
            date_range.append(current_date.strftime("%d-%b-%Y"))
            current_date += timedelta(days=1)
        
        # Process timesheets data
        employee_data = {}
        
        for timesheet in request.timesheets:
            # Create unique key for each employee-client-project combination
            key = (
                timesheet.employee_first_name,
                timesheet.employee_last_name,
                timesheet.employee_email,
                timesheet.client,
                timesheet.project_number
            )
            
            if key not in employee_data:
                employee_data[key] = {
                    "employee_first_name": timesheet.employee_first_name,
                    "employee_last_name": timesheet.employee_last_name,
                    "employee_email": timesheet.employee_email,
                    "client_name": timesheet.client,
                    "job_number": timesheet.project_number,
                    "daily_hours": {date: 0.0 for date in date_range},
                    "total_hours": 0.0
                }
            
            # Sum up time entries for this timesheet entry - only "EGA Consultant" type
            daily_total = sum(entry.total_time for entry in timesheet.time_entries if entry.type == "EGA Consultant")
            
            # Only process if there are EGA Consultant hours
            if daily_total > 0:
                # Normalize the timesheet date to match our date range format
                timesheet_date = datetime.strptime(timesheet.date, "%d-%b-%Y").strftime("%d-%b-%Y")
                
                # Add to the appropriate date
                if timesheet_date in employee_data[key]["daily_hours"]:
                    employee_data[key]["daily_hours"][timesheet_date] += daily_total
                    employee_data[key]["total_hours"] += daily_total
        
        # Generate CSV content
        output = StringIO()
        writer = csv.writer(output)
        
        # Write header
        header = [
            "employee_first_name",
            "employee_last_name", 
            "employee_email",
            "client_name",
            "job_number"
        ] + date_range + ["total_hours"]
        
        writer.writerow(header)
        
        # Write data rows
        for employee in employee_data.values():
            row = [
                employee["employee_first_name"],
                employee["employee_last_name"],
                employee["employee_email"],
                employee["client_name"],
                employee["job_number"]
            ] + [employee["daily_hours"][date] for date in date_range] + [employee["total_hours"]]
            
            writer.writerow(row)
        
        # Prepare response
        output.seek(0)
        csv_content = output.getvalue()
        
        return StreamingResponse(
            BytesIO(csv_content.encode('utf-8')),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=timesheets.csv"}
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Date parsing error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing timesheets: {str(e)}")

# ================================
# Utilities
# ================================
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
    Given a list of SharePoint 'items' from Graph,
    build a nested folder structure based on parentReference.id.
    """
    nodes = {}

    for item in items:
        etag_str = item.get("@odata.etag", "")
        server_id = etag_str.strip('"').split(",")[0]
        parent_id = item.get("parentReference", {}).get("id")  # parent's GUID
        display_name = item.get("fields", {}).get("FileLeafRef")

        nodes[server_id] = {
            "name": display_name,
            "serverID": server_id,
            "parentID": parent_id,
            "children": [],
            "rawItem": item,
        }

    # Build hierarchy
    roots = []
    for sid, node in nodes.items():
        pid = node["parentID"]
        if pid and pid in nodes:
            nodes[pid]["children"].append(node)
        else:
            roots.append(node)

    return roots


def find_folder_by_id_in_hierarchy(tree, target_id: str):
    """
    Recursively search the hierarchy (list of root nodes) for an item
    whose 'serverID' matches 'target_id'. Return that node with children.
    """
    for node in tree:
        if node["serverID"] == target_id:
            return node
        result = find_folder_by_id_in_hierarchy(node["children"], target_id)
        if result:
            return result
    return None


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
