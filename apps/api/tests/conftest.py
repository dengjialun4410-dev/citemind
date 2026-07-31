import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test_citemind.db"
os.environ["UPLOAD_DIR"] = "./test_uploads"
os.environ["OPENAI_API_KEY"] = ""
os.environ["EMBEDDING_PROVIDER"] = "hashing"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["DEMO_SEED_ENABLED"] = "true"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
