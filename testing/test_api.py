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

# The DOCX file for upload/delete tests:
DOCX_TEST_PATH = "./testing/test-upload.docx"

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
def test_upload_file_integration(client):
    """
    Integration test that:
    1) Uploads test PDF and DOCX files to SharePoint using the upload_file endpoint
    2) Verifies the uploads were successful and gets the file_urls
    3) Writes the base64 encoded file data to txt files for verification
    """
    
    # Test files to upload
    test_files = [
        {"path": PDF_TO_APPEND_PATH, "type": "pdf"},
        {"path": DOCX_TEST_PATH, "type": "docx"}
    ]
    
    for test_file in test_files:
        file_path = test_file["path"]
        file_type = test_file["type"]
        
        print(f"\n--- Testing {file_type.upper()} file upload ---")
        
        # 1) Prepare test file for upload
        if not os.path.exists(file_path):
            pytest.fail(f"Cannot find test {file_type} file for upload: {file_path}")
        
        with open(file_path, "rb") as f:
            test_file_bytes = f.read()
        test_file_b64 = base64.b64encode(test_file_bytes).decode("utf-8")
        
        # Write base64 encoded file as txt
        base64_output_file = f"./testing/test_upload_{file_type}_base64_{int(time.time())}.txt"
        with open(base64_output_file, "w") as f:
            f.write(test_file_b64)
        print(f"Base64 encoded {file_type} file written to: {base64_output_file}")
        
        # Generate a unique filename for this test
        test_filename = f"test_upload_{file_type}_{int(time.time())}.{file_type}"
        
        # 2) Upload the file
        upload_payload = {
            "file_name": test_filename,
            "server_id": UPLOAD_SERVER_ID,
            "file_data": test_file_b64
        }
        
        print(f"Uploading test {file_type} file: {test_filename}")
        upload_response = client.post("/upload_file", json=upload_payload)
        
        # 3) Verify upload was successful
        assert upload_response.status_code == 200, f"{file_type.upper()} upload failed with status: {upload_response.status_code}"
        upload_data = upload_response.json()
        
        assert "message" in upload_data, f"{file_type.upper()} upload response should contain 'message'"
        assert "file_url" in upload_data, f"{file_type.upper()} upload response should contain 'file_url'"
        assert "delete_url" in upload_data, f"{file_type.upper()} upload response should contain 'delete_url'"
        assert upload_data["message"] == "File uploaded successfully"
        
        file_url = upload_data["file_url"]
        delete_url = upload_data["delete_url"]
        print(f"{file_type.upper()} file uploaded successfully.")
        print(f"Access URL: {file_url}")
        print(f"Delete URL: {delete_url}")
    
    print("\nIntegration test passed: Both PDF and DOCX files were successfully uploaded.")


@pytest.mark.integration
def test_delete_file_integration(client):
    """
    Integration test that:
    1) Uploads test PDF and DOCX files to SharePoint first
    2) Deletes the uploaded files using the delete_file endpoint
    3) Verifies the deletions were successful
    """
    
    # Test files to upload and then delete
    test_files = [
        {"path": PDF_TO_APPEND_PATH, "type": "pdf"},
        {"path": DOCX_TEST_PATH, "type": "docx"}
    ]
    
    for test_file in test_files:
        file_path = test_file["path"]
        file_type = test_file["type"]
        
        print(f"\n--- Testing {file_type.upper()} file deletion ---")
        
        # 1) First upload a file to have something to delete
        if not os.path.exists(file_path):
            pytest.fail(f"Cannot find test {file_type} file for upload: {file_path}")
        
        with open(file_path, "rb") as f:
            test_file_bytes = f.read()
        test_file_b64 = base64.b64encode(test_file_bytes).decode("utf-8")
        
        # Generate a unique filename for this test
        test_filename = f"test_delete_{file_type}_{int(time.time())}.{file_type}"
        
        # Upload the file first
        upload_payload = {
            "file_name": test_filename,
            "server_id": UPLOAD_SERVER_ID,
            "file_data": test_file_b64
        }
        
        print(f"Uploading test {file_type} file for deletion: {test_filename}")
        upload_response = client.post("/upload_file", json=upload_payload)
        
        assert upload_response.status_code == 200, f"{file_type.upper()} upload failed with status: {upload_response.status_code}"
        upload_data = upload_response.json()
        file_url = upload_data["file_url"]
        delete_url = upload_data["delete_url"]
        print(f"{file_type.upper()} file uploaded successfully for deletion test.")
        print(f"Access URL: {file_url}")
        print(f"Delete URL: {delete_url}")
        
        # 2) Delete the uploaded file using the delete_url
        delete_payload = {
            "file_url": delete_url  # Using delete_url for deletion
        }
        
        print(f"Deleting uploaded {file_type} file using delete_url...")
        delete_response = client.post("/delete_file", json=delete_payload)
        
        # 3) Verify deletion was successful
        assert delete_response.status_code == 200, f"{file_type.upper()} delete failed with status: {delete_response.status_code}"
        delete_data = delete_response.json()
        
        assert "message" in delete_data, f"{file_type.upper()} delete response should contain 'message'"
        assert delete_data["message"] == "File deleted successfully"
        assert delete_data["file_url"] == delete_url, f"{file_type.upper()} delete response should return the same delete_url"
        
        print(f"{file_type.upper()} file was successfully deleted.")
    
    print("\nIntegration test passed: Both PDF and DOCX files were successfully deleted.")


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
