"""API client for Toston backend."""
import os
from typing import Any, Dict, List, Optional
import httpx
from dotenv import load_dotenv

load_dotenv()


class TostonAPIClient:
    """Client to interact with Toston API endpoints."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None
    ):
        self.base_url = base_url or os.getenv("API_BASE_URL", "http://localhost:8888")
        self.username = username or os.getenv("API_USERNAME", "admin")
        self.password = password or os.getenv("API_PASSWORD", "root")
        self.token: Optional[str] = None
        self.client = httpx.Client(timeout=30.0)

    def login(self) -> bool:
        """Authenticate and get access token."""
        try:
            response = self.client.post(
                f"{self.base_url}/api/v1/login/access-token",
                data={
                    "username": self.username,
                    "password": self.password
                }
            )
            response.raise_for_status()
            data = response.json()
            self.token = data.get("access_token")
            return True
        except Exception:
            return False

    def _get_headers(self) -> Dict[str, str]:
        """Get authorization headers."""
        if not self.token:
            self.login()
        return {"Authorization": f"Bearer {self.token}"}

    def get_accounts(self, skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all accounts."""
        try:
            response = self.client.get(
                f"{self.base_url}/api/v1/accounts",
                headers=self._get_headers(),
                params={"skip": skip, "limit": limit}
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            return []

    def get_expenses(
        self,
        date_filter_type: Optional[str] = None,
        date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get expenses, optionally filtered by date."""
        try:
            if date_filter_type and date:
                url = f"{self.base_url}/api/v1/expenses/{date_filter_type}/{date}"
            else:
                url = f"{self.base_url}/api/v1/expenses/getAll"

            response = self.client.get(
                url,
                headers=self._get_headers()
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            return []

    def get_incomes(
        self,
        date_filter_type: Optional[str] = None,
        date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get incomes, optionally filtered by date."""
        try:
            if date_filter_type and date:
                url = f"{self.base_url}/api/v1/incomes/{date_filter_type}/{date}"
            else:
                url = f"{self.base_url}/api/v1/incomes/getAll"

            response = self.client.get(
                url,
                headers=self._get_headers()
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            return []

    def get_transfers(
        self,
        date_filter_type: Optional[str] = None,
        date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get transfers, optionally filtered by date."""
        try:
            if date_filter_type and date:
                url = f"{self.base_url}/api/v1/transfers/{date_filter_type}/{date}"
            else:
                url = f"{self.base_url}/api/v1/transfers/getAll"

            response = self.client.get(
                url,
                headers=self._get_headers()
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            return []

    def get_categories(self, skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all categories."""
        try:
            response = self.client.get(
                f"{self.base_url}/api/v1/categories",
                headers=self._get_headers(),
                params={"skip": skip, "limit": limit}
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            return []

    def get_user_info(self) -> Optional[Dict[str, Any]]:
        """Get current user information."""
        try:
            response = self.client.get(
                f"{self.base_url}/api/v1/users/me",
                headers=self._get_headers()
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

    def create_account(self, account_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a new account."""
        try:
            response = self.client.post(
                f"{self.base_url}/api/v1/accounts",
                headers=self._get_headers(),
                json=account_data
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error creating account: {e}")
            return None

    def create_expense(self, expense_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a new expense."""
        try:
            response = self.client.post(
                f"{self.base_url}/api/v1/expenses",
                headers=self._get_headers(),
                json=expense_data
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error creating expense: {e}")
            return None

    def create_income(self, income_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a new income."""
        try:
            response = self.client.post(
                f"{self.base_url}/api/v1/incomes",
                headers=self._get_headers(),
                json=income_data
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error creating income: {e}")
            return None

    def create_transfer(self, transfer_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a new transfer."""
        try:
            response = self.client.post(
                f"{self.base_url}/api/v1/transfers",
                headers=self._get_headers(),
                json=transfer_data
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error creating transfer: {e}")
            return None

    def close(self):
        """Close the HTTP client."""
        self.client.close()
