"""Toston Financial Terminal App using Textual."""
from datetime import datetime, timedelta
from typing import List, Dict, Any
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Header,
    Footer,
    Static,
    DataTable,
    Button,
    Label,
    TabbedContent,
    TabPane,
)
from textual.reactive import reactive
from rich.text import Text
from api_client import TostonAPIClient


class BalanceCard(Static):
    """Widget to display account balance."""

    balance = reactive(0.0)
    label_text = reactive("")

    def __init__(self, label: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.label_text = label

    def render(self) -> Text:
        """Render the balance card."""
        color = "green" if self.balance >= 0 else "red"
        return Text.from_markup(
            f"[bold]{self.label_text}[/bold]\n"
            f"[{color} bold]${self.balance:,.2f}[/{color} bold]"
        )


class AccountsTable(Static):
    """Widget to display accounts."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accounts_data: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield DataTable(id="accounts_table")

    def on_mount(self) -> None:
        """Set up the table when mounted."""
        table = self.query_one("#accounts_table", DataTable)
        table.add_columns("Name", "Type", "Balance", "Color")
        table.cursor_type = "row"

    def update_data(self, accounts: List[Dict[str, Any]]) -> None:
        """Update the accounts table data."""
        self.accounts_data = accounts
        table = self.query_one("#accounts_table", DataTable)
        table.clear()

        for account in accounts:
            table.add_row(
                account.get("name", "N/A"),
                account.get("type", "N/A"),
                f"${account.get('current_balance', 0):,.2f}",
                account.get("color", "N/A"),
            )


class TransactionsTable(Static):
    """Widget to display transactions (expenses, incomes, transfers)."""

    def __init__(self, transaction_type: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.transaction_type = transaction_type
        self.transactions_data: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield DataTable(id=f"{self.transaction_type}_table")

    def on_mount(self) -> None:
        """Set up the table when mounted."""
        table = self.query_one(f"#{self.transaction_type}_table", DataTable)
        table.cursor_type = "row"

        if self.transaction_type == "expenses":
            table.add_columns("Date", "Description", "Amount", "Category", "Account")
        elif self.transaction_type == "incomes":
            table.add_columns("Date", "Description", "Amount", "Category", "Account")
        elif self.transaction_type == "transfers":
            table.add_columns("Date", "Description", "Amount", "From", "To")

    def update_data(self, transactions: List[Dict[str, Any]]) -> None:
        """Update the transactions table data."""
        self.transactions_data = transactions
        table = self.query_one(f"#{self.transaction_type}_table", DataTable)
        table.clear()

        for txn in transactions:
            if self.transaction_type == "transfers":
                table.add_row(
                    str(txn.get("date", "N/A")),
                    txn.get("description", "N/A"),
                    f"${txn.get('amount', 0):,.2f}",
                    str(txn.get("from_acc", "N/A")),
                    str(txn.get("to_acc", "N/A")),
                )
            else:
                table.add_row(
                    str(txn.get("date", "N/A")),
                    txn.get("description", "N/A"),
                    f"${txn.get('amount', 0):,.2f}",
                    str(txn.get("category_id", "N/A")),
                    str(txn.get("account_id", "N/A")),
                )


class TostonFinanceApp(App):
    """Toston Finance Terminal Application."""

    CSS = """
    Screen {
        background: $surface;
    }

    #balance_cards {
        height: auto;
        padding: 1;
        margin: 1;
    }

    BalanceCard {
        width: 1fr;
        height: 5;
        border: solid $primary;
        padding: 1;
        margin: 0 1;
        background: $panel;
        content-align: center middle;
    }

    #controls {
        height: auto;
        padding: 1;
        margin: 1;
    }

    Button {
        margin: 0 1;
    }

    DataTable {
        height: 100%;
    }

    TabbedContent {
        margin: 1;
    }

    #status {
        padding: 0 1;
        height: 3;
        background: $panel;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("d", "toggle_dark", "Toggle Dark"),
    ]

    def __init__(self):
        super().__init__()
        self.api_client = TostonAPIClient()
        self.total_balance = 0.0
        self.total_income = 0.0
        self.total_expenses = 0.0

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header(show_clock=True)

        with Horizontal(id="balance_cards"):
            yield BalanceCard("Total Balance", id="balance_total")
            yield BalanceCard("Total Income", id="balance_income")
            yield BalanceCard("Total Expenses", id="balance_expenses")

        with Horizontal(id="controls"):
            yield Button("Refresh All", id="btn_refresh", variant="primary")
            yield Button("This Month", id="btn_month", variant="default")
            yield Button("This Year", id="btn_year", variant="default")
            yield Static("[bold]Status:[/bold] Ready", id="status")

        with TabbedContent():
            with TabPane("Accounts"):
                yield AccountsTable(id="accounts_widget")

            with TabPane("Expenses"):
                yield TransactionsTable("expenses", id="expenses_widget")

            with TabPane("Incomes"):
                yield TransactionsTable("incomes", id="incomes_widget")

            with TabPane("Transfers"):
                yield TransactionsTable("transfers", id="transfers_widget")

        yield Footer()

    def on_mount(self) -> None:
        """Initialize the app when mounted."""
        self.set_interval(0.1, self.initial_load, repeat=1)

    def initial_load(self) -> None:
        """Load initial data."""
        self.update_status("Logging in...")
        if self.api_client.login():
            self.update_status("Connected")
            self.action_refresh()
        else:
            self.update_status("Login failed")

    def update_status(self, message: str) -> None:
        """Update status message."""
        status = self.query_one("#status", Static)
        status.update(f"[bold]Status:[/bold] {message}")

    def calculate_totals(
        self,
        accounts: List[Dict[str, Any]],
        incomes: List[Dict[str, Any]],
        expenses: List[Dict[str, Any]]
    ) -> None:
        """Calculate and update balance totals."""
        self.total_balance = sum(
            account.get("current_balance", 0) for account in accounts
        )
        self.total_income = sum(income.get("amount", 0) for income in incomes)
        self.total_expenses = sum(expense.get("amount", 0) for expense in expenses)

        balance_total = self.query_one("#balance_total", BalanceCard)
        balance_income = self.query_one("#balance_income", BalanceCard)
        balance_expenses = self.query_one("#balance_expenses", BalanceCard)

        balance_total.balance = self.total_balance
        balance_income.balance = self.total_income
        balance_expenses.balance = self.total_expenses

    def action_refresh(self) -> None:
        """Refresh all data from API."""
        self.update_status("Refreshing...")

        accounts = self.api_client.get_accounts()
        expenses = self.api_client.get_expenses()
        incomes = self.api_client.get_incomes()
        transfers = self.api_client.get_transfers()

        self.calculate_totals(accounts, incomes, expenses)

        accounts_widget = self.query_one("#accounts_widget", AccountsTable)
        accounts_widget.update_data(accounts)

        expenses_widget = self.query_one("#expenses_widget", TransactionsTable)
        expenses_widget.update_data(expenses)

        incomes_widget = self.query_one("#incomes_widget", TransactionsTable)
        incomes_widget.update_data(incomes)

        transfers_widget = self.query_one("#transfers_widget", TransactionsTable)
        transfers_widget.update_data(transfers)

        self.update_status(
            f"Loaded: {len(accounts)} accounts, "
            f"{len(expenses)} expenses, "
            f"{len(incomes)} incomes, "
            f"{len(transfers)} transfers"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "btn_refresh":
            self.action_refresh()
        elif event.button.id == "btn_month":
            self.load_month_data()
        elif event.button.id == "btn_year":
            self.load_year_data()

    def load_month_data(self) -> None:
        """Load data for current month."""
        self.update_status("Loading this month...")
        now = datetime.now()
        month_str = now.strftime("%Y-%m")

        accounts = self.api_client.get_accounts()
        expenses = self.api_client.get_expenses("month", month_str)
        incomes = self.api_client.get_incomes("month", month_str)
        transfers = self.api_client.get_transfers("month", month_str)

        self.calculate_totals(accounts, incomes, expenses)

        expenses_widget = self.query_one("#expenses_widget", TransactionsTable)
        expenses_widget.update_data(expenses)

        incomes_widget = self.query_one("#incomes_widget", TransactionsTable)
        incomes_widget.update_data(incomes)

        transfers_widget = self.query_one("#transfers_widget", TransactionsTable)
        transfers_widget.update_data(transfers)

        self.update_status(f"Month {month_str}: {len(expenses)} expenses, {len(incomes)} incomes")

    def load_year_data(self) -> None:
        """Load data for current year."""
        self.update_status("Loading this year...")
        now = datetime.now()
        year_str = str(now.year)

        accounts = self.api_client.get_accounts()
        expenses = self.api_client.get_expenses("year", year_str)
        incomes = self.api_client.get_incomes("year", year_str)
        transfers = self.api_client.get_transfers("year", year_str)

        self.calculate_totals(accounts, incomes, expenses)

        expenses_widget = self.query_one("#expenses_widget", TransactionsTable)
        expenses_widget.update_data(expenses)

        incomes_widget = self.query_one("#incomes_widget", TransactionsTable)
        incomes_widget.update_data(incomes)

        transfers_widget = self.query_one("#transfers_widget", TransactionsTable)
        transfers_widget.update_data(transfers)

        self.update_status(f"Year {year_str}: {len(expenses)} expenses, {len(incomes)} incomes")

    def action_toggle_dark(self) -> None:
        """Toggle dark mode."""
        self.dark = not self.dark

    def on_unmount(self) -> None:
        """Clean up when app closes."""
        self.api_client.close()


def main():
    """Run the Toston Finance App."""
    app = TostonFinanceApp()
    app.run()


if __name__ == "__main__":
    main()
