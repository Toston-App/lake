# Contributing to Toston

Thank you for your interest in contributing to Toston! This document provides guidelines and instructions for contributing.

## Getting Started

### Prerequisites

- [uv](https://github.com/astral-sh/uv) for Python package and environment management.
- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/)

### Setup

- [Docker](https://www.docker.com/).
- [Docker Compose](https://docs.docker.com/compose/install/).
- [uv](https://github.com/astral-sh/uv) for Python package and environment management.

### Setup

1. Fork the repository
2. Clone your fork locally
3. Copy `.env.develop` to `.env`:

   ```bash
   # Unix/Linux/Mac
   cp .env.develop .env
   ```

> [!IMPORTANT]
> Do not use `.env.develop` in production

4. Start the stack with Docker Compose: `docker-compose up -d` or `docker compose up -d`

Now you can open your browser and interact with these URLs:

- JSON based web API based on OpenAPI: http://localhost:8000
- Automatic interactive documentation with Swagger UI (from the OpenAPI backend): http://localhost:8000/docs
- Alternative automatic documentation with ReDoc (from the OpenAPI backend): http://localhost:8000/redoc
- PGAdmin, PostgreSQL web administration: http://localhost:5050
- WAHA WhatsApp integration (In case you're using it): http://localhost:3000


### DB Migrations

After changing a model (for example, adding a column), inside the container, create a revision, e.g.:

```console
$ alembic revision --autogenerate -m "Add column last_name to User model"
```

- Commit to the git repository the files generated in the alembic directory.

- After creating the revision, run the migration in the database (this is what will actually change the database):

```console
$ alembic upgrade head
```

## What to Focus On

**🎯 Good Areas to Contribute:**

- Testing (there are some files related to, but they aren't used, feel free to remove them)
- Project cleanup (removing unused files, refactoring, etc.)
- Project management (add linters, formatters, etc.)
- Documentation
- Bug fixes in existing functionality
- Fix linter warnings
- Performance optimizations

If you're unsure whether your idea falls into the preview category, feel free to ask us [directly in X](https://twitter.com/@cleverbilling) or create a GitHub issue!

## How to Contribute

### Reporting Bugs

- Use the bug report template
- Include steps to reproduce
- Provide screenshots if applicable

### Suggesting Features

- Use the feature request template
- Explain the use case
- Consider implementation details

### Code Contributions

1. Create a new branch: `git checkout -b feature/your-feature-name`
2. Make your changes
3. Run the linter: `uvx ruff check --fix`
4. Format your code: `uvx ruff format`
5. Commit your changes with a descriptive message
6. Push to your fork and create a pull request

## Code Style

- We use Ruff for code formatting and linting
- Run `uvx ruff format` to format your code
- Run `uvx ruff check` to check for linting issues
- Follow the existing code patterns

## Pull Request Process

1. Fill out the pull request template completely
2. Link any related issues
3. Request review from maintainers
4. Address any feedback

## Community

- Be respectful and inclusive
- Follow our Code of Conduct
- Help others in discussions and issues

Thank you for contributing!