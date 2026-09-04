"""Task 21.33: launch the Application CRM operational dashboard.

Usage:
    python dashboard.py

Opens a local server at http://127.0.0.1:8000 reading the real production
CRM (app/data/application_history.db via OpportunityCRMService). No demo or
fixture data is used.
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.api.dashboard:app", host="127.0.0.1", port=8000)
