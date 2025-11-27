# KvalitetTakst Backend

FastAPI backend for automated quality evaluation of building condition reports.

## Quick Start

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure environment:
```bash
cp .env.example .env
# Edit .env with your settings
```

3. Start the server:
```bash
python run.py
```

## API Documentation

Once running, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Environment Variables

See `.env.example` for all required variables.

## Database

The application uses SQLAlchemy ORM with PostgreSQL. Tables are automatically created on first run.

## Project Structure

- `app/main.py`: FastAPI application entry point
- `app/models.py`: SQLAlchemy database models
- `app/schemas.py`: Pydantic schemas for request/response validation
- `app/api/v1/`: API route handlers
- `app/services/`: Business logic (PDF extraction, AI analysis)
- `app/config.py`: Configuration management
- `app/database.py`: Database connection setup

