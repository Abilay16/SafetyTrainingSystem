import base64, time, os, re
from fastapi import HTTPException

def save_dataurl_png(data_url: str, dest_dir: str, name_prefix: str) -> str:
    m = re.match(r'^data:image/(?:png|jpeg);base64,(.+)$', data_url)
    if not m:
        raise HTTPException(400, "signature must be data:image/png;base64,...")
    raw = base64.b64decode(m.group(1))
    ext = "png"
    fname = f"{name_prefix}_{int(time.time())}.{ext}"
    os.makedirs(dest_dir, exist_ok=True)
    fpath = os.path.join(dest_dir, fname)
    with open(fpath, "wb") as f:
        f.write(raw)
    # Возвращаем относительный путь для веб-доступа (signatures/...)
    return f"signatures/{fname}"
