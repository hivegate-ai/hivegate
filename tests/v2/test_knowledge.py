import unittest
import uuid
from datetime import datetime
from unittest.mock import patch

from db.db_models import KnowledgeEntryDB
from tests.test_utils import create_test_client


class TestV2KnowledgeAPI(unittest.TestCase):
    """Test V2 knowledge API endpoints."""

    def setUp(self):
        """Set up test fixtures."""
        self.client, self.app = create_test_client()
        # Manually register routers for testing
        from api.routes.v2_router import get_v2_router

        self.app.include_router(get_v2_router())

        # Recreate client with the updated app
        from fastapi.testclient import TestClient

        self.client = TestClient(self.app)

        self.test_tenant_id = "test-tenant-123"
        self.test_collection_id = "test-collection-456"
        self.test_file_id = str(uuid.uuid4())
        self.test_knowledge_id = str(uuid.uuid4())

        self.sample_knowledge_entry = KnowledgeEntryDB(
            id=uuid.uuid4(),
            tenant_id=self.test_tenant_id,
            collection_id=None,
            file_id=uuid.UUID(self.test_file_id),
            original_filename="test-document.pdf",
            file_type="company",
            content_type="application/pdf",
            gcs_path="files/company/test-document.pdf",
            status="active",
            knowledge_status="indexing",
            entry_metadata={"tags": ["test", "document"]},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        self.sample_create_request = {
            "file_id": self.test_file_id,
            "original_filename": "test-document.pdf",
            "file_type": "company",
            "content_type": "application/pdf",
            "gcs_path": "files/company/test-document.pdf",
            "status": "active",
            "metadata": {"tags": ["test", "document"]},
        }

    # Organization Knowledge Tests

    @patch("api.routes.v2.knowledge.create_knowledge_entry")
    def test_create_tenant_knowledge_success(self, mock_create):
        """Test successful creation of tenant knowledge entry."""
        # Mock the CRUD function
        mock_create.return_value = self.sample_knowledge_entry

        response = self.client.post(f"/v2/knowledge/{self.test_tenant_id}", json=self.sample_create_request)

        # Assertions
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["tenant_id"], self.test_tenant_id)
        self.assertIsNone(data["collection_id"])
        self.assertEqual(data["file_id"], self.test_file_id)
        self.assertEqual(data["status"], "active")
        self.assertEqual(data["knowledge_status"], "indexing")  # Always starts with indexing
        self.assertEqual(
            data["message"],
            "File added to tenant knowledge base. Knowledge indexing is processing asynchronously.",
        )

        # Verify CRUD function was called correctly
        mock_create.assert_called_once()
        args, kwargs = mock_create.call_args
        self.assertEqual(kwargs["tenant_id"], self.test_tenant_id)
        self.assertEqual(kwargs["file_id"], self.test_file_id)
        self.assertIsNone(kwargs["collection_id"])

    def test_create_tenant_knowledge_invalid_file_id(self):
        """Test creation with invalid file_id format."""
        invalid_request = self.sample_create_request.copy()
        invalid_request["file_id"] = "invalid-uuid"

        response = self.client.post(f"/v2/knowledge/{self.test_tenant_id}", json=invalid_request)

        # Assertions
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("Invalid file_id format", data["detail"])

    def test_create_tenant_knowledge_invalid_file_type(self):
        """Test creation with invalid file_type."""
        invalid_request = self.sample_create_request.copy()
        invalid_request["file_type"] = "invalid_type"

        response = self.client.post(f"/v2/knowledge/{self.test_tenant_id}", json=invalid_request)

        # Assertions
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("file_type must be either 'company' or 'project'", data["detail"])

    @patch("api.routes.v2.knowledge.create_knowledge_entry")
    def test_create_tenant_knowledge_duplicate_file(self, mock_create):
        """Test creation with duplicate file_id."""
        # Mock the CRUD function to raise ValueError
        mock_create.side_effect = ValueError(f"Knowledge entry with file_id {self.test_file_id} already exists")

        response = self.client.post(f"/v2/knowledge/{self.test_tenant_id}", json=self.sample_create_request)

        # Assertions
        self.assertEqual(response.status_code, 409)
        data = response.json()
        self.assertIn("already exists", data["detail"])

    @patch("api.routes.v2.knowledge.get_knowledge_entries")
    def test_list_tenant_knowledge_success(self, mock_get_entries):
        """Test successful listing of tenant knowledge entries."""
        # Mock the CRUD function
        mock_entries = [self.sample_knowledge_entry]
        mock_get_entries.return_value = (mock_entries, 1)

        response = self.client.get(f"/v2/knowledge/{self.test_tenant_id}")

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("files", data)
        self.assertIn("pagination", data)
        self.assertEqual(len(data["files"]), 1)
        self.assertEqual(data["pagination"]["total"], 1)
        self.assertEqual(data["pagination"]["limit"], 50)
        self.assertEqual(data["pagination"]["offset"], 0)
        self.assertFalse(data["pagination"]["has_more"])

        # Verify CRUD function was called correctly
        mock_get_entries.assert_called_once()
        args, kwargs = mock_get_entries.call_args
        self.assertEqual(kwargs["tenant_id"], self.test_tenant_id)
        self.assertIsNone(kwargs["collection_id"])
        self.assertEqual(kwargs["limit"], 50)
        self.assertEqual(kwargs["offset"], 0)

    @patch("api.routes.v2.knowledge.get_knowledge_entries")
    def test_list_tenant_knowledge_with_pagination(self, mock_get_entries):
        """Test listing with custom pagination parameters."""
        # Mock the CRUD function
        mock_entries: list[KnowledgeEntryDB] = []
        mock_get_entries.return_value = (mock_entries, 100)

        response = self.client.get(f"/v2/knowledge/{self.test_tenant_id}?limit=25&offset=50")

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["pagination"]["limit"], 25)
        self.assertEqual(data["pagination"]["offset"], 50)
        self.assertEqual(data["pagination"]["total"], 100)
        self.assertTrue(data["pagination"]["has_more"])

    @patch("api.routes.v2.knowledge.get_knowledge_entry")
    def test_get_tenant_knowledge_file_success(self, mock_get_entry):
        """Test successful retrieval of specific tenant knowledge entry."""
        # Mock the CRUD function
        mock_get_entry.return_value = self.sample_knowledge_entry

        response = self.client.get(f"/v2/knowledge/{self.test_tenant_id}/files/{self.test_file_id}")

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["tenant_id"], self.test_tenant_id)
        self.assertEqual(data["file_id"], self.test_file_id)
        self.assertIsNone(data["collection_id"])

        # Verify CRUD function was called correctly
        mock_get_entry.assert_called_once()
        args, kwargs = mock_get_entry.call_args
        self.assertEqual(kwargs["tenant_id"], self.test_tenant_id)
        self.assertEqual(kwargs["file_id"], self.test_file_id)
        self.assertIsNone(kwargs["collection_id"])

    @patch("api.routes.v2.knowledge.get_knowledge_entry")
    def test_get_tenant_knowledge_file_not_found(self, mock_get_entry):
        """Test retrieval of non-existent knowledge entry."""
        # Mock the CRUD function to return None
        mock_get_entry.return_value = None

        response = self.client.get(f"/v2/knowledge/{self.test_tenant_id}/files/{self.test_file_id}")

        # Assertions
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("Knowledge entry not found", data["detail"])

    def test_get_tenant_knowledge_file_invalid_file_id(self):
        """Test retrieval with invalid file_id format."""
        response = self.client.get(f"/v2/knowledge/{self.test_tenant_id}/files/invalid-uuid")

        # Assertions
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("Invalid file_id format", data["detail"])

    @patch("api.routes.v2.knowledge.update_knowledge_entry")
    def test_update_tenant_knowledge_file_success(self, mock_update):
        """Test successful update of tenant knowledge entry."""
        # Mock the CRUD function
        updated_entry = self.sample_knowledge_entry
        updated_entry.status = "archived"  # type: ignore[assignment]
        mock_update.return_value = updated_entry

        update_request = {"status": "archived", "knowledge_status": "outdated"}

        response = self.client.patch(
            f"/v2/knowledge/{self.test_tenant_id}/files/{self.test_file_id}", json=update_request
        )

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "archived")

        # Verify CRUD function was called correctly
        mock_update.assert_called_once()
        args, kwargs = mock_update.call_args
        self.assertEqual(kwargs["tenant_id"], self.test_tenant_id)
        self.assertEqual(kwargs["file_id"], self.test_file_id)
        self.assertIsNone(kwargs["collection_id"])
        self.assertEqual(kwargs["updates"]["status"], "archived")
        self.assertEqual(kwargs["updates"]["knowledge_status"], "outdated")

    @patch("api.routes.v2.knowledge.update_knowledge_entry")
    def test_update_tenant_knowledge_file_not_found(self, mock_update):
        """Test update of non-existent knowledge entry."""
        # Mock the CRUD function to return None
        mock_update.return_value = None

        update_request = {"status": "archived"}

        response = self.client.patch(
            f"/v2/knowledge/{self.test_tenant_id}/files/{self.test_file_id}", json=update_request
        )

        # Assertions
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("Knowledge entry not found", data["detail"])

    def test_update_tenant_knowledge_file_no_fields(self):
        """Test update with no valid fields provided."""
        response = self.client.patch(f"/v2/knowledge/{self.test_tenant_id}/files/{self.test_file_id}", json={})

        # Assertions
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("No valid fields provided for update", data["detail"])

    @patch("api.routes.v2.knowledge.delete_knowledge_entry")
    def test_delete_tenant_knowledge_file_soft_delete(self, mock_delete):
        """Test soft deletion of tenant knowledge entry."""
        # Mock the CRUD function
        mock_delete.return_value = True

        response = self.client.delete(f"/v2/knowledge/{self.test_tenant_id}/files/{self.test_file_id}")

        # Assertions
        self.assertEqual(response.status_code, 204)

        # Verify CRUD function was called correctly
        mock_delete.assert_called_once()
        args, kwargs = mock_delete.call_args
        self.assertEqual(kwargs["tenant_id"], self.test_tenant_id)
        self.assertEqual(kwargs["file_id"], self.test_file_id)
        self.assertIsNone(kwargs["collection_id"])
        self.assertFalse(kwargs["hard_delete"])

    @patch("api.routes.v2.knowledge.delete_knowledge_entry")
    def test_delete_tenant_knowledge_file_hard_delete(self, mock_delete):
        """Test hard deletion of tenant knowledge entry."""
        # Mock the CRUD function
        mock_delete.return_value = True

        response = self.client.delete(f"/v2/knowledge/{self.test_tenant_id}/files/{self.test_file_id}?hard_delete=true")

        # Assertions
        self.assertEqual(response.status_code, 204)

        # Verify CRUD function was called correctly
        mock_delete.assert_called_once()
        args, kwargs = mock_delete.call_args
        self.assertTrue(kwargs["hard_delete"])

    @patch("api.routes.v2.knowledge.delete_knowledge_entry")
    def test_delete_tenant_knowledge_file_not_found(self, mock_delete):
        """Test deletion of non-existent knowledge entry."""
        # Mock the CRUD function to return False
        mock_delete.return_value = False

        response = self.client.delete(f"/v2/knowledge/{self.test_tenant_id}/files/{self.test_file_id}")

        # Assertions
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("Knowledge entry not found", data["detail"])

    # Collection Knowledge Tests

    @patch("api.routes.v2.knowledge.create_knowledge_entry")
    def test_create_collection_knowledge_success(self, mock_create):
        """Test successful creation of collection knowledge entry."""
        # Create collection-level entry
        project_entry = self.sample_knowledge_entry
        project_entry.collection_id = self.test_collection_id  # type: ignore[assignment]
        project_entry.file_type = "project"  # type: ignore[assignment]
        mock_create.return_value = project_entry

        project_request = self.sample_create_request.copy()
        project_request["file_type"] = "project"

        response = self.client.post(
            f"/v2/knowledge/{self.test_tenant_id}/{self.test_collection_id}", json=project_request
        )

        # Assertions
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["tenant_id"], self.test_tenant_id)
        self.assertEqual(data["collection_id"], self.test_collection_id)
        self.assertEqual(
            data["message"], "File added to collection knowledge base. Knowledge indexing is processing asynchronously."
        )

        # Verify CRUD function was called correctly
        mock_create.assert_called_once()
        args, kwargs = mock_create.call_args
        self.assertEqual(kwargs["tenant_id"], self.test_tenant_id)
        self.assertEqual(kwargs["collection_id"], self.test_collection_id)

    @patch("api.routes.v2.knowledge.get_knowledge_entries")
    def test_list_collection_knowledge_success(self, mock_get_entries):
        """Test successful listing of collection knowledge entries."""
        # Mock the CRUD function
        project_entry = self.sample_knowledge_entry
        project_entry.collection_id = self.test_collection_id  # type: ignore[assignment]
        mock_entries = [project_entry]
        mock_get_entries.return_value = (mock_entries, 1)

        response = self.client.get(f"/v2/knowledge/{self.test_tenant_id}/{self.test_collection_id}")

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["files"]), 1)

        # Verify CRUD function was called correctly
        mock_get_entries.assert_called_once()
        args, kwargs = mock_get_entries.call_args
        self.assertEqual(kwargs["tenant_id"], self.test_tenant_id)
        self.assertEqual(kwargs["collection_id"], self.test_collection_id)

    @patch("api.routes.v2.knowledge.get_knowledge_entry")
    def test_get_collection_knowledge_file_success(self, mock_get_entry):
        """Test successful retrieval of specific collection knowledge entry."""
        # Mock the CRUD function
        project_entry = self.sample_knowledge_entry
        project_entry.collection_id = self.test_collection_id  # type: ignore[assignment]
        mock_get_entry.return_value = project_entry

        response = self.client.get(
            f"/v2/knowledge/{self.test_tenant_id}/{self.test_collection_id}/files/{self.test_file_id}"
        )

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["tenant_id"], self.test_tenant_id)
        self.assertEqual(data["collection_id"], self.test_collection_id)

        # Verify CRUD function was called correctly
        mock_get_entry.assert_called_once()
        args, kwargs = mock_get_entry.call_args
        self.assertEqual(kwargs["tenant_id"], self.test_tenant_id)
        self.assertEqual(kwargs["collection_id"], self.test_collection_id)
        self.assertEqual(kwargs["file_id"], self.test_file_id)

    @patch("api.routes.v2.knowledge.update_knowledge_entry")
    def test_update_collection_knowledge_file_success(self, mock_update):
        """Test successful update of collection knowledge entry."""
        # Mock the CRUD function
        project_entry = self.sample_knowledge_entry
        project_entry.collection_id = self.test_collection_id  # type: ignore[assignment]
        project_entry.status = "archived"  # type: ignore[assignment]
        mock_update.return_value = project_entry

        update_request = {"status": "archived"}

        response = self.client.patch(
            f"/v2/knowledge/{self.test_tenant_id}/{self.test_collection_id}/files/{self.test_file_id}",
            json=update_request,
        )

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "archived")

        # Verify CRUD function was called correctly
        mock_update.assert_called_once()
        args, kwargs = mock_update.call_args
        self.assertEqual(kwargs["tenant_id"], self.test_tenant_id)
        self.assertEqual(kwargs["collection_id"], self.test_collection_id)

    @patch("api.routes.v2.knowledge.delete_knowledge_entry")
    def test_delete_collection_knowledge_file_success(self, mock_delete):
        """Test successful deletion of collection knowledge entry."""
        # Mock the CRUD function
        mock_delete.return_value = True

        response = self.client.delete(
            f"/v2/knowledge/{self.test_tenant_id}/{self.test_collection_id}/files/{self.test_file_id}"
        )

        # Assertions
        self.assertEqual(response.status_code, 204)

        # Verify CRUD function was called correctly
        mock_delete.assert_called_once()
        args, kwargs = mock_delete.call_args
        self.assertEqual(kwargs["tenant_id"], self.test_tenant_id)
        self.assertEqual(kwargs["collection_id"], self.test_collection_id)


class TestV2KnowledgeAPIExtended(unittest.TestCase):
    """Extended V2 Knowledge API tests (KB-004 to KB-050)."""

    def setUp(self):
        """Set up test fixtures."""
        self.client, self.app = create_test_client()
        from api.routes.v2_router import get_v2_router

        self.app.include_router(get_v2_router())

        from fastapi.testclient import TestClient

        self.client = TestClient(self.app)

        self.test_tenant_id = "test-tenant-kb"
        self.test_collection_id = "test-collection-kb"
        self.test_file_id = str(uuid.uuid4())

    @patch("api.routes.v2.knowledge.create_knowledge_entry")
    def test_kb_004_create_with_metadata(self, mock_create):
        """KB-004: Create knowledge entry with metadata."""
        knowledge_id = uuid.uuid4()
        mock_entry = KnowledgeEntryDB(
            id=knowledge_id,
            tenant_id=self.test_tenant_id,
            collection_id=None,
            file_id=uuid.UUID(self.test_file_id),
            original_filename="test-doc.pdf",
            file_type="company",
            content_type="application/pdf",
            gcs_path="files/test-doc.pdf",
            status="active",
            knowledge_status="indexing",
            entry_metadata={"tags": ["important", "quarterly"], "department": "finance", "year": 2025},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        mock_create.return_value = mock_entry

        request = {
            "file_id": self.test_file_id,
            "original_filename": "test-doc.pdf",
            "file_type": "company",
            "content_type": "application/pdf",
            "gcs_path": "files/test-doc.pdf",
            "status": "active",
            "metadata": {"tags": ["important", "quarterly"], "department": "finance", "year": 2025},
        }

        response = self.client.post(f"/v2/knowledge/{self.test_tenant_id}", json=request)

        self.assertEqual(response.status_code, 201)

        # Verify metadata was passed to CRUD
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs["metadata"]["department"], "finance")
        self.assertEqual(call_kwargs["metadata"]["year"], 2025)
        self.assertIn("important", call_kwargs["metadata"]["tags"])

    @patch("api.routes.v2.knowledge.update_knowledge_entry")
    def test_kb_031_update_metadata(self, mock_update):
        """KB-031: Update knowledge entry metadata."""
        knowledge_id = uuid.uuid4()
        updated_entry = KnowledgeEntryDB(
            id=knowledge_id,
            tenant_id=self.test_tenant_id,
            collection_id=None,
            file_id=uuid.UUID(self.test_file_id),
            original_filename="test-doc.pdf",
            file_type="company",
            content_type="application/pdf",
            gcs_path="files/test-doc.pdf",
            status="active",
            knowledge_status="indexed",
            entry_metadata={"tags": ["updated"], "reviewed": True},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        mock_update.return_value = updated_entry

        update_request = {"metadata": {"tags": ["updated"], "reviewed": True}}

        response = self.client.patch(
            f"/v2/knowledge/{self.test_tenant_id}/files/{self.test_file_id}", json=update_request
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["metadata"]["reviewed"], True)
        self.assertIn("updated", data["metadata"]["tags"])

        # Verify metadata was passed to CRUD (as entry_metadata)
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args[1]
        self.assertIn("entry_metadata", call_kwargs["updates"])
        self.assertEqual(call_kwargs["updates"]["entry_metadata"]["reviewed"], True)

    @patch("api.routes.v2.knowledge.get_knowledge_entries")
    def test_kb_022_list_empty_results(self, mock_get_entries):
        """KB-022: List knowledge entries with no results."""
        mock_get_entries.return_value = ([], 0)

        response = self.client.get(f"/v2/knowledge/{self.test_tenant_id}")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["files"]), 0)
        self.assertEqual(data["pagination"]["total"], 0)
        self.assertFalse(data["pagination"]["has_more"])

    @patch("api.routes.v2.knowledge.create_knowledge_entry")
    def test_kb_040_create_database_error(self, mock_create):
        """KB-040: Create knowledge entry with database error."""
        mock_create.side_effect = Exception("Database connection failed")

        request = {
            "file_id": self.test_file_id,
            "original_filename": "test-doc.pdf",
            "file_type": "company",
            "content_type": "application/pdf",
            "gcs_path": "files/test-doc.pdf",
        }

        response = self.client.post(f"/v2/knowledge/{self.test_tenant_id}", json=request)

        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertIn("Failed to create", data["detail"])

    @patch("api.routes.v2.knowledge.delete_knowledge_entry")
    def test_kb_041_delete_hard_delete_success(self, mock_delete):
        """KB-041: Hard delete removes entry permanently."""
        mock_delete.return_value = True

        response = self.client.delete(f"/v2/knowledge/{self.test_tenant_id}/files/{self.test_file_id}?hard_delete=true")

        self.assertEqual(response.status_code, 204)

        # Verify hard_delete flag was passed
        mock_delete.assert_called_once()
        call_kwargs = mock_delete.call_args[1]
        self.assertTrue(call_kwargs["hard_delete"])

    @patch("api.routes.v2.knowledge.get_knowledge_entries")
    def test_kb_023_list_pagination_last_page(self, mock_get_entries):
        """KB-023: List knowledge with pagination at last page."""
        # Total of 55 entries, requesting offset 50 with limit 50
        # Should return 5 entries and has_more=False
        entries = []
        for i in range(5):
            entries.append(
                KnowledgeEntryDB(
                    id=uuid.uuid4(),
                    tenant_id=self.test_tenant_id,
                    collection_id=None,
                    file_id=uuid.uuid4(),
                    original_filename=f"doc-{i}.pdf",
                    file_type="company",
                    content_type="application/pdf",
                    gcs_path=f"files/doc-{i}.pdf",
                    status="active",
                    knowledge_status="indexed",
                    entry_metadata={},
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
        mock_get_entries.return_value = (entries, 55)

        response = self.client.get(f"/v2/knowledge/{self.test_tenant_id}?limit=50&offset=50")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["pagination"]["total"], 55)
        self.assertEqual(data["pagination"]["offset"], 50)
        self.assertEqual(data["pagination"]["limit"], 50)
        self.assertFalse(data["pagination"]["has_more"])  # 50 + 50 >= 55

    @patch("api.routes.v2.knowledge.get_knowledge_entry")
    def test_kb_050_get_entry_with_full_metadata(self, mock_get_entry):
        """KB-050: Get knowledge entry returns full metadata."""
        knowledge_id = uuid.uuid4()
        entry = KnowledgeEntryDB(
            id=knowledge_id,
            tenant_id=self.test_tenant_id,
            collection_id=None,
            file_id=uuid.UUID(self.test_file_id),
            original_filename="full-doc.pdf",
            file_type="company",
            content_type="application/pdf",
            gcs_path="files/full-doc.pdf",
            status="active",
            knowledge_status="indexed",
            entry_metadata={
                "tags": ["important", "q4"],
                "department": "finance",
                "year": 2025,
                "indexed_pages": 45,
                "custom_field": "value",
            },
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        mock_get_entry.return_value = entry

        response = self.client.get(f"/v2/knowledge/{self.test_tenant_id}/files/{self.test_file_id}")

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Verify all metadata is returned
        self.assertIn("metadata", data)
        self.assertEqual(data["metadata"]["department"], "finance")
        self.assertEqual(data["metadata"]["year"], 2025)
        self.assertEqual(data["metadata"]["indexed_pages"], 45)
        self.assertEqual(data["metadata"]["custom_field"], "value")


if __name__ == "__main__":
    unittest.main()
