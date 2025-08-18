<p align="center">
  <p align="center">If you find this project useful, please consider giving it a star on GitHub! ⭐</p>
</p>

<br/>

<p align="center">
  <h1 align="center">Toston <span style="font-size: 0.7em; font-weight: normal;">(Formerly Cleverbill.ing)</span></h1>
  <h3 align="center">A free, open-source personal finance manager powered by AI</h3>
</p>

<br/>

<p align="center">
  <p align="center">Follow <a href="https://twitter.com/@cleverbilling">us on Twitter (@cleverbill.ing)</a> for updates!</p>
</p>

<br/>

## 🌟 Features

- 💬 WhatsApp integration for transaction recording
- 💸 Expense tracking
- 💵 Income tracking
- 🔄 Transfer tracking
- 📅 Date-based filtering
- 📈 Yearly, monthly, weekly and daily reports
- 📊 Advanced filtering
- 🏷️ Customizable categories and subcategories
- 📥 Import capabilities
- 📤 Export capabilities // Soon
- 🤖 AI-powered insights // soon

## 🚀 Getting Started

### Prerequisites

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

As during local development your app directory is mounted as a volume inside the container, you can also run the migrations with `alembic` commands inside the container and the migration code will be in your app directory (instead of being only inside the container). So you can add it to your git repository.

Make sure you create a "revision" of your models and that you "upgrade" your database with that revision every time you change them. As this is what will update the tables in your database. Otherwise, your application will have errors.

- Start an interactive session in the backend container:

```console
$ docker-compose exec backend bash
```

- If you created a new model in `./backend/app/app/models/`, make sure to import it in `./backend/app/app/db/base.py`, that Python module (`base.py`) that imports all the models will be used by Alembic.

- After changing a model (for example, adding a column), inside the container, create a revision, e.g.:

```console
$ alembic revision --autogenerate -m "Add column last_name to User model"
```

- Commit to the git repository the files generated in the alembic directory.

- After creating the revision, run the migration in the database (this is what will actually change the database):

```console
$ alembic upgrade head
```

## Contributing

We welcome contributions! While we're actively developing and refactoring certain areas, there are plenty of opportunities to contribute effectively.

**🎯 Focus areas:** Tests, documentation, performance, bug fixes, project cleanup.

See our [Contributing Guide](.github/CONTRIBUTING.md) for detailed setup instructions, development guidelines, and complete focus area guidance.

**Quick start for contributors:**

- Fork the repo and clone locally
- Follow the setup instructions in CONTRIBUTING.md
- Create a feature branch and submit a PR

## 🔒 Security

See [SECURITY.md](SECURITY.md) for reporting vulnerabilities.
