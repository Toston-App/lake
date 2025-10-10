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
    Input,
    Select,
)
from textual.screen import ModalScreen
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


class CreateAccountScreen(ModalScreen):
    """Screen for creating a new account."""

    def compose(self) -> ComposeResult:
        with Vertical(id="create_account_dialog"):
            yield Static("[bold]Create New Account[/bold]", id="dialog_title")
            yield Input(placeholder="Account Name", id="input_account_name")
            yield Input(placeholder="Account Type (e.g., checking, savings)", id="input_account_type")
            yield Input(placeholder="Initial Balance", id="input_account_balance")
            yield Input(placeholder="Color (e.g., #FF5733)", id="input_account_color")
            with Horizontal(id="dialog_buttons"):
                yield Button("Create", variant="primary", id="btn_create_account")
                yield Button("Cancel", variant="default", id="btn_cancel_account")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_create_account":
            name = self.query_one("#input_account_name", Input).value
            acc_type = self.query_one("#input_account_type", Input).value
            balance = self.query_one("#input_account_balance", Input).value
            color = self.query_one("#input_account_color", Input).value

            self.dismiss({
                "name": name,
                "type": acc_type,
                "current_balance": float(balance) if balance else 0.0,
                "color": color
            })
        else:
            self.dismiss(None)


class CreateExpenseScreen(ModalScreen):
    """Screen for creating a new expense."""

    def __init__(self, accounts: List[Dict], categories: List[Dict], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accounts = accounts
        self.categories = categories

    def compose(self) -> ComposeResult:
        with Vertical(id="create_expense_dialog"):
            yield Static("[bold]Create New Expense[/bold]", id="dialog_title")
            yield Input(placeholder="Description", id="input_expense_description")
            yield Input(placeholder="Amount", id="input_expense_amount")
            yield Input(placeholder="Date (YYYY-MM-DD)", id="input_expense_date")
            yield Input(placeholder="Account ID", id="input_expense_account")
            yield Input(placeholder="Category ID", id="input_expense_category")
            with Horizontal(id="dialog_buttons"):
                yield Button("Create", variant="primary", id="btn_create_expense")
                yield Button("Cancel", variant="default", id="btn_cancel_expense")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_create_expense":
            description = self.query_one("#input_expense_description", Input).value
            amount = self.query_one("#input_expense_amount", Input).value
            date = self.query_one("#input_expense_date", Input).value
            account_id = self.query_one("#input_expense_account", Input).value
            category_id = self.query_one("#input_expense_category", Input).value

            self.dismiss({
                "description": description,
                "amount": float(amount) if amount else 0.0,
                "date": date or datetime.now().strftime("%Y-%m-%d"),
                "account_id": int(account_id) if account_id else None,
                "category_id": int(category_id) if category_id else None
            })
        else:
            self.dismiss(None)


class CreateIncomeScreen(ModalScreen):
    """Screen for creating a new income."""

    def __init__(self, accounts: List[Dict], categories: List[Dict], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accounts = accounts
        self.categories = categories

    def compose(self) -> ComposeResult:
        with Vertical(id="create_income_dialog"):
            yield Static("[bold]Create New Income[/bold]", id="dialog_title")
            yield Input(placeholder="Description", id="input_income_description")
            yield Input(placeholder="Amount", id="input_income_amount")
            yield Input(placeholder="Date (YYYY-MM-DD)", id="input_income_date")
            yield Input(placeholder="Account ID", id="input_income_account")
            yield Input(placeholder="Category ID", id="input_income_category")
            with Horizontal(id="dialog_buttons"):
                yield Button("Create", variant="primary", id="btn_create_income")
                yield Button("Cancel", variant="default", id="btn_cancel_income")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_create_income":
            description = self.query_one("#input_income_description", Input).value
            amount = self.query_one("#input_income_amount", Input).value
            date = self.query_one("#input_income_date", Input).value
            account_id = self.query_one("#input_income_account", Input).value
            category_id = self.query_one("#input_income_category", Input).value

            self.dismiss({
                "description": description,
                "amount": float(amount) if amount else 0.0,
                "date": date or datetime.now().strftime("%Y-%m-%d"),
                "account_id": int(account_id) if account_id else None,
                "category_id": int(category_id) if category_id else None
            })
        else:
            self.dismiss(None)


class CreateTransferScreen(ModalScreen):
    """Screen for creating a new transfer."""

    def __init__(self, accounts: List[Dict], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accounts = accounts

    def compose(self) -> ComposeResult:
        with Vertical(id="create_transfer_dialog"):
            yield Static("[bold]Create New Transfer[/bold]", id="dialog_title")
            yield Input(placeholder="Description", id="input_transfer_description")
            yield Input(placeholder="Amount", id="input_transfer_amount")
            yield Input(placeholder="Date (YYYY-MM-DD)", id="input_transfer_date")
            yield Input(placeholder="From Account ID", id="input_transfer_from")
            yield Input(placeholder="To Account ID", id="input_transfer_to")
            with Horizontal(id="dialog_buttons"):
                yield Button("Create", variant="primary", id="btn_create_transfer")
                yield Button("Cancel", variant="default", id="btn_cancel_transfer")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_create_transfer":
            description = self.query_one("#input_transfer_description", Input).value
            amount = self.query_one("#input_transfer_amount", Input).value
            date = self.query_one("#input_transfer_date", Input).value
            from_acc = self.query_one("#input_transfer_from", Input).value
            to_acc = self.query_one("#input_transfer_to", Input).value

            self.dismiss({
                "description": description,
                "amount": float(amount) if amount else 0.0,
                "date": date or datetime.now().strftime("%Y-%m-%d"),
                "from_acc": int(from_acc) if from_acc else None,
                "to_acc": int(to_acc) if to_acc else None
            })
        else:
            self.dismiss(None)


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

    ModalScreen {
        align: center middle;
    }

    #create_account_dialog, #create_expense_dialog, #create_income_dialog, #create_transfer_dialog {
        width: 60;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1;
    }

    #dialog_title {
        dock: top;
        height: 3;
        content-align: center middle;
    }

    Input {
        margin: 1 0;
    }

    #dialog_buttons {
        height: auto;
        align: center middle;
        margin: 1 0;
    }

    .create_button {
        margin: 0 1;
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
        self.accounts = []
        self.categories = []

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
                with Vertical():
                    with Horizontal():
                        yield Button("➕ New Account", id="btn_new_account", variant="success", classes="create_button")
                    yield AccountsTable(id="accounts_widget")

            with TabPane("Expenses"):
                with Vertical():
                    with Horizontal():
                        yield Button("➕ New Expense", id="btn_new_expense", variant="success", classes="create_button")
                    yield TransactionsTable("expenses", id="expenses_widget")

            with TabPane("Incomes"):
                with Vertical():
                    with Horizontal():
                        yield Button("➕ New Income", id="btn_new_income", variant="success", classes="create_button")
                    yield TransactionsTable("incomes", id="incomes_widget")

            with TabPane("Transfers"):
                with Vertical():
                    with Horizontal():
                        yield Button("➕ New Transfer", id="btn_new_transfer", variant="success", classes="create_button")
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

        self.accounts = self.api_client.get_accounts()
        self.categories = self.api_client.get_categories()
        expenses = self.api_client.get_expenses()
        incomes = self.api_client.get_incomes()
        transfers = self.api_client.get_transfers()

        self.calculate_totals(self.accounts, incomes, expenses)

        accounts_widget = self.query_one("#accounts_widget", AccountsTable)
        accounts_widget.update_data(self.accounts)

        expenses_widget = self.query_one("#expenses_widget", TransactionsTable)
        expenses_widget.update_data(expenses)

        incomes_widget = self.query_one("#incomes_widget", TransactionsTable)
        incomes_widget.update_data(incomes)

        transfers_widget = self.query_one("#transfers_widget", TransactionsTable)
        transfers_widget.update_data(transfers)

        self.update_status(
            f"Loaded: {len(self.accounts)} accounts, "
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
        elif event.button.id == "btn_new_account":
            self.create_new_account()
        elif event.button.id == "btn_new_expense":
            self.create_new_expense()
        elif event.button.id == "btn_new_income":
            self.create_new_income()
        elif event.button.id == "btn_new_transfer":
            self.create_new_transfer()

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

    def create_new_account(self) -> None:
        """Show dialog to create a new account."""
        def handle_result(result):
            if result:
                self.update_status("Creating account...")
                created = self.api_client.create_account(result)
                if created:
                    self.update_status("Account created successfully!")
                    self.action_refresh()
                else:
                    self.update_status("Failed to create account")

        self.push_screen(CreateAccountScreen(), handle_result)

    def create_new_expense(self) -> None:
        """Show dialog to create a new expense."""
        def handle_result(result):
            if result:
                self.update_status("Creating expense...")
                created = self.api_client.create_expense(result)
                if created:
                    self.update_status("Expense created successfully!")
                    self.action_refresh()
                else:
                    self.update_status("Failed to create expense")

        self.push_screen(CreateExpenseScreen(self.accounts, self.categories), handle_result)

    def create_new_income(self) -> None:
        """Show dialog to create a new income."""
        def handle_result(result):
            if result:
                self.update_status("Creating income...")
                created = self.api_client.create_income(result)
                if created:
                    self.update_status("Income created successfully!")
                    self.action_refresh()
                else:
                    self.update_status("Failed to create income")

        self.push_screen(CreateIncomeScreen(self.accounts, self.categories), handle_result)

    def create_new_transfer(self) -> None:
        """Show dialog to create a new transfer."""
        def handle_result(result):
            if result:
                self.update_status("Creating transfer...")
                created = self.api_client.create_transfer(result)
                if created:
                    self.update_status("Transfer created successfully!")
                    self.action_refresh()
                else:
                    self.update_status("Failed to create transfer")

        self.push_screen(CreateTransferScreen(self.accounts), handle_result)

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
