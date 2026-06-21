import os
import pytest
from selenium.webdriver.common.by import By

BASE_URL = os.getenv("APP_URL", "http://localhost:5000")

class TestAPIEndpoints:

    def test_list_items_returns_all_products(self, driver):
        """GET /api/items should return all three products."""
        driver.get(f"{BASE_URL}/api/items")
        body = driver.find_element(By.TAG_NAME, "body").text
        assert "Product A" in body
        assert "Product B" in body
        assert "Product C" in body

    def test_get_known_item_returns_product(self, driver):
        """GET /api/items/1 should return Product A."""
        driver.get(f"{BASE_URL}/api/items/1")
        body = driver.find_element(By.TAG_NAME, "body").text
        assert "Product A" in body

    def test_api_returns_404_for_unknown_item(self, driver):
        """GET /api/items/999 — item does not exist."""
        driver.get(f"{BASE_URL}/api/items/999")
        body = driver.find_element(By.TAG_NAME, "body").text.lower()

        assert "internal server error" not in body, (
            "BUG: /api/items/999 raised an unhandled KeyError and returned "
            "a 500 Internal Server Error instead of a proper 404 response."
        )
        assert "not found" in body, (
            "Expected a 404 JSON response with 'not found' in the body."
        )

class TestDashboardAccess:

    def test_dashboard_requires_auth(self, driver):
        """Hitting /dashboard without a session returns 403."""
        driver.get(f"{BASE_URL}/dashboard")
        body = driver.find_element(By.TAG_NAME, "body").text
        assert "Unauthorized" in body or "403" in body
