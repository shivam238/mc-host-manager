import sys
sys.path.append("/home/darkeeidea/Shared/Kali/MC Hosting Manager/MC_HOSTER")
from utils.config import load_config
from utils.matchmaker import check_signal
cfg = load_config()
fb = cfg.get("firebase_url")
sid = cfg.get("server_id")
print("Signal:", check_signal(fb, sid))
