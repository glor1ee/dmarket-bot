import json
import sys
sys.stdout.reconfigure(encoding="utf-8")
from dmarket import get_user_offers

items = get_user_offers()
print(f"Offers: {len(items)}\n")
print(json.dumps(items, indent=2, ensure_ascii=False))
