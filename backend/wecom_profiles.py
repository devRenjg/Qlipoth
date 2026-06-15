"""企微多机器人凭证切换 —— 物理替换默认配置目录的 bot.enc 实现 bot 切换。

背景：wecom-cli 在 Windows 只认默认配置目录 ~/.config/wecom/，不认 XDG_CONFIG_HOME。
因此切换 bot = 把对应 profile 的凭证文件物理拷进默认目录。

凭证 profile 存于 ~/.wecom-profiles/<name>/（含 bot.enc / .encryption_key / mcp_config.enc）。
配额按 bot 独立时，bot1 用尽→切 bot2 可再导一批。
"""
import os
import shutil

_HOME = os.path.expanduser("~")
_DEFAULT_DIR = os.path.join(_HOME, ".config", "wecom")
_PROFILES_DIR = os.path.join(_HOME, ".wecom-profiles")
_CRED_FILES = ["bot.enc", ".encryption_key", "mcp_config.enc"]

# 串行顺序：先 bot1，用尽再 bot2
BOTS = ["bot1", "bot2"]


def list_profiles() -> list[str]:
    """返回已配置且凭证完整的 profile 名列表（按 BOTS 顺序）。"""
    ok = []
    for name in BOTS:
        p = os.path.join(_PROFILES_DIR, name)
        if os.path.exists(os.path.join(p, "bot.enc")) and os.path.exists(os.path.join(p, ".encryption_key")):
            ok.append(name)
    return ok


def switch_to(name: str) -> bool:
    """把指定 profile 的凭证拷进默认目录，使 wecom-cli 使用该 bot。成功返回 True。"""
    src = os.path.join(_PROFILES_DIR, name)
    if not os.path.exists(os.path.join(src, "bot.enc")):
        return False
    os.makedirs(_DEFAULT_DIR, exist_ok=True)
    for f in _CRED_FILES:
        s = os.path.join(src, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(_DEFAULT_DIR, f))
    return True


def current_fingerprint() -> str:
    """默认目录 bot.enc 的指纹（md5），用于确认当前激活的是哪个 bot。"""
    import hashlib
    p = os.path.join(_DEFAULT_DIR, "bot.enc")
    if not os.path.exists(p):
        return ""
    return hashlib.md5(open(p, "rb").read()).hexdigest()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in BOTS:
        ok = switch_to(sys.argv[1])
        print(f"切换到 {sys.argv[1]}: {'成功' if ok else '失败(profile不存在)'} | 指纹 {current_fingerprint()[:8]}")
    else:
        print("已配置 profiles:", list_profiles())
        print("当前激活指纹:", current_fingerprint()[:8])
