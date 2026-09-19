$ErrorActionPreference = "Stop"
if (Test-Path ".\.venv\Scripts\Activate.ps1") { . .\.venv\Scripts\Activate.ps1 }
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
