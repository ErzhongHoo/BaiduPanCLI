#!/usr/bin/env python3
"""
百度网盘数据集拉取命令行工具

基于百度网盘开放平台 API 实现，支持：
- OAuth2.0 授权（设备码模式 / 授权码模式）
- 浏览网盘文件列表
- 搜索文件
- 下载文件/文件夹（支持断点续传）

使用前请先执行 `python baidupan.py auth` 完成授权。
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

import requests
from dotenv import load_dotenv

# 加载 .env 文件（优先从脚本所在目录加载）
load_dotenv(Path(__file__).parent / ".env")

# ============================================================
# 配置（从环境变量读取）
# ============================================================

APP_KEY = os.environ.get("APP_KEY", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
SIGN_KEY = os.environ.get("SIGN_KEY", "")

CONFIG_DIR = Path.home() / ".baidupan-cli"
TOKEN_FILE = CONFIG_DIR / "token.json"

BASE_URL = "https://pan.baidu.com"
AUTH_URL = "https://openapi.baidu.com/oauth/2.0"
UPLOAD_URL = "https://d.pcs.baidu.com/rest/2.0/pcs/superfile2"

USER_AGENT = "pan.baidu.com"
UPLOAD_BLOCK_SIZE = 4 * 1024 * 1024
UPLOAD_RETRIES = 5

# ============================================================
# Token 管理
# ============================================================


def require_app_config():
    """仅在需要访问开放平台时检查应用密钥。"""
    if not APP_KEY or not SECRET_KEY:
        print("[✗] 未配置 APP_KEY 或 SECRET_KEY，请检查 .env 文件")
        print(f"    配置文件位置: {Path(__file__).parent / '.env'}")
        sys.exit(1)


def save_token(token_data: dict):
    """保存 token 到本地文件"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    token_data["saved_at"] = int(time.time())
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2, ensure_ascii=False)
    print(f"[✓] Token 已保存到 {TOKEN_FILE}")


def load_token() -> dict:
    """加载本地 token"""
    if not TOKEN_FILE.exists():
        print("[✗] 未找到授权信息，请先执行: python baidupan.py auth")
        sys.exit(1)
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_access_token() -> str:
    """获取有效的 access_token，过期则自动刷新"""
    require_app_config()
    token_data = load_token()
    saved_at = token_data.get("saved_at", 0)
    expires_in = token_data.get("expires_in", 0)

    # 提前 5 分钟刷新
    if time.time() - saved_at > expires_in - 300:
        print("[i] Access token 已过期，正在刷新...")
        token_data = refresh_token(token_data["refresh_token"])

    return token_data["access_token"]


def refresh_token(refresh_tok: str) -> dict:
    """使用 refresh_token 刷新 access_token"""
    params = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_tok,
        "client_id": APP_KEY,
        "client_secret": SECRET_KEY,
    }
    resp = requests.get(f"{AUTH_URL}/token", params=params)
    data = resp.json()
    if "access_token" not in data:
        print(f"[✗] 刷新 token 失败: {data}")
        sys.exit(1)
    save_token(data)
    return data


# ============================================================
# 授权
# ============================================================


def cmd_auth(args):
    """执行 OAuth2.0 授权流程"""
    require_app_config()
    # 使用授权码模式，redirect_uri=oob（无 server 场景）
    auth_params = {
        "response_type": "code",
        "client_id": APP_KEY,
        "redirect_uri": "oob",
        "scope": "basic,netdisk",
        "display": "page",
    }
    auth_url = f"{AUTH_URL}/authorize?" + urllib.parse.urlencode(auth_params)

    print("=" * 60)
    print("百度网盘授权")
    print("=" * 60)
    print()
    print("请复制以下链接到浏览器中打开，完成登录并授权：")
    print()
    print(auth_url)
    print()
    print("授权成功后，页面会显示一个授权码（code），")
    print("请将其复制粘贴到下方：")
    print()
    code = input("授权码 (code): ").strip()

    if not code:
        print("[✗] 授权码不能为空")
        sys.exit(1)

    # 用 code 换取 access_token
    token_params = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": APP_KEY,
        "client_secret": SECRET_KEY,
        "redirect_uri": "oob",
    }
    resp = requests.get(f"{AUTH_URL}/token", params=token_params)
    data = resp.json()

    if "access_token" not in data:
        print(f"[✗] 获取 token 失败: {data}")
        sys.exit(1)

    save_token(data)
    print("[✓] 授权成功！")
    print(f"    Access Token 有效期: {data['expires_in'] // 86400} 天")
    print(f"    Refresh Token 有效期: 10 年")


# ============================================================
# 文件列表
# ============================================================


def api_list_files(access_token: str, dir_path: str = "/", start: int = 0,
                   limit: int = 1000, order: str = "name", desc: int = 0) -> dict:
    """获取文件列表"""
    params = {
        "method": "list",
        "access_token": access_token,
        "dir": dir_path,
        "order": order,
        "start": start,
        "limit": limit,
        "desc": desc,
        "web": 1,
        "folder": 0,
    }
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(f"{BASE_URL}/rest/2.0/xpan/file", params=params, headers=headers)
    return resp.json()


def cmd_ls(args):
    """列出文件"""
    access_token = get_access_token()
    dir_path = args.path or "/"

    result = api_list_files(access_token, dir_path)

    if result.get("errno", -1) != 0:
        print(f"[✗] 获取文件列表失败: errno={result.get('errno')}")
        if result.get("errno") == -9:
            print("    目录不存在")
        return

    file_list = result.get("list", [])
    if not file_list:
        print(f"[i] 目录 {dir_path} 为空")
        return

    print(f"\n{'类型':<4} {'大小':>12} {'修改时间':<20} {'文件名'}")
    print("-" * 70)

    for item in file_list:
        is_dir = item.get("isdir", 0)
        size = item.get("size", 0)
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item.get("server_mtime", 0)))
        name = item.get("server_filename", "")

        type_str = "📁" if is_dir else "📄"
        size_str = format_size(size) if not is_dir else "-"

        print(f"{type_str:<4} {size_str:>12} {mtime:<20} {name}")

    print(f"\n共 {len(file_list)} 个项目")


# ============================================================
# 搜索文件
# ============================================================


def api_search(access_token: str, key: str, dir_path: str = "/", recursion: int = 1) -> dict:
    """搜索文件"""
    params = {
        "method": "search",
        "access_token": access_token,
        "key": key,
        "dir": dir_path,
        "recursion": recursion,
        "web": 1,
    }
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(f"{BASE_URL}/rest/2.0/xpan/file", params=params, headers=headers)
    return resp.json()


# ============================================================
# 上传
# ============================================================


def normalize_remote_path(path: str) -> str:
    """规范化网盘绝对路径，保留 /apps/<应用名> 约束。"""
    value = "/" + str(path).strip().lstrip("/")
    while "//" in value:
        value = value.replace("//", "/")
    return value.rstrip("/") or "/"


def api_create_directory(access_token: str, path: str) -> dict:
    params = {"method": "create", "access_token": access_token}
    data = {
        "path": normalize_remote_path(path),
        "size": 0,
        "isdir": 1,
        "block_list": "[]",
        "rtype": 3,
    }
    response = requests.post(
        f"{BASE_URL}/rest/2.0/xpan/file",
        params=params,
        data=data,
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    return response.json()


def ensure_remote_directory(access_token: str, path: str) -> None:
    """逐级创建目录；已存在目录由 rtype=3 安全覆盖。"""
    normalized = normalize_remote_path(path)
    if normalized == "/":
        return
    parts = normalized.strip("/").split("/")
    current = ""
    if parts and parts[0].lower() == "apps":
        # /apps 是开放平台保留根目录，应用无权重复创建。
        current = "/apps"
        parts = parts[1:]
    for part in parts:
        current += "/" + part
        result = api_create_directory(access_token, current)
        if result.get("errno", -1) not in (0, -8):
            raise RuntimeError(
                f"创建目录失败: {current}, errno={result.get('errno')}, "
                f"response={result}"
            )


def file_block_md5(path: Path, block_size: int = UPLOAD_BLOCK_SIZE) -> list[str]:
    """按百度网盘上传协议计算每个分片的 MD5。"""
    digests = []
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digests.append(hashlib.md5(block).hexdigest())
    return digests or [hashlib.md5(b"").hexdigest()]


def api_precreate(
    access_token: str,
    remote_path: str,
    size: int,
    block_list: list[str],
) -> dict:
    params = {"method": "precreate", "access_token": access_token}
    data = {
        "path": normalize_remote_path(remote_path),
        "size": size,
        "isdir": 0,
        "autoinit": 1,
        "rtype": 3,
        "block_list": json.dumps(block_list),
    }
    response = requests.post(
        f"{BASE_URL}/rest/2.0/xpan/file",
        params=params,
        data=data,
        headers={"User-Agent": USER_AGENT},
        timeout=120,
    )
    return response.json()


def api_upload_block(
    access_token: str,
    remote_path: str,
    upload_id: str,
    part_sequence: int,
    block: bytes,
) -> dict:
    params = {
        "method": "upload",
        "access_token": access_token,
        "type": "tmpfile",
        "path": normalize_remote_path(remote_path),
        "uploadid": upload_id,
        "partseq": part_sequence,
    }
    response = requests.post(
        UPLOAD_URL,
        params=params,
        files={"file": ("blob", block, "application/octet-stream")},
        headers={"User-Agent": USER_AGENT},
        timeout=300,
    )
    return response.json()


def api_create_file(
    access_token: str,
    remote_path: str,
    size: int,
    upload_id: str,
    block_list: list[str],
) -> dict:
    params = {"method": "create", "access_token": access_token}
    data = {
        "path": normalize_remote_path(remote_path),
        "size": size,
        "isdir": 0,
        "uploadid": upload_id,
        "block_list": json.dumps(block_list),
        "rtype": 3,
    }
    response = requests.post(
        f"{BASE_URL}/rest/2.0/xpan/file",
        params=params,
        data=data,
        headers={"User-Agent": USER_AGENT},
        timeout=120,
    )
    return response.json()


def remote_file_in_parent(access_token: str, remote_path: str) -> dict | None:
    normalized = normalize_remote_path(remote_path)
    parent = os.path.dirname(normalized) or "/"
    start = 0
    while True:
        result = api_list_files(access_token, parent, start=start, limit=1000)
        if result.get("errno", -1) != 0:
            return None
        entries = result.get("list", [])
        for item in entries:
            if normalize_remote_path(item.get("path", "")) == normalized:
                return item
        if len(entries) < 1000:
            return None
        start += len(entries)


def upload_file(
    access_token: str,
    local_path: Path,
    remote_path: str,
    *,
    overwrite: bool = False,
) -> bool:
    local_path = local_path.resolve()
    remote_path = normalize_remote_path(remote_path)
    size = local_path.stat().st_size
    existing = remote_file_in_parent(access_token, remote_path)
    if existing and existing.get("size") == size and not overwrite:
        print(f"[跳过] 远端已有同尺寸文件: {remote_path}")
        return True

    ensure_remote_directory(access_token, os.path.dirname(remote_path))
    print(f"[i] 计算分片校验: {local_path.name} ({format_size(size)})")
    block_list = file_block_md5(local_path)
    if len(block_list) > 1024:
        raise ValueError(
            f"文件需要 {len(block_list)} 个4 MiB分片，超过接口上限1024"
        )

    precreate = api_precreate(access_token, remote_path, size, block_list)
    if precreate.get("errno", -1) != 0:
        raise RuntimeError(f"预上传失败: {remote_path}, response={precreate}")

    upload_id = precreate.get("uploadid")
    requested = precreate.get("block_list", list(range(len(block_list))))
    if upload_id and requested:
        requested = [int(value) for value in requested]
        started = time.time()
        with local_path.open("rb") as handle:
            for done, part_sequence in enumerate(requested, 1):
                handle.seek(part_sequence * UPLOAD_BLOCK_SIZE)
                block = handle.read(UPLOAD_BLOCK_SIZE)
                result = None
                for attempt in range(1, UPLOAD_RETRIES + 1):
                    try:
                        result = api_upload_block(
                            access_token,
                            remote_path,
                            upload_id,
                            part_sequence,
                            block,
                        )
                    except requests.RequestException as exc:
                        result = {"error": str(exc)}
                    if result.get("md5") or result.get("errno", 0) == 0:
                        break
                    if attempt < UPLOAD_RETRIES:
                        time.sleep(min(2 ** attempt, 16))
                if not result or (
                    not result.get("md5") and result.get("errno", 0) != 0
                ):
                    raise RuntimeError(
                        f"分片上传失败: part={part_sequence}, response={result}"
                    )
                uploaded = min((part_sequence + 1) * UPLOAD_BLOCK_SIZE, size)
                elapsed = max(time.time() - started, 1e-6)
                speed = uploaded / elapsed
                percent = uploaded / max(size, 1) * 100
                print(
                    f"\r    {percent:6.2f}% {format_size(uploaded)}/"
                    f"{format_size(size)} {format_size(int(speed))}/s "
                    f"({done}/{len(requested)} blocks)",
                    end="",
                    flush=True,
                )
        print()

    if not upload_id:
        # 秒传成功时部分接口版本不返回 uploadid，远端文件已创建。
        existing = remote_file_in_parent(access_token, remote_path)
        if existing and existing.get("size") == size:
            print(f"[✓] 秒传完成: {remote_path}")
            return True
        raise RuntimeError(f"预上传未返回 uploadid: {precreate}")

    created = api_create_file(
        access_token,
        remote_path,
        size,
        upload_id,
        block_list,
    )
    if created.get("errno", -1) != 0:
        raise RuntimeError(f"创建远端文件失败: {remote_path}, response={created}")
    print(f"[✓] 上传完成: {remote_path}")
    return True


def cmd_mkdir(args):
    access_token = get_access_token()
    ensure_remote_directory(access_token, args.path)
    print(f"[✓] 目录已就绪: {normalize_remote_path(args.path)}")


def cmd_upload(args):
    access_token = get_access_token()
    local_path = Path(args.local)
    if not local_path.exists():
        raise FileNotFoundError(f"本地路径不存在: {local_path}")

    remote_text = str(args.remote)
    remote = normalize_remote_path(remote_text)
    if local_path.is_file():
        target = remote
        if remote_text.endswith("/") or args.into_directory:
            target = normalize_remote_path(remote + "/" + local_path.name)
        upload_file(
            access_token,
            local_path,
            target,
            overwrite=args.overwrite,
        )
        return

    root_remote = normalize_remote_path(remote + "/" + local_path.name)
    files = sorted(path for path in local_path.rglob("*") if path.is_file())
    total_size = sum(path.stat().st_size for path in files)
    print(f"[i] 递归上传 {len(files)} 个文件，共 {format_size(total_size)}")
    ensure_remote_directory(access_token, root_remote)
    for index, path in enumerate(files, 1):
        relative = path.relative_to(local_path).as_posix()
        print(f"\n[{index}/{len(files)}] {relative}")
        upload_file(
            access_token,
            path,
            root_remote + "/" + relative,
            overwrite=args.overwrite,
        )


def cmd_search(args):
    """搜索文件"""
    access_token = get_access_token()
    keyword = args.keyword
    dir_path = args.path or "/"

    result = api_search(access_token, keyword, dir_path)

    if result.get("errno", -1) != 0:
        print(f"[✗] 搜索失败: errno={result.get('errno')}")
        return

    file_list = result.get("list", [])
    if not file_list:
        print(f"[i] 未找到匹配 '{keyword}' 的文件")
        return

    print(f"\n搜索 '{keyword}' 的结果：")
    print(f"{'类型':<4} {'大小':>12} {'路径'}")
    print("-" * 70)

    for item in file_list:
        is_dir = item.get("isdir", 0)
        size = item.get("size", 0)
        path = item.get("path", "")

        type_str = "📁" if is_dir else "📄"
        size_str = format_size(size) if not is_dir else "-"

        print(f"{type_str:<4} {size_str:>12} {path}")

    print(f"\n共 {len(file_list)} 个结果")


# ============================================================
# 查询文件信息（获取 dlink）
# ============================================================


def api_file_metas(access_token: str, fsids: list) -> dict:
    """查询文件信息，获取下载地址 dlink"""
    params = {
        "method": "filemetas",
        "access_token": access_token,
        "fsids": json.dumps(fsids),
        "dlink": 1,
    }
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(f"{BASE_URL}/rest/2.0/xpan/multimedia", params=params, headers=headers)
    return resp.json()


# ============================================================
# 下载
# ============================================================


def download_file(dlink: str, access_token: str, save_path: str, file_size: int = 0):
    """下载单个文件，支持断点续传"""
    headers = {"User-Agent": USER_AGENT}

    # 检查是否有部分下载的文件
    temp_path = save_path + ".downloading"
    downloaded = 0

    if os.path.exists(temp_path):
        downloaded = os.path.getsize(temp_path)
        if file_size > 0 and downloaded >= file_size:
            # 已下载完成
            os.rename(temp_path, save_path)
            return
        headers["Range"] = f"bytes={downloaded}-"
        print(f"    [续传] 从 {format_size(downloaded)} 处继续下载")

    url = f"{dlink}&access_token={access_token}"

    resp = requests.get(url, headers=headers, stream=True, allow_redirects=True)

    if resp.status_code not in (200, 206):
        print(f"    [✗] 下载失败: HTTP {resp.status_code}")
        return False

    total = file_size if file_size > 0 else int(resp.headers.get("content-length", 0)) + downloaded

    # 确保目录存在
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

    mode = "ab" if downloaded > 0 else "wb"
    start_time = time.time()
    last_downloaded = downloaded

    with open(temp_path, mode) as f:
        chunk_size = 1024 * 1024  # 1MB
        for chunk in resp.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)

                elapsed = time.time() - start_time
                if elapsed > 0:
                    speed = (downloaded - last_downloaded) / elapsed
                    speed_str = f"{format_size(int(speed))}/s"
                else:
                    speed_str = "-- B/s"

                if total > 0:
                    percent = downloaded / total * 100
                    remaining = total - downloaded
                    if speed > 0:
                        eta_sec = int(remaining / speed)
                        eta_str = format_eta(eta_sec)
                    else:
                        eta_str = "--:--"
                    bar = "█" * int(percent // 2) + "░" * (50 - int(percent // 2))
                    print(f"\r    [{bar}] {percent:.1f}% {format_size(downloaded)}/{format_size(total)} | {speed_str} | ETA {eta_str}  ", end="")
                else:
                    print(f"\r    已下载: {format_size(downloaded)} | {speed_str}  ", end="")

    print()  # 换行

    # 下载完成，重命名
    os.rename(temp_path, save_path)
    return True


def _print_overall_progress(downloaded: int, total: int, start_time: float,
                            file_idx: int, file_count: int, current_file: str):
    """打印整体下载进度（单行刷新）"""
    elapsed = time.time() - start_time
    if elapsed > 0 and downloaded > 0:
        speed = downloaded / elapsed
        speed_str = f"{format_size(int(speed))}/s"
        remaining = total - downloaded
        eta_str = format_eta(int(remaining / speed)) if speed > 0 else "--:--"
    else:
        speed_str = "-- B/s"
        eta_str = "--:--"

    percent = downloaded / total * 100 if total > 0 else 0
    bar_len = 30
    bar = "█" * int(percent / 100 * bar_len) + "░" * (bar_len - int(percent / 100 * bar_len))

    # 截断文件名避免太长
    name = current_file if len(current_file) <= 25 else "..." + current_file[-22:]

    line = f"\r    [{bar}] {percent:.1f}% {format_size(downloaded)}/{format_size(total)} | {speed_str} | ETA {eta_str} | [{file_idx}/{file_count}] {name}"
    # 用空格覆盖上一行残留字符
    print(f"{line:<120}", end="", flush=True)


def download_file_batch(dlink: str, access_token: str, save_path: str, file_size: int,
                        base_downloaded: int, total_size: int, start_time: float,
                        file_idx: int, file_count: int, rel_path: str) -> bool:
    """下载单个文件（批量模式），进度汇入整体进度条"""
    headers = {"User-Agent": USER_AGENT}

    temp_path = save_path + ".downloading"
    file_downloaded = 0

    if os.path.exists(temp_path):
        file_downloaded = os.path.getsize(temp_path)
        if file_size > 0 and file_downloaded >= file_size:
            os.rename(temp_path, save_path)
            return True
        headers["Range"] = f"bytes={file_downloaded}-"

    url = f"{dlink}&access_token={access_token}"
    resp = requests.get(url, headers=headers, stream=True, allow_redirects=True)

    if resp.status_code not in (200, 206):
        return False

    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

    mode = "ab" if file_downloaded > 0 else "wb"
    with open(temp_path, mode) as f:
        chunk_size = 1024 * 1024
        for chunk in resp.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)
                file_downloaded += len(chunk)
                current_total = base_downloaded + file_downloaded
                _print_overall_progress(current_total, total_size, start_time, file_idx, file_count, rel_path)

    os.rename(temp_path, save_path)
    return True


def collect_files_recursive(access_token: str, dir_path: str) -> list:
    """递归收集目录下所有文件"""
    all_files = []
    result = api_list_files(access_token, dir_path)

    if result.get("errno", -1) != 0:
        print(f"[✗] 获取目录 {dir_path} 失败")
        return all_files

    for item in result.get("list", []):
        if item.get("isdir", 0):
            # 递归进入子目录
            sub_files = collect_files_recursive(access_token, item["path"])
            all_files.extend(sub_files)
        else:
            all_files.append(item)

    return all_files


def cmd_download(args):
    """下载文件或文件夹"""
    access_token = get_access_token()
    remote_path = args.path
    output_dir = args.output or "."

    print(f"[i] 正在获取文件信息: {remote_path}")

    # 先尝试把 remote_path 当目录列出
    result_as_dir = api_list_files(access_token, remote_path)
    if result_as_dir.get("errno", -1) == 0 and result_as_dir.get("list"):
        target = {"path": remote_path, "isdir": 1}
    else:
        # 当作文件处理：列出父目录，查找匹配项
        parent_dir = os.path.dirname(remote_path) or "/"
        result = api_list_files(access_token, parent_dir)

        if result.get("errno", -1) != 0:
            print(f"[✗] 获取文件信息失败: errno={result.get('errno')}")
            return

        # 查找目标（大小写不敏感匹配）
        target = None
        for item in result.get("list", []):
            if item.get("path", "").lower() == remote_path.lower():
                target = item
                break

        # 如果精确匹配失败，尝试按文件名匹配
        if target is None:
            target_name = os.path.basename(remote_path).lower()
            for item in result.get("list", []):
                if item.get("server_filename", "").lower() == target_name:
                    target = item
                    break

        if target is None:
            print(f"[✗] 未找到: {remote_path}")
            print(f"[i] 父目录 {parent_dir} 下的文件：")
            for item in result.get("list", []):
                print(f"    {item.get('path')}")
            return

    if target.get("isdir", 0):
        # 下载整个目录
        print(f"[i] 正在递归扫描目录: {remote_path}")
        files = collect_files_recursive(access_token, remote_path)

        if not files:
            print("[i] 目录为空，无文件可下载")
            return

        total_size = sum(f.get("size", 0) for f in files)
        print(f"[i] 共 {len(files)} 个文件，总大小: {format_size(total_size)}")
        print()

        # 计算已下载的字节数（跳过的文件）
        total_downloaded = 0
        start_time = time.time()
        failed = []

        for i, file_item in enumerate(files, 1):
            rel_path = file_item["path"][len(remote_path):].lstrip("/")
            save_path = os.path.join(output_dir, os.path.basename(remote_path), rel_path)

            # 如果文件已存在且大小一致，跳过
            if os.path.exists(save_path) and os.path.getsize(save_path) == file_item.get("size", -1):
                total_downloaded += file_item.get("size", 0)
                _print_overall_progress(total_downloaded, total_size, start_time, i, len(files), rel_path)
                continue

            # 获取 dlink
            fsid = file_item.get("fs_id")
            meta_result = api_file_metas(access_token, [fsid])

            if meta_result.get("errno", -1) != 0:
                failed.append(rel_path)
                continue

            meta_list = meta_result.get("list", [])
            if not meta_list or "dlink" not in meta_list[0]:
                failed.append(rel_path)
                continue

            dlink = meta_list[0]["dlink"]
            success = download_file_batch(
                dlink, access_token, save_path, file_item.get("size", 0),
                total_downloaded, total_size, start_time, i, len(files), rel_path,
            )
            if success:
                total_downloaded += file_item.get("size", 0)
            else:
                failed.append(rel_path)

        print()  # 结束进度条行
        if failed:
            print(f"\n[!] {len(failed)} 个文件下载失败：")
            for f in failed:
                print(f"    {f}")

    else:
        # 下载单个文件
        fsid = target.get("fs_id")
        filename = target.get("server_filename", os.path.basename(remote_path))
        save_path = os.path.join(output_dir, filename)

        # 如果文件已存在且大小一致，跳过
        if os.path.exists(save_path) and os.path.getsize(save_path) == target.get("size", -1):
            print(f"[✓] 文件已存在且大小一致，跳过: {save_path}")
            return

        print(f"[i] 下载: {filename} ({format_size(target.get('size', 0))})")

        # 获取 dlink
        meta_result = api_file_metas(access_token, [fsid])

        if meta_result.get("errno", -1) != 0:
            print(f"[✗] 获取下载地址失败: {meta_result}")
            return

        meta_list = meta_result.get("list", [])
        if not meta_list or "dlink" not in meta_list[0]:
            print(f"[✗] 未获取到下载地址")
            return

        dlink = meta_list[0]["dlink"]
        download_file(dlink, access_token, save_path, target.get("size", 0))

    print("\n[✓] 下载完成！")


# ============================================================
# 用户信息
# ============================================================


def cmd_info(args):
    """显示用户信息和网盘容量"""
    access_token = get_access_token()

    # 获取用户信息
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(
        f"{BASE_URL}/rest/2.0/xpan/nas",
        params={"method": "uinfo", "access_token": access_token},
        headers=headers,
    )
    user_info = resp.json()

    # 获取容量信息
    resp2 = requests.get(
        f"https://pan.baidu.com/api/quota",
        params={"access_token": access_token, "checkfree": 1, "checkexpire": 1},
        headers=headers,
    )
    quota_info = resp2.json()

    print("\n用户信息:")
    print(f"  用户名: {user_info.get('baidu_name', 'N/A')}")
    print(f"  网盘名: {user_info.get('netdisk_name', 'N/A')}")
    print(f"  VIP类型: {['普通用户', '普通会员', '超级会员'][user_info.get('vip_type', 0)]}")

    if quota_info.get("errno", -1) == 0:
        total = quota_info.get("total", 0)
        used = quota_info.get("used", 0)
        free = total - used
        print(f"\n容量信息:")
        print(f"  总容量: {format_size(total)}")
        print(f"  已使用: {format_size(used)}")
        print(f"  剩余:   {format_size(free)}")
        print(f"  使用率: {used / total * 100:.1f}%")


# ============================================================
# 工具函数
# ============================================================


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}"


def format_eta(seconds: int) -> str:
    """格式化剩余时间"""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h{m:02d}m"


# ============================================================
# 主入口
# ============================================================


def main():
    parser = argparse.ArgumentParser(
        description="百度网盘命令行上传与下载工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python baidupan.py auth                    # 首次授权
  python baidupan.py info                    # 查看用户信息
  python baidupan.py ls /                    # 列出根目录
  python baidupan.py ls /datasets            # 列出 /datasets 目录
  python baidupan.py search "train"          # 搜索文件
  python baidupan.py upload ./release /apps/MyApp/release  # 递归上传
  python baidupan.py download /datasets/mnist -o ./data   # 下载到本地
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # auth
    auth_parser = subparsers.add_parser("auth", help="授权登录百度网盘")

    # info
    info_parser = subparsers.add_parser("info", help="查看用户信息和容量")

    # ls
    ls_parser = subparsers.add_parser("ls", help="列出文件")
    ls_parser.add_argument("path", nargs="?", default="/", help="目录路径 (默认: /)")

    # search
    search_parser = subparsers.add_parser("search", help="搜索文件")
    search_parser.add_argument("keyword", help="搜索关键词")
    search_parser.add_argument("-p", "--path", default="/", help="搜索目录 (默认: /)")

    # download
    dl_parser = subparsers.add_parser("download", help="下载文件或文件夹")
    dl_parser.add_argument("path", help="网盘文件/文件夹路径")
    dl_parser.add_argument("-o", "--output", default=".", help="本地保存目录 (默认: 当前目录)")

    # mkdir
    mkdir_parser = subparsers.add_parser("mkdir", help="递归创建网盘目录")
    mkdir_parser.add_argument("path", help="网盘目录，需位于 /apps/<应用名> 下")

    # upload
    upload_parser = subparsers.add_parser("upload", help="上传文件或递归上传目录")
    upload_parser.add_argument("local", help="本地文件或目录")
    upload_parser.add_argument("remote", help="网盘目标路径")
    upload_parser.add_argument(
        "--into-directory",
        action="store_true",
        help="将单文件放入目标目录并保留本地文件名",
    )
    upload_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖远端同路径文件；默认同尺寸时跳过",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    commands = {
        "auth": cmd_auth,
        "info": cmd_info,
        "ls": cmd_ls,
        "search": cmd_search,
        "mkdir": cmd_mkdir,
        "upload": cmd_upload,
        "download": cmd_download,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
