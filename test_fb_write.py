import sys
sys.path.append("/home/darkeeidea/Shared/Kali/MC Hosting Manager/MC_HOSTER")
from utils.config import load_config
from utils.matchmaker import acquire_lock
cfg = load_config()
fb = cfg.get("firebase_url")
sid = cfg.get("server_id")
print("Write test:", acquire_lock(fb, sid, {"test": 123, "node_id": "test_script"}))
