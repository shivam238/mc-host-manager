import sys
sys.path.append("/home/darkeeidea/Shared/Kali/MC Hosting Manager/MC_HOSTER")
from utils.config import load_config
from utils.matchmaker import get_lock_data
cfg = load_config()
fb = cfg.get("firebase_url")
sid = cfg.get("server_id")
print("Lock:", get_lock_data(fb, sid))
