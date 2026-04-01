# AI Voice Interview System

Full-stack AI Voice Interview System with:

- Domain Based Interview
- Resume Based Interview
- Company Based Interview
- AI evaluation mode
- Keyword-based company evaluation mode
- React + Vite + Tailwind frontend
- FastAPI + modular Python backend
- MongoDB persistence with in-memory fallback for local development
- PDF final report export

## Folder Structure

```text
backend/
  app/
    config.py
    dependencies.py
    main.py
    models.py
    routers/
      health.py
      interviews.py
      metadata.py
database/
  mongo.py
modules/
  ai_evaluator.py
  keyword_matcher.py
  question_extractor.py
  report_generator.py
  resume_parser.py
  voice_handler.py
frontend/
  src/
    components/
    hooks/
    lib/
    pages/
```

## Backend API Routes

- `GET /api/health`
- `GET /api/metadata/domains`
- `POST /api/interviews/domain/start`
- `POST /api/interviews/resume/start`
- `POST /api/interviews/company/start`
- `GET /api/interviews/{session_id}`
- `POST /api/interviews/{session_id}/answer`
- `GET /api/interviews/{session_id}/report`
- `GET /api/interviews/{session_id}/report/pdf`

## MongoDB Collections

- `Users`
- `Questions`
- `CompanyQuestions`
- `InterviewResults`
- `InterviewSessions`

Each answer record in `InterviewResults` stores:

- `candidate_name`
- `question`
- `user_answer`
- `score`
- `feedback`
- `improvement_suggestion`
- `timestamp`

## Local Setup

### 1. Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` in the project root from `.env.example`.

Start MongoDB locally, or use memory fallback:

```powershell
$env:USE_IN_MEMORY_DB="true"
```

Run backend:

```powershell
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

### 2. Frontend

```powershell
cd frontend
npm install
npm run dev
```

Frontend URL:

```text
http://localhost:5173
```

Backend URL:

```text
http://127.0.0.1:8000
```

## Supported Files

- Resume upload: `PDF`
- Company uploads: `PDF`, `Excel`, `CSV`, `TXT`

## Voice System

The frontend uses browser-native:

- `SpeechSynthesis` for AI voice output
- `SpeechRecognition` / `webkitSpeechRecognition` for microphone input

For best results use latest Chrome or Edge.

## Notes

- If `GEMINI_API_KEY` is configured, the backend tries semantic LLM evaluation.
- Without an API key, the system uses a structured heuristic evaluator so the app still runs locally.
- Company keyword mode calculates score by keyword coverage percentage.
