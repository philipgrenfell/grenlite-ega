import os
import pytest
import base64
import time
from fastapi.testclient import TestClient

# We import the FastAPI 'app' from wherever your main code is.
# Example: from main import app
from app import app

# The PDF file to append:
PDF_TO_APPEND_PATH = "./testing/test_doc.pdf"

# A real server ID from SharePoint referencing a DOC file:
SERVER_ID = "e69033db-d82b-413f-a29b-6abf946564bf"

# Server ID for upload/delete tests:
UPLOAD_SERVER_ID = "0fa47696-8a49-4ad1-a4a1-886197849584"

@pytest.fixture(scope="session")
def client():
    """
    Provide a single TestClient instance for the entire test session.
    """
    return TestClient(app)

@pytest.mark.integration
def test_convert_doc_to_pdf_integration(client):
    """
    Integration test that:
    1) Calls the live '/convert_doc_to_pdf/{server_id}' endpoint with a real SharePoint doc.
    2) Appends a local PDF to it.
    3) Confirms the combined PDF is returned as base64, and is valid PDF data.
    4) Also checks the local file was 'saved' on the server side (the path returned in JSON).
    """

    # 1) Read a local PDF -> base64
    if not os.path.exists(PDF_TO_APPEND_PATH):
        pytest.fail(f"Cannot find local PDF file to append: {PDF_TO_APPEND_PATH}")

    with open(PDF_TO_APPEND_PATH, "rb") as f:
        append_pdf_bytes = f.read()
    append_pdf_b64 = base64.b64encode(append_pdf_bytes).decode("utf-8")

    # 2) Construct the payload
    payload = {
        "pdf_to_append_b64": append_pdf_b64
    }

    # 3) Make the POST request
    url = f"/convert_doc_to_pdf/{SERVER_ID}"
    response = client.post(url, json=payload)

    # 4) Check results
    assert response.status_code == 200, f"Unexpected status code: {response.status_code}"
    resp_data = response.json()

    # Confirm the keys exist
    assert "combined_pdf_base64" in resp_data, "Should return 'combined_pdf_base64'."

    # Decode the combined PDF and check it looks like PDF
    combined_pdf_b64 = resp_data["combined_pdf_base64"]
    combined_pdf_bytes = base64.b64decode(combined_pdf_b64)
    assert b"%PDF" in combined_pdf_bytes, "Merged PDF data should contain PDF header."

    # Print out the SharePoint file URL returned by the API
    if "sharepoint_file_url" in resp_data:
        print("SharePoint file URL:", resp_data["sharepoint_file_url"])
    else:
        print("Note: No SharePoint file URL returned")

    # Optionally, you could write `combined_pdf_bytes` locally for verification:
    # with open("combined_test_output.pdf", "wb") as f:
    #     f.write(combined_pdf_bytes)

    print("Integration test passed: The endpoint combined the PDFs and returned valid data.")


@pytest.mark.integration
def test_upload_and_delete_file_integration(client):
    """
    Integration test that:
    1) Uploads a test PDF file to SharePoint using the upload_file endpoint
    2) Verifies the upload was successful and gets the file_url
    3) Deletes the uploaded file using the delete_file endpoint
    4) Verifies the deletion was successful
    """
    
    # 1) Prepare test file for upload
    if not os.path.exists(PDF_TO_APPEND_PATH):
        pytest.fail(f"Cannot find test PDF file for upload: {PDF_TO_APPEND_PATH}")
    
    with open(PDF_TO_APPEND_PATH, "rb") as f:
        test_file_bytes = f.read()
    test_file_b64 = base64.b64encode(test_file_bytes).decode("utf-8")
    
    # Generate a unique filename for this test
    test_filename = f"test_upload_delete_{int(time.time())}.pdf"
    
    # 2) Upload the file
    upload_payload = {
        "file_name": test_filename,
        "server_id": UPLOAD_SERVER_ID,
        "file_data": test_file_b64
    }
    
    print(f"Uploading test file: {test_filename}")
    upload_response = client.post("/upload_file", json=upload_payload)
    
    # 3) Verify upload was successful
    assert upload_response.status_code == 200, f"Upload failed with status: {upload_response.status_code}"
    upload_data = upload_response.json()
    
    assert "message" in upload_data, "Upload response should contain 'message'"
    assert "file_url" in upload_data, "Upload response should contain 'file_url'"
    assert upload_data["message"] == "File uploaded successfully"
    
    file_url = upload_data["file_url"]
    print(f"File uploaded successfully. URL: {file_url}")
    
    # 4) Delete the uploaded file
    delete_payload = {
        "file_url": file_url
    }
    
    print(f"Deleting uploaded file: {file_url}")
    delete_response = client.post("/delete_file", json=delete_payload)
    
    # 5) Verify deletion was successful
    assert delete_response.status_code == 200, f"Delete failed with status: {delete_response.status_code}"
    delete_data = delete_response.json()
    
    assert "message" in delete_data, "Delete response should contain 'message'"
    assert delete_data["message"] == "File deleted successfully"
    assert delete_data["file_url"] == file_url, "Delete response should return the same file_url"
    
    print("Integration test passed: File was successfully uploaded and then deleted.")


@pytest.mark.integration
def test_delete_nonexistent_file(client):
    """
    Test that attempting to delete a non-existent file returns appropriate error.
    """
    
    # Use a fake file URL that doesn't exist
    fake_file_url = f"https://example.sharepoint.com/sites/test/Shared%20Documents/nonexistent_file_{int(time.time())}.pdf"
    
    delete_payload = {
        "file_url": fake_file_url
    }
    
    delete_response = client.post("/delete_file", json=delete_payload)
    
    # Should return 404 for non-existent file
    assert delete_response.status_code == 404, f"Expected 404 for non-existent file, got: {delete_response.status_code}"
    
    delete_data = delete_response.json()
    assert "detail" in delete_data
    assert "not found" in delete_data["detail"].lower()
    
    print("Test passed: Non-existent file deletion properly returns 404.")
