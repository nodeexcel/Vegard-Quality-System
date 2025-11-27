# KvalitetTakst

A complete web solution for automated quality evaluation of Norwegian building condition reports (tilstandsrapporter) based on Norwegian building standards (Forskrift til avhendingslova, NS 3600:2018, NS 3940:2023).

## Project Overview

KvalitetTakst allows building surveyors to upload PDF condition reports and receive automated quality evaluations including:
- Overall quality scores
- Component-by-component analysis
- Compliance checking against Norwegian standards
- Detailed findings and recommendations

## Architecture

- **Backend**: Python + FastAPI
- **Frontend**: Next.js (React) + TypeScript + Tailwind CSS
- **Database**: PostgreSQL
- **AI Analysis**: OpenAI GPT-4 API
- **PDF Processing**: pdfplumber

## Project Structure

```
.
├── backend/           # FastAPI backend application
│   ├── app/
│   │   ├── api/      # API routes
│   │   ├── models.py # Database models
│   │   ├── schemas.py # Pydantic schemas
│   │   ├── services/ # Business logic (PDF extraction, AI analysis)
│   │   └── main.py   # FastAPI application entry point
│   └── requirements.txt
├── frontend/         # Next.js frontend application
│   ├── app/          # Next.js app directory
│   └── package.json
└── README.md
```

## Setup Instructions

### Prerequisites

- Python 3.9+
- Node.js 18+
- PostgreSQL 12+
- OpenAI API key

### Backend Setup

1. Navigate to the backend directory:
```bash
cd backend
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

Required environment variables:
- `DATABASE_URL`: PostgreSQL connection string
- `OPENAI_API_KEY`: Your OpenAI API key
- `SECRET_KEY`: A secret key for the application
- `FRONTEND_URL`: Frontend URL (default: http://localhost:3000)

5. Install PostgreSQL (if not installed):
```bash
# On macOS - run the install script
cd backend
bash scripts/install_postgres.sh

# Or install manually
brew install postgresql@15
brew services start postgresql@15
```

6. Create the database:
```bash
# Simple method
createdb kvalitettakst_db

# Or using psql:
psql postgres
CREATE DATABASE kvalitettakst_db;
\q
```

7. Initialize database tables:
```bash
# Option 1: Using init_db.py (quick setup)
python init_db.py

# Option 2: Using Alembic (recommended for production)
alembic revision --autogenerate -m "Initial migration"
alembic upgrade head
```

7. Start the backend server:
```bash
python run.py
# Or: uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`
API documentation: `http://localhost:8000/docs`

### Frontend Setup

1. Navigate to the frontend directory:
```bash
cd frontend
```

2. Install dependencies:
```bash
npm install
```

3. Set up environment variables:
```bash
cp .env.local.example .env.local
# Edit .env.local with your API URL
```

Set `NEXT_PUBLIC_API_URL=http://localhost:8000`

4. Start the development server:
```bash
npm run dev
```

The frontend will be available at `http://localhost:3000`

## Usage

1. Open the frontend application in your browser
2. Upload a PDF condition report (tilstandsrapport)
3. Optionally provide report system and building year
4. Wait for AI analysis (typically 10-30 seconds)
5. Review the results page with scores, components, findings, and recommendations

## API Endpoints

### Upload Report
```
POST /api/v1/reports/upload
Content-Type: multipart/form-data

Parameters:
- file: PDF file (required)
- report_system: string (optional)
- building_year: integer (optional)
```

### Get Report
```
GET /api/v1/reports/{report_id}
```

### List Reports
```
GET /api/v1/reports?skip=0&limit=100
```

## Database Schema

### Reports Table
- `id`: Primary key
- `filename`: Original filename
- `report_system`: Optional report system identifier
- `building_year`: Optional building year
- `overall_score`: Overall quality score (0-100)
- `quality_score`: Quality score (0-100)
- `completeness_score`: Completeness score (0-100)
- `compliance_score`: Compliance score (0-100)
- `extracted_text`: Extracted text from PDF
- `ai_analysis`: JSON field with AI analysis summary and recommendations
- `uploaded_at`: Timestamp

### Components Table
- `id`: Primary key
- `report_id`: Foreign key to reports
- `component_type`: Type of component (e.g., "roof", "foundation")
- `name`: Component name
- `condition`: Condition rating
- `description`: Component description
- `score`: Component score (0-100)

### Findings Table
- `id`: Primary key
- `report_id`: Foreign key to reports
- `finding_type`: Type of finding (missing_info, non_compliance, quality_issue)
- `severity`: Severity level (low, medium, high, critical)
- `title`: Finding title
- `description`: Finding description
- `suggestion`: Improvement suggestion
- `standard_reference`: Reference to Norwegian standard

## Development

### Running Tests
```bash
cd backend
pytest
```

### Code Formatting
```bash
# Backend
black app/
isort app/

# Frontend
npm run lint
```

## Deployment

### Backend Deployment

1. Set up a PostgreSQL database on your server
2. Configure environment variables
3. Install dependencies and run migrations
4. Use a process manager like PM2 or systemd
5. Set up reverse proxy (nginx) if needed

Example with uvicorn:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Frontend Deployment

1. Build the production bundle:
```bash
npm run build
```

2. Deploy to Vercel, Netlify, or your own server:
```bash
npm start
```

## Future Enhancements (Phase 2 & 3)

- [ ] Rule engine for structural checks
- [ ] Enhanced scoring algorithms
- [ ] Admin dashboard
- [ ] User authentication (Vipps/BankID OAuth2)
- [ ] Report history and comparison
- [ ] Export functionality (PDF/Excel)
- [ ] Email notifications
- [ ] API rate limiting
- [ ] Caching for improved performance

## License

This project is proprietary software.

## Support

For issues or questions, please contact the development team.

