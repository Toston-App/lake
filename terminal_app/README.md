# Toston Financial Terminal App

A modern terminal-based financial management application built with [Textual](https://textual.textualize.io/). This app provides an interactive interface to view and manage your financial data from the Toston backend.

## Features

- 📊 **Dashboard Overview**: Real-time display of total balance, income, and expenses
- 🏦 **Accounts Management**: View all your accounts with balances and types
- 💸 **Expense Tracking**: Browse and filter expenses by date
- 💵 **Income Tracking**: Monitor your income sources
- 🔄 **Transfer History**: Track money transfers between accounts
- 📅 **Date Filters**: View data by day, week, month, quarter, year, or custom range
- 🎨 **Modern UI**: Beautiful terminal interface with dark mode support
- ⚡ **Real-time Updates**: Refresh data on demand

## Prerequisites

- Python 3.10 or higher
- Running Toston backend (see main project README)
- API credentials for the backend

## Installation

1. Install dependencies:

```bash
cd terminal_app
pip install -r requirements.txt
```

2. Configure environment variables:

```bash
cp .env.example .env
```

Edit `.env` file with your API credentials:

```env
API_BASE_URL=http://localhost:8888
API_USERNAME=admin
API_PASSWORD=root
```

## Usage

Run the application:

```bash
python app.py
```

### Keyboard Shortcuts

- `q` - Quit the application
- `r` - Refresh all data
- `d` - Toggle dark/light mode
- `Tab` - Navigate between tabs
- `Arrow keys` - Navigate within tables

### Interface Sections

1. **Balance Cards** (Top): Shows total balance, total income, and total expenses
2. **Controls** (Middle): Buttons to refresh data and filter by month/year
3. **Tabs** (Bottom):
   - **Accounts**: All your financial accounts
   - **Expenses**: Transaction history of expenses
   - **Incomes**: Transaction history of incomes
   - **Transfers**: Money transfers between accounts

## API Endpoints Used

The app connects to the following Toston backend endpoints:

- `POST /api/v1/login/access-token` - Authentication
- `GET /api/v1/accounts` - Retrieve accounts
- `GET /api/v1/expenses/{filter}/{date}` - Get expenses with date filters
- `GET /api/v1/incomes/{filter}/{date}` - Get incomes with date filters
- `GET /api/v1/transfers/{filter}/{date}` - Get transfers with date filters
- `GET /api/v1/categories` - Retrieve categories
- `GET /api/v1/users/me` - Get current user info

## Architecture

- `app.py` - Main Textual application with UI components
- `api_client.py` - HTTP client for backend API communication
- `requirements.txt` - Python dependencies
- `.env.example` - Environment configuration template

## Development

The app is built using:

- **Textual**: Modern TUI framework for Python
- **httpx**: Async HTTP client for API requests
- **Rich**: Terminal formatting and styling
- **python-dotenv**: Environment variable management

## Troubleshooting

### Connection Issues

If you can't connect to the API:

1. Verify the backend is running: `docker-compose ps`
2. Check the API_BASE_URL in your `.env` file
3. Ensure credentials are correct
4. Test API manually: `curl http://localhost:8888/docs`

### Display Issues

- Ensure your terminal supports Unicode and colors
- Try toggling dark mode with `d` key
- Resize terminal for better layout

## Contributing

This app is part of the Toston project. See the main [CONTRIBUTING.md](../.github/CONTRIBUTING.md) for contribution guidelines.

## License

See [LICENSE](../LICENSE) file in the main project directory.
