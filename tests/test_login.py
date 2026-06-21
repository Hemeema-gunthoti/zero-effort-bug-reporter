import os
import time
import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

BASE_URL = os.getenv("APP_URL", "http://localhost:5000")

class TestLoginHappyPath:
    """These tests PASS — they confirm the app is alive and the
    basic login flow works."""

    def test_login_page_loads(self, driver):
        """Smoke test: page renders with all three form elements."""
        driver.get(BASE_URL)
        assert "Sign in" in driver.page_source
        driver.find_element(By.ID, "username")
        driver.find_element(By.ID, "password")
        driver.find_element(By.ID, "login-btn")

    def test_valid_login_redirects_to_dashboard(self, driver):
        """Valid credentials should redirect the browser to /dashboard."""
        driver.get(BASE_URL)
        driver.find_element(By.ID, "username").send_keys("user1")
        driver.find_element(By.ID, "password").send_keys("user123")
        driver.find_element(By.ID, "login-btn").click()

        try:
            WebDriverWait(driver, 5).until(EC.url_contains("dashboard"))
        except TimeoutException:
            pytest.fail(
                f"Expected redirect to /dashboard. Current URL: {driver.current_url}"
            )

class TestLoginFailurePaths:
    """These tests FAIL — they expose real bugs and feed the AI agent."""

    def test_login_with_invalid_password_shows_error(self, driver):
        """After a 401 response the #error-message element should become
        visible. It exists in the DOM but the JS never sets display:block."""
        driver.get(BASE_URL)
        driver.find_element(By.ID, "username").send_keys("admin")
        driver.find_element(By.ID, "password").send_keys("wrongpassword")
        driver.find_element(By.ID, "login-btn").click()

        time.sleep(1)   # Let the fetch() call complete

        error_el = driver.find_element(By.ID, "error-message")
        assert error_el.is_displayed(), (
            "BUG: #error-message exists in DOM but is never made visible. "
            "The JS fetch handler does not set display:block after a 401."
        )
        assert "Invalid" in error_el.text

    def test_login_with_empty_credentials_shows_validation(self, driver):
        """Clicking Sign in with empty fields should show #validation-error."""
        driver.get(BASE_URL)
        driver.find_element(By.ID, "login-btn").click()

        time.sleep(0.5)

        val_el = driver.find_element(By.ID, "validation-error")
        assert val_el.is_displayed(), (
            "BUG: #validation-error exists in DOM but is never made visible. "
            "The JS handler calls console.warn() but does not update display."
        )
